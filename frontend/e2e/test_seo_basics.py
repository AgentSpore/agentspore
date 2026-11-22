"""SEO basics on a local build: canonical per serving host, one h1 with ordered headings, axe name rules."""

import unittest
from pathlib import Path

import auth_form_fixtures as browser_fixtures
from playwright.sync_api import expect
from test_home_first_screen import route_home

AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
HOSTS = {"agentspore.com": "https://agentspore.com", "agentspore.ru": "https://agentspore.ru"}
HEADING_LEVELS = "[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => Number(h.tagName[1]))"


class SeoBasicsTests(unittest.TestCase):
    setUpClass = classmethod(browser_fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(browser_fixtures.BrowserFixture.tearDownClass.__func__)

    def open(self, path, locale, host=None):
        headers = {"x-forwarded-host": host} if host else {}
        context = self.browser.new_context(
            viewport={"width": 1440, "height": 900}, locale=locale,
            service_workers="block", extra_http_headers=headers,
        )
        self.addCleanup(context.close)
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.route("**/*", route_home(self.options))
        page = context.new_page()
        page.set_default_timeout(8000)
        page.goto(self.options.base_url + path)
        expect(page.locator("main h1").first).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page

    def canonical(self, page):
        return page.locator("link[rel=canonical]").evaluate_all("els => els.map(e => e.getAttribute('href'))")

    def test_canonical_is_absolute_on_the_serving_host(self):
        for path in ("/", "/showcase"):
            for host, origin in HOSTS.items():
                for locale in ("en", "ru"):
                    with self.subTest(path=path, host=host, locale=locale):
                        page = self.open(path, locale, host)
                        self.assertEqual(self.canonical(page), [origin + path.rstrip("/")])

    def test_canonical_without_host_follows_language(self):
        for path in ("/", "/showcase"):
            for locale, origin in (("en", HOSTS["agentspore.com"]), ("ru", HOSTS["agentspore.ru"])):
                with self.subTest(path=path, locale=locale):
                    self.assertEqual(self.canonical(self.open(path, locale)), [origin + path.rstrip("/")])

    def test_home_has_one_h1_and_no_skipped_levels(self):
        for locale in ("en", "ru"):
            with self.subTest(locale=locale):
                levels = self.open("/", locale).evaluate(HEADING_LEVELS)
                self.assertEqual(levels.count(1), 1)
                for before, after in zip(levels, levels[1:]):
                    self.assertLessEqual(after, before + 1, levels)

    def test_axe_name_rules_clean_on_home(self):
        for locale in ("en", "ru"):
            with self.subTest(locale=locale):
                page = self.open("/", locale)
                page.add_script_tag(path=str(AXE))
                for only in (
                    {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]},
                    {"type": "rule", "values": ["label-content-name-mismatch"]},
                ):
                    violations = page.evaluate(
                        "only => axe.run(document, {runOnly: only})"
                        ".then(r => r.violations.map(v => v.id + ': ' + v.nodes.length))",
                        only,
                    )
                    self.assertEqual(violations, [], only)


if __name__ == "__main__":
    SeoBasicsTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
