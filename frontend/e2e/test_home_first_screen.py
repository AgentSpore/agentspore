"""Home first screen against a local build: one named service, one primary action, axe clean."""

import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

import auth_form_fixtures as browser_fixtures
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = {"d": (1440, 900), "m": (390, 844)}
SERVICE_NAMES = {"en": "PodMemory", "ru": "PodMemory"}
PROJECT = {
    "id": "00000000-0000-4000-8000-000000000001",
    "title": "API PodMemory",
    "description": "",
    "status": "deployed",
    "repo_url": "https://github.com/AgentSpore/podmemory",
    "deploy_url": "https://podmemory.agentspore.com",
}
STATS = {
    "total_agents": 3, "active_agents": 3, "total_projects": 5, "total_code_commits": 360,
    "total_reviews": 12, "projects_deployed": 3, "total_repos_created": 5, "total_prs_merged": 8,
}
API = {
    "/api/v1/projects": [PROJECT],
    "/api/v1/agents/stats": STATS,
    "/api/v1/blog/posts": {"posts": []},
    "/api/v1/agents/leaderboard": [],
    "/api/v1/activity": [],
}
PRIMARY = """els => els.filter(el => {
  const box = el.getBoundingClientRect(), style = getComputedStyle(el);
  return box.width > 0 && box.top < innerHeight && box.bottom > 0 && style.visibility !== 'hidden'
    && style.backgroundColor === 'rgb(255, 255, 255)';
}).map(el => el.textContent.trim())"""


def route_home(options):
    """Serve the local build and synthetic API reads; abort everything else."""
    origin = urlparse(options.base_url).netloc

    def handle(route):
        request, target = route.request, urlparse(route.request.url)
        if target.netloc == origin and request.method == "GET":
            route.continue_()
        elif request.method == "GET" and target.path in API:
            route.fulfill(json=API[target.path], headers={"access-control-allow-origin": "*"})
        else:
            route.abort()

    return handle


class HomeFirstScreenTests(unittest.TestCase):
    setUpClass = classmethod(browser_fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(browser_fixtures.BrowserFixture.tearDownClass.__func__)

    def open_home(self, locale, size):
        context = self.browser.new_context(
            viewport={"width": size[0], "height": size[1]}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.route("**/*", route_home(self.options))
        page = context.new_page()
        page.set_default_timeout(8000)
        page.goto(self.options.base_url + "/")
        expect(page.locator("main article h3").first).to_be_visible()
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
                    yield locale, key, size, self.open_home(locale, size)

    def test_service_name_and_open_button_inside_the_fold(self):
        for locale, key, size, page in self.each_view():
            card = page.locator("main article").first
            name = card.locator("h3").bounding_box()
            action = card.locator('a[target="_blank"]').first.bounding_box()
            self.assertIn(SERVICE_NAMES[locale], card.locator("h3").inner_text())
            self.assertLessEqual(name["y"] + name["height"], size[1])
            self.assertLessEqual(action["y"] + action["height"], size[1])
            page.screenshot(path=str(self.options.screenshots / f"after_{locale}_{key}_fold.png"))

    def test_exactly_one_primary_cta_in_the_fold(self):
        for locale, _, _, page in self.each_view():
            primary = page.eval_on_selector_all("a, button", PRIMARY)
            self.assertEqual(primary, ["Try a service" if locale == "en" else "Попробовать сервис"])

    def test_try_a_service_scrolls_the_heading_below_the_header(self):
        for locale, _, _, page in self.each_view():
            page.get_by_role("link", name="Try a service" if locale == "en" else "Попробовать сервис").click()
            page.wait_for_timeout(900)
            heading = page.locator("h2#home-services-title").bounding_box()
            header = page.locator("header").first.bounding_box()
            self.assertGreaterEqual(heading["y"], header["y"] + header["height"])

    def test_one_connect_link_in_the_fold(self):
        for _, _, _, page in self.each_view():
            count = page.eval_on_selector_all(
                'a[href="/workspace#connect-agent"]',
                "els => els.filter(el => { const b = el.getBoundingClientRect(); return b.width > 0 && b.top < innerHeight; }).length",
            )
            self.assertEqual(count, 1)

    def test_no_terminal_and_no_em_dash_in_the_hero(self):
        for _, _, _, page in self.each_view():
            expect(page.get_by_text("platform://status")).to_have_count(0)
            self.assertNotIn("—", page.locator("main section").first.inner_text())

    def test_axe_has_no_violations(self):
        axe = ROOT / "node_modules/axe-core/axe.min.js"
        self.assertTrue(axe.exists(), "axe-core package missing")
        for _, _, _, page in self.each_view():
            page.add_script_tag(path=str(axe))
            tags = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]
            result = page.evaluate(
                "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})"
                ".then(r => r.violations.map(v => ({id: v.id, nodes: v.nodes.map(n => n.target)})))",
                tags,
            )
            self.assertEqual(result, [], json.dumps(result)[:800])


if __name__ == "__main__":
    HomeFirstScreenTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
