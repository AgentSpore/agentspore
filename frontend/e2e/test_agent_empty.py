"""Empty and zero states: agent without work and blog list; synthetic HTTP only, never a live service."""

import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import auth_form_fixtures as browser_fixtures
from auth_form_fixtures import AuthHTTP
from playwright.sync_api import expect
from test_agent_detail_states import AGENT, PAGE
from test_blog_reads import BASE, post

AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
VIEWPORTS = {"mobile": (390, 844), "desktop": (1440, 900)}
EMPTY = dict(AGENT, karma=0, projects_created=0, code_commits=0, fork_count=0)
SENTENCE = "This sentence is complete and readable for the visitor. "
LONG_POST = dict(post(0), content=SENTENCE * 3 + "A final run-on part " + "keeps going without a stop " * 12)
COPY = {
    "en": ("No public work yet", "See what other agents built"),
    "ru": ("Пока нет публичной работы", "Посмотреть, что сделали другие агенты"),
}


class EmptyHTTP(AuthHTTP):
    """Serve the zero-activity agent and one long blog post; abort router prefetches silently."""

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != self.origin or target.scheme != "http":
            return self.reject(route)
        if request.headers.get("next-router-prefetch") == "1":
            return route.abort()
        query = parse_qs(target.query, keep_blank_values=True)
        if request.method == "GET" and target.path in {PAGE, "/blog"} and not target.query:
            return route.continue_()
        if request.method == "GET" and target.path == "/api/v1" + PAGE:
            return route.fulfill(json=EMPTY)
        if request.method == "GET" and target.path.startswith("/api/v1" + PAGE + "/"):
            empty = {"github-activity": {"activities": [], "total": 0}}.get(target.path.rsplit("/", 1)[1], [])
            return route.fulfill(json=empty)
        if request.method == "GET" and target.path == "/api/v1/activity":
            return route.fulfill(json=[])
        if request.method == "GET" and target.path.startswith("/api/v1/blog/agents/"):
            return route.fulfill(json={"posts": [], "total": 0, "limit": 10, "offset": 0})
        if request.method == "GET" and target.path == BASE and query.get("limit") == ["20"]:
            return route.fulfill(json={"posts": [LONG_POST], "total": 1, "limit": 20, "offset": 0})
        return super().route(route)


class EmptyStateTests(unittest.TestCase):
    setUpClass = classmethod(browser_fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(browser_fixtures.BrowserFixture.tearDownClass.__func__)

    def open(self, path, locale, size, name):
        context = self.browser.new_context(
            viewport={"width": size[0], "height": size[1]}, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = EmptyHTTP(self.options.base_url)
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(7000)
        page.goto(self.options.base_url + path)
        expect(page.locator("#main-content h1")).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(900)
        page.screenshot(path=str(self.options.screenshots / f"{name}-{locale}-{size[0]}.png"), timeout=30000)
        return page

    def assert_axe_clean(self, page):
        page.add_script_tag(path=str(AXE))
        violations = page.evaluate(
            "axe.run('#main-content', {runOnly: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']})"
            ".then(r => r.violations.map(v => v.id + ': ' + v.nodes.length))"
        )
        self.assertEqual(violations, [])
        surprises = [r for r in self.http.unexpected if r["url"].split("?")[0] != "/api/v1/funnel/visit"]
        self.assertEqual(surprises, [])

    def test_agent_without_work(self):
        for locale, (title, link) in COPY.items():
            for label, size in VIEWPORTS.items():
                with self.subTest(locale=locale, viewport=label):
                    page = self.open(PAGE, locale, size, "agent-empty")
                    expect(page.get_by_text(title, exact=True)).to_be_visible()
                    expect(page.get_by_role("link", name=link, exact=True)).to_have_attribute("href", "/showcase")
                    tiles = page.locator(".stat-card .font-mono").first
                    self.assertEqual(page.locator(".stat-card").count(), 0, tiles)
                    self.assert_axe_clean(page)

    def test_blog_list_excerpt_and_no_reactions(self):
        for locale in COPY:
            for label, size in VIEWPORTS.items():
                with self.subTest(locale=locale, viewport=label):
                    page = self.open("/blog", locale, size, "blog-list")
                    article = page.locator("article").first
                    expect(article).to_be_visible()
                    self.assertEqual(article.get_by_role("button").count(), 0)
                    text = article.locator("p").filter(has_text="complete and readable").first.inner_text()
                    self.assertRegex(text, re.compile(r"[.!?]$"))
                    self.assert_axe_clean(page)


if __name__ == "__main__":
    EmptyStateTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
