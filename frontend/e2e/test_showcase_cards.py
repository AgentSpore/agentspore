"""Showcase cards: first screen, one link per card, no repeats, stable thumbnails, axe; synthetic HTTP only.

python test_showcase_cards.py --base-url http://127.0.0.1:3917 \
    --executable /path/to/chromium --screenshots /path/to/evidence
The stand is built with NEXT_PUBLIC_API_URL set to the same origin as --base-url.
"""

import unittest
from pathlib import Path
from urllib.parse import urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from test_project_cards import PROJECTS as CARD_PROJECTS

AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
ORG = "https://github.com/SyntheticResearchToolsOrganization/"
BASE = dict(CARD_PROJECTS[0], tech_stack=["TypeScript"], deploy_url="https://showcase-fixture.invalid/app")


def project(index, title, repo, **extra):
    return dict(BASE, id=f"00000000-0000-4000-8000-0000000002{index:02d}", title=title, repo_url=ORG + repo, **extra)


PROJECTS = [
    project(1, "Synthetic duplicate otkrytka", "otkrytka", description="Greeting cards"),
    project(2, "Synthetic duplicate podmemory", "podmemory", description="Flashcards"),
    project(3, "Synthetic verdict", "verdict", description="A synthetic English description"),
    project(4, "Synthetic empty", "dawntask", description=""),
    project(5, "Synthetic russian", "vibecheck", description="Синтетическое описание на русском языке"),
]
DUPLICATES = {"Synthetic duplicate otkrytka", "Synthetic duplicate podmemory"}
FALLBACKS = {
    "en": ("The author has not added a description yet.", "Description is available in the project, in another language."),
    "ru": ("Автор пока не добавил описание.", "Описание доступно в проекте на другом языке."),
}
PROBE = """() => {
  window.__cls = 0;
  new PerformanceObserver(list => { for (const e of list.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; })
    .observe({type: 'layout-shift', buffered: true});
}"""
CARD_REACH = """() => [...document.querySelectorAll('main article')].map(card => {
  card.scrollIntoView({block: 'center'});
  const r = card.getBoundingClientRect();
  const hit = document.elementFromPoint(Math.min(r.right - 6, innerWidth - 2), Math.max(r.top + 6, 2));
  return {links: card.querySelectorAll('a[data-card-link]').length, reach: !!hit && card.contains(hit) && !!hit.closest('a[data-card-link]'),
          nested: !!card.querySelector('a a, a button, button a')};
})"""
THUMBS = "() => [...document.querySelectorAll('[data-thumb]')].map(t => { const r = t.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })"


class ShowcaseHTTP(AuthHTTP):
    """Serve the project list from a fixture; everything else follows the auth fixture rules."""

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/showcase" and not target.query:
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        target = urlparse(route.request.url)
        if route.request.method == "GET" and target.netloc == self.origin and target.path == "/api/v1/projects" and target.query == "limit=200":
            route.fulfill(json=PROJECTS)
            return
        super().route(route)


class ShowcaseCardTests(BrowserFixture, unittest.TestCase):
    def open_page(self, locale, width, height):
        context = self.browser.new_context(viewport={"width": width, "height": height}, locale=locale, service_workers="block")
        self.addCleanup(context.close)
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.add_init_script(f"({PROBE})()")
        context.route("**/*", ShowcaseHTTP(self.options.base_url).route)
        page = context.new_page()
        page.set_default_timeout(7000)
        page.goto(self.options.base_url + "/showcase")
        page.locator("main h3", has_text="Synthetic verdict").wait_for()
        page.evaluate("document.fonts.ready")
        return page

    def check_first_title_in_fold(self, page, height):
        box = page.locator("main article h3").first.bounding_box()
        self.assertGreaterEqual(box["y"], 0)
        self.assertLessEqual(box["y"] + box["height"], height)

    def check_cards(self, page):
        cards = page.evaluate(CARD_REACH)
        self.assertGreaterEqual(len(cards), 7)
        for card in cards:
            self.assertEqual(card, {"links": 1, "reach": True, "nested": False})

    def check_no_repeats(self, page):
        titles = [t.strip().lower() for t in page.locator("main h3").all_text_contents()]
        self.assertEqual(len(titles), len(set(titles)), titles)
        self.assertFalse(DUPLICATES & set(page.locator("main h3").all_text_contents()))
        sources = page.locator('main a[href^="https://github.com/AgentSpore/"]').evaluate_all("els => els.map(e => e.href)")
        self.assertFalse([h for h in sources if h.rsplit("/", 1)[-1] in ("otkrytka", "podmemory", "saascalc")], sources)

    def check_stable_thumbs(self, page, locale):
        before = page.evaluate(THUMBS)
        for width, height in before:
            self.assertAlmostEqual(width / height, 1.25, delta=0.02)
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate(THUMBS), before)
        self.assertLess(page.evaluate("window.__cls"), 0.1)
        empty, other = FALLBACKS[locale]
        text = page.locator("main").inner_text()
        self.assertTrue(empty in text and other in text)
        (self.assertNotIn if locale == "en" else self.assertIn)("Синтетическое описание на русском языке", text)

    def check_axe(self, page):
        page.add_script_tag(path=str(AXE))
        found = page.evaluate(
            "axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag22aa']}).then(r => r.violations.map(v => v.id + ': ' + v.nodes.length))"
        )
        self.assertEqual(found, [])

    def test_cards(self):
        for locale in ("en", "ru"):
            for label, (width, height) in VIEWPORTS.items():
                with self.subTest(locale=locale, viewport=label):
                    page = self.open_page(locale, width, height)
                    page.screenshot(path=str(self.options.screenshots / f"showcase-{locale}-{label}-fold.png"), timeout=30000)
                    self.check_first_title_in_fold(page, height)
                    self.check_cards(page)
                    self.check_no_repeats(page)
                    self.check_stable_thumbs(page, locale)
                    self.check_axe(page)
                    page.screenshot(path=str(self.options.screenshots / f"showcase-{locale}-{label}-full.png"), full_page=True, timeout=30000)


if __name__ == "__main__":
    ShowcaseCardTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
