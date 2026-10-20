"""Verify existing email states against an explicit local compiled frontend."""

import json
import unittest
from urllib.parse import parse_qs, urlencode, urlparse

import test_auth_forms as auth
import auth_form_fixtures as browser_fixtures
from auth_form_fixtures import MESSAGES, AuthHTTP
from playwright.sync_api import expect

CODE = "synthetic+verification/with?characters"
EXPIRED = "Invalid or expired verification link."
SUCCESS = {
    "message": "Email verified successfully.",
    "access_token": "synthetic-access",
    "refresh_token": "synthetic-refresh",
    "token_type": "bearer",
}
NEUTRAL = {"message": "If the account exists and is unverified, a new link has been sent."}


class VerificationHTTP(AuthHTTP):
    """Add only the two source-backed verification HTTP contracts."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.verifications = []

    def accepts_page(self, request, target):
        """Allow only verification document query names and known framework prefetch."""
        if target.path != "/verify-email":
            return super().accepts_page(request, target)
        query = parse_qs(target.query, keep_blank_values=True)
        keys = {"token"}
        if request.headers.get("rsc") == "1" and request.headers.get("next-router-prefetch") == "1":
            keys.add("_rsc")
        return (
            request.method == "GET"
            and set(query) <= keys
            and all(len(value) == 1 for value in query.values())
        )

    def route(self, route):
        """Hold verification/resend requests while retaining the accepted strict origin guard."""
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != self.origin or target.scheme != "http":
            super().route(route)
            return
        if (
            request.method == "GET"
            and target.path == "/api/v1/auth/verify-email"
            and parse_qs(target.query, keep_blank_values=True) == {"token": [CODE]}
        ):
            self.verifications.append(route)
            return
        if (
            request.method == "POST"
            and target.path == "/api/v1/auth/resend-verification"
            and not target.query
            and isinstance(request.post_data_json, dict)
            and set(request.post_data_json) == {"email"}
        ):
            self.requests.append({"action": "resend-verification", "body": request.post_data_json})
            self.pending.append(route)
            return
        super().route(route)


class EmailVerificationTests(unittest.TestCase):
    """Reuse accepted DOM geometry without inheriting or rediscovering AF cases."""

    setUpClass = classmethod(auth.AuthFormTests.setUpClass.__func__)
    tearDownClass = classmethod(auth.AuthFormTests.tearDownClass.__func__)
    capture = auth.AuthFormTests.capture
    assert_action = auth.AuthFormTests.assert_action

    def open_verification(self, locale, width, mode):
        """Keep each existing outcome in a fresh synthetic browser context."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.http, self.errors = VerificationHTTP(self.options.base_url), []
        self.copy, self.width = MESSAGES[locale], width
        self.tag = f"verification-{locale}-{width}-{mode}"
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.set_default_timeout(5000)
        query = "" if mode in ("missing", "resend-http") else "?" + urlencode({"token": CODE})
        page.goto(self.options.base_url + "/verify-email" + query)
        expect(page.locator("main")).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page

    def resend(self, page, outcome):
        """Preserve an Enter failure, then continue baseline through the existing button."""
        email = page.get_by_role("textbox", name=self.copy["emailAddress"], exact=True)
        button = page.locator("main").get_by_role("button").last
        email.fill("Synthetic@Example.Invalid")
        target = email.bounding_box()
        (self.options.screenshots / f"{self.tag}-input-target.json").write_text(json.dumps(target))
        self.assertIsNotNone(target)
        self.assertGreaterEqual(target["width"], 44)
        self.assertGreaterEqual(target["height"], 44)
        self.capture(page, "resend-ready")
        with self.subTest(autocomplete=True):
            expect(email).to_have_attribute("autocomplete", "email")
        email.press("Enter")
        with self.subTest(native_enter=True):
            expect(button).to_be_disabled(timeout=500)
        if not self.http.pending:
            button.click()
        expect(button).to_be_disabled()
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual(self.http.requests[0]["body"], {"email": "synthetic@example.invalid"})
        email.press("Enter")
        self.assertEqual(len(self.http.requests), 1)
        self.capture(page, "resending")
        pending = self.http.pending.pop(0)
        if outcome == "network":
            pending.abort("failed")
        else:
            pending.fulfill(status=outcome, json=NEUTRAL)
        expect(page.get_by_test_id("state-resent")).to_be_visible()
        self.capture(page, "resent")
        expect(
            page.get_by_text(self.copy["ifTheAccountExistsAndIsUnverifiedA"], exact=True)
        ).to_be_visible()
        self.assertEqual(self.errors, [])

    def missing_flow(self, page):
        """No verification GET without token; blank email cannot issue a resend."""
        expect(page.get_by_test_id("state-missing")).to_be_visible()
        self.capture(page, "missing")
        self.assertEqual(self.http.verifications, [])
        expect(page.get_by_role("button", name=self.copy["resend"], exact=True)).to_be_disabled()
        self.resend(page, 200)

    def expired_flow(self, page):
        """Hold loading, reject using the backend's exact expired detail, then abort resend."""
        expect(page.get_by_test_id("state-verifying")).to_be_visible()
        self.capture(page, "loading")
        self.assertEqual(len(self.http.verifications), 1)
        self.http.verifications[0].fulfill(status=400, json={"detail": EXPIRED})
        expect(page.get_by_test_id("state-error")).to_be_visible()
        expect(page.get_by_test_id("error-message")).to_have_text(
            self.copy["verificationLinkInvalidOrExpired"]
        )
        self.capture(page, "expired")
        self.resend(page, "network")
        self.assertEqual(len(self.http.verifications), 1)

    def error_flow(self, page, outcome=503):
        """General and network verification errors retain the existing no-resend branch."""
        self.assertEqual(len(self.http.verifications), 1)
        request = self.http.verifications[0]
        if outcome == "network":
            request.abort("failed")
        else:
            request.fulfill(status=503, json={"detail": "Synthetic verification unavailable."})
        expect(page.get_by_test_id("state-error")).to_be_visible()
        self.capture(page, "error")
        expect(page.get_by_role("textbox")).to_have_count(0)
        expect(
            page.get_by_role("link", name=self.copy["backToLogin"], exact=True)
        ).to_have_attribute("href", "/login")

    def success_flow(self, page):
        """Keep the real two-second countdown while advancing only the controlled clock."""
        page.clock.install(time="2026-01-01T00:00:00.000Z")
        page.clock.pause_at("2026-01-01T00:01:00.000Z")
        self.assertEqual(len(self.http.verifications), 1)
        self.http.verifications[0].fulfill(json=SUCCESS)
        expect(page.get_by_test_id("state-success")).to_be_visible()
        self.capture(page, "success")
        for key in ("access_token", "refresh_token"):
            self.assertEqual(page.evaluate("key => localStorage.getItem(key)", key), SUCCESS[key])
        paragraph = page.get_by_test_id("state-success").locator("p")
        expect(paragraph).to_contain_text("2")
        expect(
            page.get_by_role("link", name=self.copy["goToProfile"], exact=True)
        ).to_have_attribute("href", "/profile")
        page.clock.run_for(1000)
        expect(paragraph).to_contain_text("1")
        page.clock.run_for(1000)
        page.wait_for_url(self.options.base_url + "/profile")
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.http.verifications), 1)

    def test_existing_states_in_six_layouts(self):
        flows = {
            "missing": self.missing_flow,
            "expired": self.expired_flow,
            "error": self.error_flow,
            "success": self.success_flow,
        }
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                for mode, flow in flows.items():
                    with self.subTest(locale=locale, width=width, mode=mode):
                        page = self.open_verification(locale, width, mode)
                        try:
                            flow(page)
                        finally:
                            page.context.close()

    def test_network_verification_and_http_resend_remain_account_neutral(self):
        for locale in ("en", "ru"):
            for mode in ("network", "resend-http"):
                with self.subTest(locale=locale, mode=mode):
                    page = self.open_verification(locale, 320, mode)
                    try:
                        if mode == "network":
                            self.error_flow(page, "network")
                        else:
                            self.resend(page, 503)
                    finally:
                        page.context.close()


if __name__ == "__main__":
    EmailVerificationTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
