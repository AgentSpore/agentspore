"""Single-row header: height, nav content, tab order and axe, on a local build with held synthetic HTTP."""

import unittest
from pathlib import Path
from urllib.parse import urlparse

import auth_form_fixtures as browser_fixtures
from auth_form_fixtures import AuthHTTP
from playwright.sync_api import expect

AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
PATHS = ("/", "/projects", "/showcase", "/login")
NAV = {"en": ["Services", "Projects", "Agents", "Blog", "Token", "More"],
       "ru": ["Сервисы", "Проекты", "Агенты", "Блог", "Токен", "Ещё"]}
VISIBLE = "header a:visible, header button:visible"


class PageHTTP(AuthHTTP):
    """AuthHTTP that also accepts plain documents of the measured pages."""

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path in PATHS and not target.query:
            return True
        return super().accepts_page(request, target)


class HeaderNavTests(unittest.TestCase):
    """The header must stay one short row and keep its keyboard order."""

    setUpClass = classmethod(browser_fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(browser_fixtures.BrowserFixture.tearDownClass.__func__)

    def open(self, path, locale, width=1440):
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.route("**/*", PageHTTP(self.options.base_url).route)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.goto(self.options.base_url + path)
        expect(page.locator("header")).to_be_visible()
        page.evaluate("document.fonts.ready")
        return page

    def test_header_is_one_short_row(self):
        for locale in ("en", "ru"):
            for path in PATHS:
                for width in (1440, 1024):
                    with self.subTest(locale=locale, path=path, width=width):
                        page = self.open(path, locale, width)
                        self.assertLessEqual(page.locator("header").bounding_box()["height"], 72)
                        centers = page.locator(VISIBLE).evaluate_all(
                            "els => els.map(e => {const b = e.getBoundingClientRect(); return b.y + b.height / 2})"
                        )
                        self.assertLessEqual(max(centers) - min(centers), 8)

    def test_anonymous_nav_and_tab_order(self):
        for locale in ("en", "ru"):
            with self.subTest(locale=locale):
                page = self.open("/projects", locale)
                nav = page.locator("header nav:visible").first
                self.assertEqual(nav.locator("a:visible, button:visible").all_inner_texts(), NAV[locale])
                header = page.locator("header")
                self.assertEqual(header.get_by_role("link", name="Workspace").count(), 0)
                self.assertNotIn("AUTONOMOUS", header.inner_text().upper())
                visual = page.locator(VISIBLE).evaluate_all(
                    "els => els.map(e => [e.getBoundingClientRect().x, e.textContent.trim()])"
                )
                visual = [text for _, text in sorted(visual)]
                page.evaluate("document.body.focus()")
                tabbed = []
                for _ in range(40):
                    page.keyboard.press("Tab")
                    inside = page.evaluate(
                        "document.activeElement.closest('header') "
                        "? document.activeElement.textContent.trim() : null"
                    )
                    if inside is not None:
                        tabbed.append(inside)
                    elif tabbed:
                        break
                self.assertEqual(tabbed, visual)

    def test_header_axe_clean(self):
        for locale in ("en", "ru"):
            for path in PATHS:
                with self.subTest(locale=locale, path=path):
                    page = self.open(path, locale)
                    page.add_script_tag(path=str(AXE))
                    violations = page.evaluate(
                        "axe.run(document.querySelector('header'), "
                        "{runOnly: ['wcag2a','wcag2aa','wcag22aa']})"
                        ".then(r => r.violations.map(v => v.id + ': ' + v.nodes.length))"
                    )
                    self.assertEqual(violations, [])


if __name__ == "__main__":
    HeaderNavTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
