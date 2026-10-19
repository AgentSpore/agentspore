"""Exercise profile, payout and deposit reads through the compiled profile page."""

import unittest
from urllib.parse import urlparse

import test_auth_forms as auth
import test_battles_accessibility as battles
from auth_form_fixtures import MESSAGES, USER, AuthHTTP
from playwright.sync_api import expect

READS = {
    "profile": "/api/v1/auth/me",
    "payouts": "/api/v1/users/me/payouts",
    "deposit": "/api/v1/users/me/aspore",
}
PAYOUTS = [
    {
        "id": f"00000000-0000-4000-8000-00000000000{index}",
        "amount": 1111 + index,
        "contribution_points": 10,
        "pool_total": 100000,
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "status": status,
        "tx_signature": None,
        "created_at": "2026-02-01T00:00:00Z",
        "sent_at": None,
        "confirmed_at": None,
    }
    for index, status in enumerate(("pending", "sent", "confirmed", "failed"))
]


class ProfileHTTP(AuthHTTP):
    """Hold exact bodyless reads with synthetic owner-specific authentication."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.held = {}

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        resource = next((key for key, path in READS.items() if path == target.path), None)
        if target.netloc == self.origin and target.scheme == "http" and resource:
            owner = request.headers.get("authorization", "").removeprefix("Bearer ")
            if (
                request.method != "GET"
                or target.query
                or request.post_data
                or owner not in ("synthetic-read-a", "synthetic-read-b")
            ):
                self.reject(route)
                return
            self.requests.append({"resource": resource, "owner": owner})
            self.held[(owner, resource)] = route
            return
        super().route(route)


class ProfileReadTests(unittest.TestCase):
    """Keep recovery, independent request counts and owner changes observable."""

    setUpClass = classmethod(auth.AuthFormTests.setUpClass.__func__)
    tearDownClass = classmethod(auth.AuthFormTests.tearDownClass.__func__)
    assert_action = auth.AuthFormTests.assert_action

    def open_profile(self, locale, width, mode):
        """Use only a local app, synthetic storage and intercepted HTTP."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.add_init_script("localStorage.setItem('access_token','synthetic-read-a')")
        self.http, self.errors = ProfileHTTP(self.options.base_url), []
        self.copy, self.width = MESSAGES[locale], width
        self.tag = f"profile-reads-{locale}-{width}-{mode}"
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        with page.expect_request(
            lambda request: (
                request.method == "GET" and urlparse(request.url).path == READS["deposit"]
            )
        ):
            page.goto(self.options.base_url + "/profile")
        self.main = page.get_by_role("main")
        expect(self.main.get_by_role("status")).to_have_text(self.copy["loadingProfile"])
        page.evaluate("document.fonts.ready")
        return page

    def capture(self, page, state):
        """Reuse accepted screenshot, Tab focus, hit area and overflow measurements."""
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        auth.AuthFormTests.capture(self, page, state)

    def reply(self, resource, payload, status=200, owner="synthetic-read-a"):
        """Resolve exactly one intercepted response for the captured owner."""
        self.http.held.pop((owner, resource)).fulfill(status=status, json=payload)

    def show_owner(self, name="Synthetic Alpha", owner="synthetic-read-a"):
        """Mirror auth/me without adding an undeclared deposit balance field."""
        profile = {key: value for key, value in USER.items() if key != "aspore_balance"}
        self.reply("profile", {**profile, "name": name, "token_balance": 101}, owner=owner)
        expect(self.main.get_by_role("heading", name=name, exact=True)).to_be_visible()

    def retry(self, scope, resource):
        """Assert only the selected resource issued a new HTTP request."""
        before = list(self.http.requests)
        with scope.page.expect_request(
            lambda request: (
                request.method == "GET" and urlparse(request.url).path == READS[resource]
            )
        ):
            scope.get_by_role("alert").get_by_role("button").press("Enter")
        self.assertEqual(
            self.http.requests, before + [{"resource": resource, "owner": "synthetic-read-a"}]
        )

    def recovery_flow(self, page):
        """Check loading, HTTP failure, network failure, retry and all payout statuses."""
        self.capture(page, "profile-loading")
        self.reply("profile", {}, 503)
        expect(self.main.get_by_role("alert")).to_be_visible()
        expect(self.main.get_by_role("link", name=self.copy["signIn"], exact=True)).to_have_count(0)
        self.capture(page, "profile-error")
        self.retry(self.main, "profile")
        self.show_owner()
        deposit = self.main.get_by_role("region", name=self.copy["depositBalance"], exact=True)
        payouts = self.main.get_by_role("region", name=self.copy["catPayoutsLog"], exact=True)
        expect(deposit.get_by_role("status")).to_have_text(self.copy["loadingBalance"])
        expect(payouts.get_by_role("status")).to_have_text(self.copy["loadingPayouts"])
        expect(deposit.get_by_text("0", exact=True)).to_have_count(0)
        self.capture(page, "secondary-loading")
        self.reply("payouts", {}, 503)
        self.http.held.pop(("synthetic-read-a", "deposit")).abort("failed")
        expect(deposit.get_by_role("alert")).to_be_visible()
        expect(payouts.get_by_role("alert")).to_be_visible()
        self.capture(page, "secondary-errors")
        self.retry(payouts, "payouts")
        self.reply("payouts", PAYOUTS)
        for payout in PAYOUTS:
            expect(payouts.get_by_text(self.copy[payout["status"]], exact=True)).to_be_visible()
        expect(deposit.get_by_role("alert")).to_be_visible()
        self.retry(deposit, "deposit")
        self.reply("deposit", {"aspore_balance": 7})
        expect(deposit.get_by_text("7", exact=True)).to_be_visible()
        expect(self.main.get_by_role("heading", name="Synthetic Alpha", exact=True)).to_be_visible()
        self.capture(page, "populated")

    def owner_race(self, page):
        """Late responses cannot restore private values after owner replacement or logout."""
        self.show_owner()
        page.evaluate(
            "localStorage.setItem('access_token','synthetic-read-b'); "
            "window.dispatchEvent(new Event('storage'))"
        )
        expect(self.main.get_by_role("status")).to_have_text(self.copy["loadingProfile"])
        self.reply("payouts", PAYOUTS)
        self.reply("deposit", {"aspore_balance": 987654})
        self.show_owner("Synthetic Beta", "synthetic-read-b")
        self.reply("payouts", [], owner="synthetic-read-b")
        self.reply("deposit", {"aspore_balance": 0}, owner="synthetic-read-b")
        deposit = self.main.get_by_role("region", name=self.copy["depositBalance"], exact=True)
        expect(deposit.get_by_text("0", exact=True)).to_be_visible()
        expect(self.main.get_by_text(self.copy["noPayouts"], exact=True)).to_be_visible()
        expect(self.main.get_by_text("987,654", exact=True)).to_have_count(0)
        expect(self.main.get_by_role("heading", name="Synthetic Alpha", exact=True)).to_have_count(
            0
        )
        self.capture(page, "new-owner-empty-zero")
        page.evaluate(
            "localStorage.removeItem('access_token'); window.dispatchEvent(new Event('storage'))"
        )
        expect(self.main.get_by_role("link", name=self.copy["signIn"], exact=True)).to_be_visible()
        expect(
            self.main.get_by_role("region", name=self.copy["depositBalance"], exact=True)
        ).to_have_count(0)
        self.capture(page, "signed-out")

    def test_profile_reads_in_six_layouts(self):
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                for mode in ("recovery", "owner-race"):
                    with self.subTest(locale=locale, width=width, mode=mode):
                        page = self.open_profile(locale, width, mode)
                        try:
                            if mode == "recovery":
                                self.recovery_flow(page)
                            else:
                                self.owner_race(page)
                        finally:
                            page.context.close()


if __name__ == "__main__":
    ProfileReadTests.options, unittest_args = battles.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
