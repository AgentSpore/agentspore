"""Anonymous dashboard on a local build: stats first, no sign-up banner, named or hidden glyphs, axe clean."""

import unittest
from pathlib import Path
from urllib.parse import urlparse

import auth_form_fixtures as browser_fixtures
import funnel_beacon
from playwright.sync_api import expect

AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
VIEWPORTS = {"d": (1440, 900), "m": (390, 844)}
BANNER = {"en": ("Not signed in", "your environment"), "ru": ("Вход не выполнен", "работает у вас")}
STATS = {
    "total_agents": 4, "active_agents": 4, "total_projects": 5, "total_code_commits": 360,
    "total_reviews": 12, "projects_deployed": 3, "total_repos_created": 5, "total_prs_merged": 8,
}
AGENTS = [
    {
        "id": f"00000000-0000-4000-8000-00000000000{n}", "name": f"Synthetic agent {n}",
        "handle": f"synthetic-agent-{n}", "agent_type": "external", "model_provider": "synthetic",
        "model_name": "model", "specialization": "programmer", "skills": [], "karma": 100 - n,
        "projects_created": 1, "code_commits": 1, "reviews_done": 1,
        "last_heartbeat": "2026-10-20T11:00:00Z", "is_active": False, "created_at": None,
        "dna_risk": None, "dna_speed": None, "dna_verbosity": None, "dna_creativity": None,
        "bio": None, "fork_count": 0, "is_hosted": False,
    }
    for n in range(1, 5)
]
ACTIVITY = [
    {"id": f"synthetic-{n}", "agent_id": AGENTS[0]["id"], "agent_name": "Synthetic agent 1",
     "action_type": action, "description": "Synthetic event", "metadata": {}, "ts": "2026-11-22T10:00:00Z"}
    for n, action in enumerate(("registered", "code_commit", "deploy", "code_review"))
]
API = {
    "/api/v1/agents/stats": STATS,
    "/api/v1/agents/leaderboard": AGENTS,
    "/api/v1/activity": ACTIVITY,
}
UNNAMED = (
    "el => !el.closest('[aria-hidden=true]')"
    " && !(el.getAttribute('role') === 'img' && el.getAttribute('aria-label'))"
)
GLYPH_ONLY = f"""() => {{
  const glyph = /\\p{{S}}|\\p{{Extended_Pictographic}}/u, word = /[\\p{{L}}\\p{{N}}]/u, unnamed = {UNNAMED};
  return [...document.querySelectorAll('main *, footer *')].filter(el => {{
    const text = el.textContent.trim();
    return !el.childElementCount && glyph.test(text) && !word.test(text);
  }}).filter(unnamed).map(el => el.outerHTML.slice(0, 120));
}}"""
UNNAMED_SVG = f"""() => {{
  const unnamed = {UNNAMED};
  return [...document.querySelectorAll('main svg, footer svg')].filter(unnamed)
    .map(el => el.outerHTML.slice(0, 120));
}}"""
GLYPH_COUNT = "() => document.querySelectorAll('main [aria-hidden=true], main [role=img]').length"


def route_dashboard(options):
    """Serve the local build, synthetic API reads and the funnel beacon; abort everything else."""
    origin = urlparse(options.base_url).netloc

    def handle(route):
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != origin:
            route.abort()
        elif request.method == "GET" and target.path in API:
            route.fulfill(json=API[target.path])
        elif request.method == "GET" and target.path == "/api/v1/activity/stream":
            route.fulfill(body=": ok\n\n", content_type="text/event-stream")
        elif funnel_beacon.answer(route, target):
            pass
        elif request.method == "GET":
            route.continue_()
        else:
            route.abort()

    return handle


class DashboardAnonTests(unittest.TestCase):
    setUpClass = classmethod(browser_fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(browser_fixtures.BrowserFixture.tearDownClass.__func__)

    def open(self, locale, size):
        context = self.browser.new_context(
            viewport={"width": size[0], "height": size[1]}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.route("**/*", route_dashboard(self.options))
        page = context.new_page()
        page.set_default_timeout(8000)
        page.goto(self.options.base_url + "/dashboard")
        expect(page.locator("#stats")).to_be_visible()
        expect(page.get_by_text("Synthetic agent 4")).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page

    def each_view(self):
        for locale in ("en", "ru"):
            for key, size in VIEWPORTS.items():
                with self.subTest(locale=locale, viewport=key):
                    page = self.open(locale, size)
                    page.screenshot(
                        path=str(self.options.screenshots / f"dashboard_{locale}_{key}.png"), full_page=True
                    )
                    yield locale, size, page

    def test_stats_are_the_first_content_block(self):
        for _, size, page in self.each_view():
            self.assertEqual(page.locator("main section").first.get_attribute("id"), "stats")
            self.assertLess(page.locator("#stats").bounding_box()["y"], size[1])

    def test_no_sign_up_banner(self):
        for locale, _, page in self.each_view():
            body = page.locator("body").text_content()
            for fragment in BANNER[locale]:
                self.assertNotIn(fragment, body)
            self.assertEqual(page.locator("main a[href^='/login']").count(), 0)

    def test_every_glyph_is_named_or_hidden(self):
        for _, _, page in self.each_view():
            self.assertEqual(page.evaluate(GLYPH_ONLY), [])
            self.assertEqual(page.evaluate(UNNAMED_SVG), [])
            self.assertGreater(page.evaluate(GLYPH_COUNT), 10)

    def test_axe_clean(self):
        for _, _, page in self.each_view():
            page.add_script_tag(path=str(AXE))
            violations = page.evaluate(
                "axe.run(document, {runOnly: {type: 'tag', values: "
                "['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}})"
                ".then(r => r.violations.map(v => v.id + ': ' + v.nodes.length))"
            )
            self.assertEqual(violations, [])


if __name__ == "__main__":
    DashboardAnonTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
