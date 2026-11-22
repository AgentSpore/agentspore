"""Check mobile layout: focusable code blocks, one-row project tabs, one-line badges, no overflow.

python test_mobile_layout.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
Synthetic reads only; fixtures are reused from the blog, project and card suites.
"""

import unittest
from argparse import Namespace
from pathlib import Path
from urllib.parse import urlparse

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect
from test_blog_geometry import BODY, GeometryHTTP, publication
from test_blog_reads import BASE as BLOG_BASE
from test_blog_reads import POST_ID
from test_chat_ownership import PROJECT_ID
from test_project_action import LIVE, TITLE, ActionHTTP
from test_project_cards import PROJECTS

WIDTH = 390
AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
PAGES = ["/", "/projects", f"/projects/{PROJECT_ID}", "/showcase", "/blog", f"/blog/{POST_ID}"]
BUILDING = {"en": "Building", "ru": "В разработке"}
TABS = {
    "en": ["Overview", "Discussion", "Contributors", "Ownership"],
    "ru": ["Обзор", "Обсуждение", "Участники", "Владение"],
}


SECOND_BLOCK = "\n\n```python\nsecond = '" + "z" * 160 + "'\n```\n"
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]


class TwoCodeBlocksHTTP(GeometryHTTP):
    """Serve the geometry post with a second, separately scrollable code block."""

    def detail_read(self, route, target):
        if target.path == f"{BLOG_BASE}/{POST_ID}" and not target.query:
            return route.fulfill(json=dict(publication(), content=BODY + SECOND_BLOCK))
        return super().detail_read(route, target)


class MobileHTTP:
    """Route synthetic reads by area; writes and foreign origins fail closed."""

    def __init__(self, base_url):
        self.origin = urlparse(base_url).netloc
        self.blog = TwoCodeBlocksHTTP(base_url)
        self.project = type("Http", (ActionHTTP,), {"project": LIVE})(base_url)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != self.origin or request.method != "GET":
            return route.abort()
        if not target.path.startswith("/api/"):
            return route.continue_()
        if target.path.startswith(BLOG_BASE) or target.path.startswith("/api/v1/agents/"):
            return self.blog.route(route)
        if target.path.startswith(f"/api/v1/projects/{PROJECT_ID}"):
            return self.project.route(route)
        if target.path == "/api/v1/projects":
            return route.fulfill(json=PROJECTS)
        return route.fulfill(status=404, json={"detail": "Synthetic: not found"})


class MobileLayoutTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, path, locale):
        context = self.browser.new_context(
            viewport={"width": WIDTH, "height": 844},
            has_touch=True,
            is_mobile=True,
            service_workers="block",
        )
        self.addCleanup(context.close)
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.route("**/*", MobileHTTP(self.options.base_url).route)
        page = context.new_page()
        page.set_default_timeout(7000)
        page.goto(self.options.base_url + path, wait_until="domcontentloaded")
        expect(page.get_by_role("main")).to_be_visible()
        page.evaluate("async()=>{await document.fonts.ready;await new Promise(r=>setTimeout(r,800));}")
        return page

    def shot(self, page, name):
        page.screenshot(path=str(self.options.screenshots / f"{name}.png"), full_page=True)

    def test_blog_post_passes_axe_with_two_code_blocks(self):
        for locale in ("en", "ru"):
            with self.subTest(locale=locale):
                page = self.open_page(f"/blog/{POST_ID}", locale)
                page.add_script_tag(path=str(AXE))
                found = page.evaluate(
                    """async tags=>{const r=await axe.run({include:[['#main-content']]},
                    {runOnly:{type:'tag',values:tags}});
                    return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))}""",
                    AXE_TAGS,
                )
                self.assertEqual(page.locator("main pre").count(), 2)
                self.shot(page, f"post-{locale}")
                self.assertEqual(found, [])

    def test_project_tabs_one_row_with_touch_targets(self):
        for locale, names in TABS.items():
            with self.subTest(locale=locale):
                page = self.open_page(f"/projects/{PROJECT_ID}", locale)
                expect(page.get_by_role("heading", level=1, name=TITLE)).to_be_visible()
                boxes = [page.get_by_role("tab", name=n).first for n in names]
                rects = [b.bounding_box() for b in boxes]
                self.shot(page, f"tabs-{locale}")
                self.assertEqual(len({round(r["y"]) for r in rects}), 1, rects)
                self.assertTrue(all(r["height"] >= 44 for r in rects), rects)
                self.assertEqual(boxes[0].get_attribute("aria-selected"), "true")

    def test_status_badge_stays_on_one_line(self):
        for locale, label in BUILDING.items():
            with self.subTest(locale=locale):
                page = self.open_page("/projects", locale)
                info = page.evaluate(
                    """label=>{const el=[...document.querySelectorAll('main article span')]
                    .find(s=>s.textContent.trim().toLowerCase()===label.toLowerCase()&&s.children.length===0);
                    const box=el.parentElement;const r=document.createRange();
                    r.selectNodeContents(el);
                    return {lines:r.getClientRects().length,space:getComputedStyle(box).whiteSpace}}""",
                    label,
                )
                self.shot(page, f"badge-{locale}")
                self.assertEqual(info, {"lines": 1, "space": "nowrap"})

    def test_no_horizontal_overflow(self):
        for locale in ("en", "ru"):
            for path in PAGES:
                with self.subTest(locale=locale, path=path):
                    page = self.open_page(path, locale)
                    width = page.evaluate("document.documentElement.scrollWidth")
                    stem = path.strip("/").replace("/", "-")[:12] or "home"
                    self.shot(page, f"page-{stem}-{locale}")
                    self.assertLessEqual(width, WIDTH)


if __name__ == "__main__":
    MobileLayoutTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
