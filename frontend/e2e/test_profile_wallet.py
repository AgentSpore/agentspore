"""Exercise only the existing profile wallet widget against a local compiled app."""

import json
import unittest
from urllib.parse import urlparse

import test_auth_forms as auth
import test_battles_accessibility as battles
import test_project_cards as cards
from auth_form_fixtures import MESSAGES, USER, AuthHTTP
from playwright.sync_api import expect

ADDRESS = "11111111111111111111111111111111"
CONFLICT = "Solana wallet already connected to another account"
VALIDATION = {
    "detail": [
        {
            "type": "value_error",
            "loc": ["body", "solana_wallet"],
            "msg": "Value error, Invalid Solana address (base58, 32-44 chars)",
            "input": "invalid-wallet",
            "ctx": {"error": {}},
        }
    ]
}


class WalletHTTP(AuthHTTP):
    """Hold exact authenticated wallet writes; reuse accepted local read contracts."""

    def __init__(self, base_url, connected):
        super().__init__(base_url)
        self.user = {**USER, "solana_wallet": ADDRESS if connected else None}

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != self.origin or target.scheme != "http":
            super().route(route)
            return
        if request.method == "GET" and target.path == "/api/v1/auth/me" and not target.query:
            route.fulfill(json=self.user)
            return
        if (
            target.path == "/api/v1/users/solana-wallet"
            and not target.query
            and request.method in ("PATCH", "DELETE")
        ):
            body = request.post_data_json if request.method == "PATCH" else None
            valid = isinstance(body, dict) and set(body) == {"solana_wallet"}
            if (request.method == "PATCH" and not valid) or (
                request.method == "DELETE" and request.post_data
            ):
                self.reject(route)
                return
            self.requests.append({"method": request.method, "body": body})
            self.pending.append(route)
            return
        super().route(route)


