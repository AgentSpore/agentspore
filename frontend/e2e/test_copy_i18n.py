"""Copy and i18n: home title, RU showcase crumb, sentence-case headings, no platform-stats jargon, no empty heartbeat row."""

import unittest
from urllib.parse import urlparse

from playwright.sync_api import expect

import auth_form_fixtures as browser_fixtures
import funnel_beacon
from auth_form_fixtures import AuthHTTP
from test_agent_detail_states import AGENT, PAGE

TITLES = {
    "en": "AgentSpore: useful services built by AI agents",
    "ru": "AgentSpore: полезные сервисы, созданные ИИ-агентами",
}
CRUMB = {"en": "Services", "ru": "Сервисы"}
HEARTBEAT = {"en": "Last check-in:", "ru": "Последний heartbeat:"}
DOCS = ("/docs/connect", "/docs/glossary")
DOCUMENTS = {"/", "/showcase", PAGE, *DOCS}
BEATING = dict(AGENT, last_heartbeat="2026-11-22T09:00:00Z")
SILENT = dict(AGENT, last_heartbeat=None)


class CopyHTTP(AuthHTTP):
    """Serve one agent profile and fail the rest of the API closed; allow the funnel beacon."""

    agent = SILENT

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != self.origin or target.scheme != "http":
            return self.reject(route)
        if funnel_beacon.answer(route, target):
            return
        if request.headers.get("next-router-prefetch") == "1":
            return route.abort()
        if request.method == "GET" and target.path in DOCUMENTS and not target.query:
            return route.continue_()
        if request.method == "GET" and target.path == "/api/v1" + PAGE:
            return route.fulfill(json=self.agent)
        if request.method == "GET" and target.path.startswith("/api/v1" + PAGE + "/"):
            return route.fulfill(json={"activities": [], "total": 0} if target.path.endswith("github-activity") else [])
        if request.method == "GET" and target.path.startswith("/api/v1/"):
            return route.fulfill(status=503, json={})
        return super().route(route)


def contains(page, needle):
    """Substring match on the rendered text, so a trailing sr-only suffix cannot hide the phrase."""
    return needle.lower() in page.locator("body").inner_text().lower()


def shouting_headings(page):
    texts = page.locator("h1, h2, h3").all_inner_texts()
    return [t for t in texts if sum(c.isalpha() for c in t) >= 4 and t == t.upper() and t != t.lower()]


class CopyTests(unittest.TestCase):
    setUpClass = classmethod(browser_fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(browser_fixtures.BrowserFixture.tearDownClass.__func__)

    def open(self, path, locale, agent=SILENT):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
        self.addCleanup(context.close)
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = CopyHTTP(self.options.base_url)
        self.http.agent = agent
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(9000)
        page.goto(self.options.base_url + path)
        expect(page.locator("#main-content h1")).to_be_visible()
        page.evaluate("document.fonts.ready")
        self.assertEqual(self.http.unexpected, [])
        page.screenshot(path=str(self.options.screenshots / f"copy-{path.strip('/').replace('/', '-') or 'home'}-{locale}.png"))
        return page

    def test_home_title(self):
        for locale, title in TITLES.items():
            with self.subTest(locale=locale):
                page = self.open("/", locale)
                self.assertEqual(page.title(), title)
                self.assertNotIn("autonomous startup forge", page.title().lower())

    def test_showcase_crumb(self):
        for locale, crumb in CRUMB.items():
            with self.subTest(locale=locale):
                page = self.open("/showcase", locale)
                expect(page.locator("#main-content span.text-neutral-300").first).to_have_text(crumb)
                expect(page.locator("#main-content").get_by_text("showcase", exact=True)).to_have_count(0)

    def test_no_shouting_headings(self):
        for locale in TITLES:
            for path in ("/", "/showcase", *DOCS):
                with self.subTest(locale=locale, path=path):
                    self.assertEqual(shouting_headings(self.open(path, locale)), [])

    def test_no_platform_stats_jargon(self):
        for locale in TITLES:
            for path in ("/", *DOCS):
                with self.subTest(locale=locale, path=path):
                    page = self.open(path, locale)
                    self.assertFalse(contains(page, "platform stat"))
                    self.assertFalse(contains(page, "статистике платформы"))

    def test_heartbeat_row_hidden_without_value(self):
        for locale, label in HEARTBEAT.items():
            with self.subTest(locale=locale, value="none"):
                page = self.open(PAGE, locale, SILENT)
                self.assertFalse(contains(page, label))
                self.assertFalse(contains(page, "heartbeat: —"))
                self.assertFalse(contains(page, "check-in: —"))
            with self.subTest(locale=locale, value="present"):
                expect(self.open(PAGE, locale, BEATING).get_by_text(label, exact=False)).to_be_visible()

    def test_absence_check_sees_suffixed_phrase(self):
        """Mutant: a phrase followed by an sr-only suffix must still be found by the substring check."""
        page = self.open("/", "en")
        page.evaluate(
            "document.body.insertAdjacentHTML('beforeend', '<p>Platform stats<span class=\"sr-only\"> (new tab)</span></p>')"
        )
        self.assertTrue(contains(page, "platform stat"))
        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<h2>ABOUT THE PLATFORM</h2>')")
        self.assertEqual(shouting_headings(page), ["ABOUT THE PLATFORM"])


if __name__ == "__main__":
    CopyTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
