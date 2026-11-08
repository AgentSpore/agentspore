"""Verify private reward status with synthetic HTTP and the compiled profile page."""

import unittest
from urllib.parse import urlparse

import auth_form_fixtures as fixtures
from auth_form_fixtures import AuthHTTP
from playwright.sync_api import expect
import test_auth_forms

WELCOME = "/api/v1/users/me/rewards/welcome"
DISABLED = {"state": "disabled", "reason": "program_disabled", "amount_raw": None,
            "decimals": 6, "asset": "ASPORE", "next_action": "await_program"}
COPY = {
    "en": {"title": "Welcome reward", "send": "Send confirmation code", "code": "Confirmation code",
           "confirm": "Confirm email", "confirmed": "Email confirmed. Reward checks continue.",
           "apply": "Submit application", "disabled": "The welcome program is not active. You can keep using AgentSpore."},
    "ru": {"title": "Приветственная награда", "send": "Отправить код подтверждения", "code": "Код подтверждения",
           "confirm": "Подтвердить почту", "confirmed": "Почта подтверждена. Проверки награды продолжаются.",
           "apply": "Подать заявку", "disabled": "Программа приветственных наград пока не действует. Вы можете пользоваться AgentSpore."},
}


class WelcomeHTTP(AuthHTTP):
    """Only exact owner-scoped mock reads/writes may reach this local fixture."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.welcome, self.request_status = dict(DISABLED), 202

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        paths = {WELCOME, WELCOME + "/application", "/api/v1/auth/email-proof/request", "/api/v1/auth/email-proof/confirm"}
        if target.netloc != self.origin or target.path not in paths:
            return super().route(route)
        if target.query or request.headers.get("authorization") != "Bearer synthetic-welcome-owner":
            return self.reject(route)
        self.requests.append({"path": target.path, "method": request.method})
        if target.path == WELCOME and request.method == "GET" and not request.post_data:
            return route.fulfill(json=self.welcome)
        if request.method != "POST":
            return self.reject(route)
        if target.path.endswith("/application") and not request.post_data:
            self.welcome = {**DISABLED, "state": "application", "reason": "risk_review", "next_action": "await_review"}
            return route.fulfill(json=self.welcome)
        if target.path.endswith("/request") and not request.post_data:
            return route.fulfill(status=self.request_status, json={"message": "Synthetic result"})
        if target.path.endswith("/confirm") and request.post_data_json == {"code": "x" * 43}:
            self.welcome = {**self.welcome, "reason": "risk_review", "next_action": "await_review"}
            return route.fulfill(json={"message": "Synthetic result"})
        return self.reject(route)


class WelcomeRewardTests(unittest.TestCase):
    """Capture desktop/mobile EN/RU, keyboard actions and honest reward states."""

    setUpClass = classmethod(fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.BrowserFixture.tearDownClass.__func__)
    assert_action = test_auth_forms.AuthFormTests.assert_action

    def capture(self, page, state):
        page.evaluate("Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})))")
        test_auth_forms.AuthFormTests.capture(self, page, state)
        page.evaluate("document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 0)")
        page.screenshot(path=str(self.options.screenshots / f"{self.tag}-{state}.png"), full_page=True)

    def test_status_confirmation_and_recovery(self):
        for locale in ("en", "ru"):
            for width in (390, 1280):
                with self.subTest(locale=locale, width=width):
                    context = self.browser.new_context(viewport={"width": width, "height": 900}, service_workers="block")
                    self.addCleanup(context.close)
                    context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
                    context.add_init_script("localStorage.setItem('access_token','synthetic-welcome-owner')")
                    self.http, self.errors, self.width = WelcomeHTTP(self.options.base_url), [], width
                    self.tag = f"welcome-{locale}-{width}"
                    context.route("**/*", self.http.route)
                    page, copy = context.new_page(), COPY[locale]
                    page.on("pageerror", lambda error: self.errors.append(str(error)))
                    page.goto(self.options.base_url + "/profile")
                    section = page.get_by_role("region", name=copy["title"], exact=True)
                    expect(section.get_by_text(copy["disabled"])).to_be_visible()
                    expect(section.get_by_role("button")).to_have_count(0)
                    self.capture(page, "disabled")
                    self.http.welcome = {**DISABLED, "state": "frozen", "reason": "email_required", "amount_raw": 10000000000, "next_action": "verify_email"}
                    self.http.request_status = 503
                    page.reload()
                    section.get_by_role("button", name=copy["send"]).press("Enter")
                    expect(section.get_by_role("alert")).to_be_visible()
                    self.capture(page, "request-error")
                    self.http.request_status = 202
                    section.get_by_role("button", name=copy["send"]).press("Enter")
                    code = section.get_by_role("textbox", name=copy["code"], exact=True)
                    expect(code).to_be_visible()
                    self.capture(page, "email-form")
                    code.fill("x" * 43)
                    section.get_by_role("button", name=copy["confirm"], exact=True).press("Enter")
                    expect(section.get_by_text(copy["confirmed"])).to_be_visible()
                    expect(section.get_by_role("textbox")).to_have_count(0)
                    self.capture(page, "confirmed")
                    self.http.welcome = {**DISABLED, "state": "not_applied", "reason": "not_applied", "next_action": "none"}
                    page.reload()
                    section.get_by_role("button", name=copy["apply"]).press("Enter")
                    expect(section.get_by_role("button")).to_have_count(0)
                    self.assertEqual(self.http.welcome["state"], "application")
                    self.assertIsNone(self.http.welcome["amount_raw"])
                    self.assertEqual(self.http.unexpected, [])
                    context.close()


if __name__ == "__main__":
    WelcomeRewardTests.options, args = fixtures.parse_options()
    unittest.main(argv=[__file__, *args], verbosity=2)