class ProfileWalletTests(unittest.TestCase):
    """Measure the wallet widget without inheriting or rediscovering other AF cases."""

    setUpClass = classmethod(auth.AuthFormTests.setUpClass.__func__)
    tearDownClass = classmethod(auth.AuthFormTests.tearDownClass.__func__)
    assert_action = auth.AuthFormTests.assert_action

    def open_wallet(self, locale, width, mode):
        """Use a fresh owner context; never invoke a wallet provider or explorer."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.add_init_script("localStorage.setItem('access_token','synthetic-wallet-session')")
        self.http, self.errors = WalletHTTP(self.options.base_url, mode == "disconnect-network"), []
        self.copy, self.width = MESSAGES[locale], width
        self.tag = f"wallet-{locale}-{width}-{mode}"
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(self.options.base_url + "/profile")
        self.widget = (
            page.get_by_role("heading", name=self.copy["walletSolana"], exact=True)
            .locator("..")
            .locator("..")
        )
        expect(self.widget).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page

    def capture(self, page, state):
        """Persist real widget bounds, text and Tab focus before checking acceptance."""
        actions = []
        controls = self.widget.locator("input:visible,button:visible:not([disabled]),a:visible")
        for control in controls.all():
            for _ in range(80):
                page.keyboard.press("Tab")
                if control.evaluate("el => el === document.activeElement"):
                    break
            action = control.evaluate(cards.ACTION)
            action["inline"] = False
            actions.append(action)
        data = {
            "state": state,
            "actions": actions,
            "text": self.widget.evaluate(cards.TEXT),
            "viewport": self.width,
            "documentWidth": page.evaluate("document.documentElement.scrollWidth"),
            "requests": list(self.http.requests),
            "unexpected": list(self.http.unexpected),
            "pageErrors": list(self.errors),
            "contrast": "unknown: rendered backgrounds unmeasured",
        }
        path = self.options.screenshots / f"{self.tag}-{state}"
        path.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        self.widget.screenshot(path=str(path.with_suffix(".png")))
        with self.subTest(state=state, measured=True):
            self.assertLessEqual(data["documentWidth"], self.width)
            self.assertEqual(data["unexpected"], [])
            self.assertEqual(data["pageErrors"], [])
            for action in actions:
                self.assert_action(action)
            for text in data["text"]:
                self.assertGreaterEqual(text["size"], 12, text)

    def connect(self, page, address):
        """Send actual input Enter once; keep a failing native-submit baseline observable."""
        field = self.widget.get_by_role("textbox")
        with self.subTest(input_name=True):
            expect(
                self.widget.get_by_role(
                    "textbox", name=self.copy["solanaWalletAddress"], exact=True
                )
            ).to_have_count(1)
        field.fill(address)
        self.capture(page, "connect-ready-" + str(len(self.http.requests)))
        button = self.widget.get_by_role("button", name=self.copy["connect"], exact=True)
        before = len(self.http.requests)
        field.press("Enter")
        with self.subTest(native_enter=True):
            expect(button).to_be_disabled(timeout=500)
        if not self.http.pending:
            button.click()
        expect(button).to_be_disabled()
        field.press("Enter")
        self.assertEqual(len(self.http.requests), before + 1)
        self.assertEqual(
            self.http.requests[-1], {"method": "PATCH", "body": {"solana_wallet": address.strip()}}
        )
        self.capture(page, "connect-pending-" + str(before))

    def disconnect(self, page):
        """Hold the real DELETE and ensure a second activation cannot write twice."""
        before = len(self.http.requests)
        button = self.widget.get_by_role("button", name=self.copy["disconnect"], exact=True)
        button.click()
        expect(button).to_be_disabled()
        button.press("Enter")
        self.assertEqual(len(self.http.requests), before + 1)
        self.assertEqual(self.http.requests[-1], {"method": "DELETE", "body": None})
        self.capture(page, "disconnect-pending-" + str(before))

    def reply(self, status, body):
        """Resolve exactly the one held wallet boundary request."""
        self.assertEqual(len(self.http.pending), 1)
        self.http.pending.pop(0).fulfill(status=status, json=body)

    def check_error(self, page, key):
        """Require localized feedback in the wallet widget, not a profile-wide fallback."""
        expect(self.widget.get_by_role("alert")).to_contain_text(self.copy[key])
        self.capture(page, "error-" + str(len(self.http.requests)))

    def contract_flow(self, page):
        """Cover real422 shape, ownership409, connect success and retryable DELETE503."""
        self.connect(page, "invalid-wallet")
        self.reply(422, VALIDATION)
        self.check_error(page, "invalidAddress")
        self.connect(page, ADDRESS)
        self.reply(409, {"detail": CONFLICT})
        self.check_error(page, "solanaWalletAlreadyConnectedToAnotherAccount")
        self.connect(page, ADDRESS)
        self.reply(200, {"solana_wallet": ADDRESS, "status": "connected"})
        expect(
            self.widget.get_by_role("button", name=self.copy["disconnect"], exact=True)
        ).to_be_visible()
        expect(
            self.widget.get_by_role("link", name=self.copy["viewOnSolscan"], exact=True)
        ).to_have_attribute("href", "https://solscan.io/account/" + ADDRESS)
        self.capture(page, "connected")
        self.disconnect(page)
        self.reply(503, {"detail": "Synthetic disconnect unavailable"})
        self.check_error(page, "failedToDisconnect")
        expect(self.widget.get_by_text(ADDRESS, exact=True)).to_be_visible()
        self.disconnect(page)
        self.reply(200, {"status": "disconnected"})
        expect(self.widget.get_by_role("textbox")).to_be_visible()
        expect(self.widget.get_by_role("alert")).to_have_count(0)
        self.capture(page, "disconnected")

    def network_flow(self, page, mode):
        """Network failure keeps the original branch visible and retryable."""
        if mode == "connect-network":
            self.connect(page, ADDRESS)
        else:
            self.capture(page, "connected-initial")
            self.disconnect(page)
        self.http.pending.pop(0).abort("failed")
        self.check_error(page, "networkError")
        if mode == "connect-network":
            expect(self.widget.get_by_role("textbox")).to_be_visible()
            self.connect(page, ADDRESS)
            self.reply(200, {"solana_wallet": ADDRESS, "status": "connected"})
            expect(
                self.widget.get_by_role("button", name=self.copy["disconnect"], exact=True)
            ).to_be_visible()
        else:
            expect(self.widget.get_by_text(ADDRESS, exact=True)).to_be_visible()
            self.disconnect(page)
            self.reply(200, {"status": "disconnected"})
            expect(self.widget.get_by_role("textbox")).to_be_visible()
        self.capture(page, "network-recovered")

    def test_wallet_contracts_and_network_recovery_in_six_layouts(self):
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                for mode in ("contract", "connect-network", "disconnect-network"):
                    with self.subTest(locale=locale, width=width, mode=mode):
                        page = self.open_wallet(locale, width, mode)
                        try:
                            if mode == "contract":
                                self.contract_flow(page)
                            else:
                                self.network_flow(page, mode)
                        finally:
                            page.context.close()


if __name__ == "__main__":
    ProfileWalletTests.options, unittest_args = battles.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
