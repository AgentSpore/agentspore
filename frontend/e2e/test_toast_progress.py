"""Verify registration-confirmation toast progress in a compiled local app.

python test_toast_progress.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
"""

import json
import re
import unittest
from urllib.parse import urlparse

import auth_form_fixtures as browser_options
from auth_form_fixtures import MESSAGES
from playwright.sync_api import expect, sync_playwright


class CompiledToastTests(unittest.TestCase):
    """Catch discarded-state progress freezing under the production React Compiler."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(executable_path=cls.options.executable)
        cls.addClassCleanup(cls.browser.close)
        cls.options.screenshots.mkdir(parents=True, exist_ok=True)

    def route_request(self, route):
        """Intercept HTTP boundaries only; prevent unknown and external requests."""
        request = route.request
        target = urlparse(request.url)
        if target.netloc != urlparse(self.options.base_url).netloc:
            self.unexpected.append("external request")
            route.abort()
        elif not target.path.startswith("/api/"):
            route.continue_()
        elif request.method == "GET" and target.path == "/api/v1/oauth/providers":
            route.fulfill(json={"github": False, "google": False})
        elif request.method == "POST" and target.path == "/api/v1/auth/register":
            self.registration_requests += 1
            route.fulfill(status=202, json={"requires_verification": True})
        else:
            self.unexpected.append(f"{request.method} {target.path}")
            route.fulfill(status=503, json={"detail": "No synthetic fixture for this endpoint"})

    def open_registration(self, locale):
        """Load real providers before installing the controlled clock and RAF."""
        context = self.browser.new_context(
            viewport={"width": 390, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.unexpected, self.registration_requests = [], 0
        context.route("**/*", self.route_request)
        page = context.new_page()
        page.set_default_timeout(5000)
        response = page.goto(self.options.base_url + "/login")
        self.assertEqual(response.status, 200)
        expect(page.locator("html")).to_have_attribute("lang", locale)
        expect(page.locator("#auth-email")).to_be_visible()
        page.get_by_role("button", name=MESSAGES[locale]["register"], exact=True).click()
        expect(page.locator("#auth-name")).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        page.clock.install(time="2026-01-01T00:00:00.000Z")
        page.clock.pause_at("2026-01-01T00:01:00.000Z")
        return page

    def sample_progress(self, page, locale, expected):
        """Wait for a React frame, then measure actual rendered CSS width."""
        bar = page.locator(".toast-card [style]")
        pattern = {100: "(100|9[8-9])", 75: "7[3-7]", 25: "2[3-7]"}[expected]
        try:
            expect(bar).to_have_attribute("style", re.compile(rf"width: {pattern}(\.\d+)?%;?"))
        finally:
            metrics = bar.evaluate("""el => ({
                width: el.getBoundingClientRect().width,
                track: el.parentElement.getBoundingClientRect().width,
                computedWidth: getComputedStyle(el).width,
                inlineWidth: el.style.width, now: Date.now()
            })""")
            metrics["expectedPercent"] = expected
            metrics["actualPercent"] = metrics["width"] / metrics["track"] * 100
            tag = f"{locale}-{expected}"
            (self.options.screenshots / f"{tag}.json").write_text(json.dumps(metrics, indent=2))
            page.screenshot(path=str(self.options.screenshots / f"{tag}.png"))
        self.assertAlmostEqual(metrics["actualPercent"], expected, delta=2)

    def test_compiled_registration_toast_progress_advances_in_both_languages(self):
        for locale in ("en", "ru"):
            with self.subTest(locale=locale):
                page = self.open_registration(locale)
                try:
                    page.locator("#auth-name").fill("Synthetic owner")
                    page.locator("#auth-email").fill("synthetic@example.invalid")
                    page.locator("#auth-password").fill("synthetic-browser-password")
                    page.locator("form button[type=submit]").click()
                    expect(page.locator(".toast-card")).to_have_count(1)
                    expect(page.locator(".toast-card")).to_contain_text(
                        MESSAGES[locale]["checkYourEmailToVerifyYourAccountThen"]
                    )
                    page.evaluate(
                        "Promise.all(document.getAnimations().filter(a => "
                        "a.effect?.getComputedTiming().iterations !== Infinity)"
                        ".map(a => a.finished.catch(() => {})))"
                    )
                    self.assertEqual(self.registration_requests, 1)
                    self.assertEqual(self.unexpected, [])
                    self.sample_progress(page, locale, 100)
                    page.clock.run_for(1000)
                    self.sample_progress(page, locale, 75)
                    page.clock.run_for(2000)
                    self.sample_progress(page, locale, 25)
                    self.assertEqual(self.unexpected, [])
                finally:
                    page.context.close()


if __name__ == "__main__":
    CompiledToastTests.options, unittest_args = browser_options.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
