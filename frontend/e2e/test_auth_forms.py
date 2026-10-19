"""Exercise AF forms against an explicit local build using held synthetic HTTP."""

import json
import unittest
from urllib.parse import urlparse

import test_battles_accessibility as battles
from auth_form_fixtures import EMAIL, ERROR, MESSAGES, PASSWORD, TOKEN, AuthHTTP
from playwright.sync_api import expect
from test_project_cards import ACTION, TEXT

AUTH_ACTION = ACTION.replace("article, main", "article, main, .toast-card")
SURFACE_TEXT = "els => els.flatMap(" + TEXT + ")"


class AuthFormTests(unittest.TestCase):
    """Keep real submits, labels, redirects and independent actions measurable."""

    setUpClass = classmethod(battles.BattleAccessibilityTests.setUpClass.__func__)
    tearDownClass = classmethod(battles.BattleAccessibilityTests.tearDownClass.__func__)

    def open_form(self, path, locale, width):
        """Use a fresh anonymous context for every flow and locale/width combination."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.http = AuthHTTP(self.options.base_url)
        self.copy, self.errors = MESSAGES[locale], []
        self.tag = f"{urlparse(path).path.strip('/')}-{locale}-{width}"
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.set_default_timeout(5000)
        page.goto(self.options.base_url + path)
        expect(page.locator("main")).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page

    def capture(self, page, state):
        """Save actual DOM, focus and geometry before acceptance assertions."""
        controls = page.locator(
            "main button:visible:not([disabled]), main a:visible, .toast-card button:visible"
        )
        actions = []
        for control in controls.all():
            for _ in range(80):
                page.keyboard.press("Tab")
                if control.evaluate("el => el === document.activeElement"):
                    break
            record = control.evaluate(AUTH_ACTION)
            record["inline"] = control.evaluate(
                "el => el.tagName === 'A' && "
                "el.parentElement.tagName === 'P' && "
                "el.parentElement.innerText.trim() !== el.innerText.trim()"
            )
            if record["inline"]:
                record["inlineRects"] = control.evaluate(
                    "el => [...el.getClientRects()].map(r => {"
                    "const hit = document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);"
                    "return {x:r.x,right:r.right,viewportTop:r.top,viewportBottom:r.bottom,"
                    "hit:hit===el || el.contains(hit)};})"
                )
            actions.append(record)
        record = {
            "state": state,
            "url": page.url,
            "actions": actions,
            "contrast": "unknown: rendered backgrounds not measured",
            "text": page.locator("main, .toast-card:visible").evaluate_all(SURFACE_TEXT),
            "toasts": page.locator(".toast-card:visible").evaluate_all(
                "els => els.map(el => ({x:el.getBoundingClientRect().x,"
                "right:el.getBoundingClientRect().right}))"
            ),
            "inputs": page.locator("main input").evaluate_all(
                "els => els.map(el => "
                "({html: el.outerHTML, labels: [...el.labels].map(l => l.innerText), "
                "ariaLabel: el.getAttribute('aria-label')}))"
            ),
            "documentWidth": page.evaluate("document.documentElement.scrollWidth"),
            "viewport": page.viewport_size["width"],
            "requests": list(self.http.requests),
            "unexpected": list(self.http.unexpected),
            "pageErrors": list(self.errors),
        }
        target = self.options.screenshots / f"{self.tag}-{state}"
        target.with_suffix(".json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        page.screenshot(path=str(target.with_suffix(".png")), full_page=True)
        with self.subTest(state=state, geometry=True):
            self.assertLessEqual(record["documentWidth"], record["viewport"])
            self.assertEqual(record["unexpected"], [])
            self.assertEqual(record["pageErrors"], [])
            for toast in record["toasts"]:
                self.assertGreaterEqual(toast["x"], 0, toast)
                self.assertLessEqual(toast["right"], record["viewport"], toast)
            for action in actions:
                self.assert_action(action)
            for text in record["text"]:
                self.assertGreaterEqual(text["size"], 12, text)

    def assert_action(self, action):
        """Sentence links retain the product exception; other actions require 44 px."""
        with self.subTest(action=action["name"]):
            if not action["inline"]:
                self.assertGreaterEqual(action["width"], 44, action)
                self.assertGreaterEqual(action["height"], 44, action)
            self.assertTrue(action["focused"], action)
            self.assertTrue(action["focusVisible"], action)
            rects = action.get("inlineRects", [action])
            self.assertTrue(rects, action)
            for rect in rects:
                self.assertTrue(rect["hit"], rect)
                self.assertGreaterEqual(rect["x"], -1, rect)
                self.assertLessEqual(rect["right"], self.width + 1, rect)
                self.assertGreaterEqual(rect["viewportTop"], max(0, action["headerBottom"]) - 1)
                self.assertLessEqual(rect["viewportBottom"], action["viewportHeight"] + 1)

    def fill_field(self, page, key, value, selector):
        """Assert semantic labels independently so a RED label still preserves later states."""
        with self.subTest(label=key):
            expect(page.get_by_label(self.copy[key], exact=True)).to_have_count(1)
        page.locator(selector).fill(value)

    def submit(self, page, key, action):
        """Send a real form event and verify the expected held write."""
        count = len(self.http.requests)
        with page.expect_request(
            lambda request: (
                request.method == "POST" and urlparse(request.url).path == f"/api/v1/auth/{action}"
            )
        ):
            page.get_by_role("button", name=self.copy[key], exact=True).click()
        self.assertEqual(len(self.http.requests), count + 1)
        self.assertEqual(self.http.requests[-1]["action"], action)
        self.assertEqual(len(self.http.pending), 1)

    def reply(self, status, body):
        """Release exactly one pending synthetic response."""
        self.http.pending.pop(0).fulfill(status=status, json=body)

    def assert_redirect(self, page):
        """Assert real navigation and synthetic session persistence at the destination."""
        page.wait_for_url(self.options.base_url + "/profile")
        self.assertEqual(page.evaluate("localStorage.getItem('access_token')"), "synthetic-access")
        self.assertEqual(self.http.unexpected, [])

    def register_flow(self, page):
        """Assert registration links, then verification switches back to a cleared login."""
        self.capture(page, "registration")
        name = page.get_by_label(self.copy["name"], exact=True)
        with self.subTest(incoming_registration=True):
            expect(name).to_be_visible()
        if not name.count():
            page.get_by_role("button", name=self.copy["register"], exact=True).click()
        self.fill_field(page, "name", "Synthetic owner", "#auth-name")
        self.fill_field(page, "email", EMAIL, "#auth-email")
        self.fill_field(page, "password", PASSWORD, "#auth-password")
        page.get_by_role("button", name=self.copy["showPassword"], exact=True).click()
        self.submit(page, "createAccount", "register")
        self.assertEqual(
            self.http.requests[-1]["body"],
            {"email": EMAIL, "password": PASSWORD, "name": "Synthetic owner"},
        )
        self.reply(202, {"requires_verification": True})
        expect(
            page.get_by_text(self.copy["checkYourEmailToVerifyYourAccountThen"], exact=True)
        ).to_be_visible()
        self.capture(page, "verification-required")
        expect(page.locator("#auth-name")).to_have_count(0)
        expect(page.locator("#auth-password")).to_have_value("")
        expect(page.locator("#auth-password")).to_have_attribute("type", "password")
        self.assertIsNone(page.evaluate("localStorage.getItem('access_token')"))

    def login_flow(self, page):
        """Preserve show/hide, retry after 401, then follow only an allowed destination."""
        self.fill_field(page, "email", EMAIL, "#auth-email")
        self.fill_field(page, "password", PASSWORD, "#auth-password")
        page.get_by_role("button", name=self.copy["showPassword"], exact=True).click()
        expect(page.locator("#auth-password")).to_have_attribute("type", "text")
        page.get_by_role("button", name=self.copy["hidePassword"], exact=True).click()
        expect(page.locator("#auth-password")).to_have_attribute("type", "password")
        for provider in ("GitHub", "Google"):
            link = page.get_by_role("link", name=self.copy[f"continueWith{provider}"], exact=True)
            expect(link).to_have_attribute("href", f"/api/v1/oauth/{provider.lower()}")
        self.submit(page, "signIn", "login")
        expect(page.get_by_role("button", name=self.copy["signingIn"], exact=True)).to_be_disabled()
        self.reply(401, {"detail": ERROR})
        expect(page.locator("main").get_by_text(ERROR, exact=True)).to_be_visible()
        self.capture(page, "login-error")
        self.assertEqual(self.http.requests[-1]["body"], {"email": EMAIL, "password": PASSWORD})
        self.submit(page, "signIn", "login")
        self.reply(200, {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"})
        self.assert_redirect(page)

    def forgot_flow(self, page, outcome="success"):
        """All transport outcomes expose the same account-neutral confirmation."""
        self.capture(page, f"forgot-{outcome}-form")
        self.fill_field(page, "email", EMAIL, "main input[type=email]")
        self.submit(page, "sendResetLink", "forgot-password")
        expect(
            page.get_by_role("button", name=self.copy["processing"], exact=True)
        ).to_be_disabled()
        self.assertEqual(self.http.requests[-1]["body"], {"email": EMAIL})
        if outcome == "network":
            self.http.pending.pop(0).abort("failed")
        else:
            self.reply(503 if outcome == "http" else 200, {"detail": "Synthetic response"})
        expect(
            page.get_by_text(self.copy["ifAnAccountWithThatEmailExistsWe"], exact=True)
        ).to_be_visible()
        self.capture(page, f"forgot-{outcome}-sent")
        expect(page.locator("main form")).to_have_count(0)
        expect(
            page.get_by_role("link", name=self.copy["signInPage"], exact=True)
        ).to_have_attribute("href", "/login")

    def reset_flow(self, page):
        """Mismatch sends nothing; API rejection stays retryable; success replaces the form."""
        self.capture(page, "reset-form")
        self.fill_field(page, "newPassword", PASSWORD, "main input[type=password] >> nth=0")
        self.fill_field(
            page, "confirmPassword", PASSWORD + "different", "main input[type=password] >> nth=1"
        )
        page.get_by_role("button", name=self.copy["resetPassword"], exact=True).click()
        expect(page.get_by_text(self.copy["passwordsDoNotMatch"], exact=True)).to_be_visible()
        self.capture(page, "reset-mismatch")
        self.assertEqual(self.http.requests, [])
        page.locator("main input[type=password]").nth(1).fill(PASSWORD)
        self.submit(page, "resetPassword", "reset-password")
        self.assertEqual(self.http.requests[-1]["body"], {"token": TOKEN, "new_password": PASSWORD})
        self.reply(400, {"detail": ERROR})
        expect(page.get_by_text(ERROR, exact=True)).to_be_visible()
        self.capture(page, "reset-error")
        self.submit(page, "resetPassword", "reset-password")
        self.reply(200, {"message": "Synthetic reset complete"})
        expect(
            page.get_by_text(self.copy["yourPasswordHasBeenResetSuccessfully"], exact=True)
        ).to_be_visible()
        self.capture(page, "reset-success")
        expect(page.locator("main form")).to_have_count(0)
        expect(page.get_by_role("link", name=self.copy["signIn"], exact=True)).to_have_attribute(
            "href", "/login"
        )

    def test_matrix(self):
        for locale in ("en", "ru"):
            for self.width in (320, 390, 1280):
                for path, flow in (
                    ("/signup", self.register_flow),
                    ("/login?tab=register", self.register_flow),
                    ("/login?next=/profile", self.login_flow),
                    ("/forgot-password", self.forgot_flow),
                    (f"/reset-password?token={TOKEN}", self.reset_flow),
                ):
                    with self.subTest(locale=locale, width=self.width, path=path):
                        page = self.open_form(path, locale, self.width)
                        try:
                            flow(page)
                        finally:
                            page.context.close()

    def test_negative_transport_and_missing_reset(self):
        self.width = 320
        for locale in ("en", "ru"):
            for outcome in ("http", "network"):
                with self.subTest(locale=locale, outcome=outcome):
                    page = self.open_form("/forgot-password", locale, self.width)
                    try:
                        self.forgot_flow(page, outcome)
                    finally:
                        page.context.close()
            with self.subTest(locale=locale, missing_token=True):
                page = self.open_form("/reset-password", locale, self.width)
                self.capture(page, "missing-token")
                expect(
                    page.get_by_role("link", name=self.copy["requestNewLink"], exact=True)
                ).to_have_attribute("href", "/forgot-password")
                self.assertEqual(self.http.requests, [])
                page.context.close()

    def test_external_next_falls_back_to_profile(self):
        self.width = 390
        page = self.open_form("/login?next=//example.invalid", "en", self.width)
        self.fill_field(page, "email", EMAIL, "#auth-email")
        self.fill_field(page, "password", PASSWORD, "#auth-password")
        self.submit(page, "signIn", "login")
        self.reply(200, {"access_token": "synthetic-access"})
        self.assert_redirect(page)
        page.context.close()


if __name__ == "__main__":
    AuthFormTests.options, unittest_args = battles.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
