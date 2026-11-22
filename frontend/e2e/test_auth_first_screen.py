"""Registration form must start inside the first screen; synthetic HTTP only, never a live service."""

import unittest
from pathlib import Path

import auth_form_fixtures as browser_fixtures
from auth_form_fixtures import AuthHTTP
from playwright.sync_api import expect

AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}


class AuthFirstScreenTests(unittest.TestCase):
    """The email field and submit must be reachable without scrolling."""

    setUpClass = classmethod(browser_fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(browser_fixtures.BrowserFixture.tearDownClass.__func__)

    def open_register(self, locale, width, height):
        context = self.browser.new_context(
            viewport={"width": width, "height": height}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        http = AuthHTTP(self.options.base_url)
        context.route("**/*", http.route)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.goto(self.options.base_url + "/login?tab=register")
        expect(page.locator("#auth-email")).to_be_visible()
        page.evaluate("document.fonts.ready")
        return page

    def test_email_inside_first_screen_and_axe_clean(self):
        for locale in ("en", "ru"):
            for label, (width, height) in VIEWPORTS.items():
                with self.subTest(locale=locale, viewport=label):
                    page = self.open_register(locale, width, height)
                    email = page.locator("#auth-email")
                    expect(email).to_be_visible()
                    box = email.bounding_box()
                    self.assertGreaterEqual(box["y"], 0)
                    self.assertLessEqual(box["y"] + box["height"], height)
                    self.assertGreaterEqual(box["x"], 0)
                    self.assertLessEqual(box["x"] + box["width"], width)
                    page.screenshot(
                        path=str(self.options.screenshots / f"register-{locale}-{label}.png"),
                        timeout=30000,
                    )
                    page.add_script_tag(path=str(AXE))
                    violations = page.evaluate(
                        "axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag22aa']})"
                        ".then(r => r.violations.map(v => v.id + ': ' + v.nodes.length))"
                    )
                    self.assertEqual(violations, [])


if __name__ == "__main__":
    AuthFirstScreenTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
