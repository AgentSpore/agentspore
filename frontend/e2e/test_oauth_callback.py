"""RR.3 callback regressions against the real local frontend and native navigation."""

import json
import unittest
from urllib.parse import parse_qs, urlencode, urlparse

import auth_form_fixtures as browser_fixtures
from auth_form_fixtures import MESSAGES, AuthHTTP
from playwright.sync_api import expect
from test_project_cards import ACTION, TEXT

PAIR = {"access_token": "synthetic-callback-access", "refresh_token": "synthetic-callback-refresh"}
PREVIOUS = {"access_token": "synthetic-previous-access", "refresh_token": "synthetic-previous-refresh"}
STORAGE_FAULT = """(() => {
    if (location.pathname !== '/auth/callback') return;
    const storage = window.localStorage, original = Storage.prototype.setItem;
    const {mode, previous} = OPTIONS;
    for (const [key, value] of Object.entries(previous)) storage.setItem(key, value);
    window.__callbackTokens = () => ({
        access_token: storage.getItem('access_token'),
        refresh_token: storage.getItem('refresh_token')
    });
    if (mode === 'getter') {
        Object.defineProperty(window, 'localStorage', {configurable: true, get() {
            throw new DOMException('Synthetic storage denied', 'SecurityError');
        }});
    } else if (mode !== 'none') {
        let writes = 0;
        Storage.prototype.setItem = function(key, value) {
            if (this === storage && ['access_token', 'refresh_token'].includes(key)) {
                writes += 1;
                if (mode === 'write' || writes === 2)
                    throw new DOMException('Synthetic storage full', 'QuotaExceededError');
            }
            return original.call(this, key, value);
        };
    }
})();"""


class CallbackHTTP(AuthHTTP):
    """Extend auth fixtures with callback queries and the recovery link's RSC navigation."""

    def accepts_page(self, request, target):
        """Accept singular callback tokens or the exact login RSC navigation query."""
        query = parse_qs(target.query, keep_blank_values=True)
        if target.path == "/login" and request.headers.get("rsc") == "1":
            return (
                request.method == "GET"
                and set(query) == {"_rsc"}
                and len(query["_rsc"]) == 1
                and bool(query["_rsc"][0])
            )
        if target.path != "/auth/callback":
            return super().accepts_page(request, target)
        return (
            request.method == "GET"
            and set(query) <= set(PAIR)
            and all(len(value) == 1 for value in query.values())
        )


class OAuthCallbackTests(unittest.TestCase):
    """Exercise storage failure, partial writes and unchanged callback destinations."""

    setUpClass = classmethod(browser_fixtures.BrowserFixture.setUpClass.__func__)
    tearDownClass = classmethod(browser_fixtures.BrowserFixture.tearDownClass.__func__)

    def open_callback(self, query, mode="none", previous=None):
        """Keep the real page, LocaleProvider and browser navigation; fault storage only."""
        target = urlparse(self.options.base_url)
        self.assertEqual(target.scheme, "http")
        self.assertIn(target.hostname, ("127.0.0.1", "localhost", "::1"))
        context = self.browser.new_context(
            viewport={"width": self.width, "height": 900},
            locale=self.locale, service_workers="block",
        )
        self.addCleanup(context.close)
        context.add_cookies([
            {"name": "agentspore_locale", "value": self.locale, "url": self.options.base_url}
        ])
        context.add_init_script(STORAGE_FAULT.replace(
            "OPTIONS", json.dumps({"mode": mode, "previous": previous or {}})
        ))
        self.http, self.errors = CallbackHTTP(self.options.base_url), []
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.set_default_timeout(5000)
        page.goto(self.options.base_url + "/auth/callback" + (f"?{urlencode(query)}" if query else ""))
        return page

    def capture(self, page, state):
        """Save screenshots and observable failure evidence even during the RED run."""
        target = self.options.screenshots / f"callback-{self.locale}-{self.width}-{state}"
        address = urlparse(page.url)
        record = {
            "path": address.path, "queryKeys": list(parse_qs(address.query)),
            "tokensInAddress": any(token in page.url for token in PAIR.values()),
            "pageErrors": self.errors, "unexpected": self.http.unexpected,
            "storedTokenCount": page.evaluate(
                "typeof window.__callbackTokens === 'function' "
                "? Object.values(window.__callbackTokens()).filter(Boolean).length : null"
            ),
            "text": page.locator("main").inner_text() if page.locator("main").count() else "",
            "documentWidth": page.evaluate("document.documentElement.scrollWidth"),
            "viewport": self.width,
        }
        target.with_suffix(".json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        page.screenshot(path=str(target.with_suffix(".png")), full_page=True)

    def assert_recovery(self, page):
        """Assert localized error, clean address and a keyboard-operable login action."""
        copy = MESSAGES[self.locale]
        with self.subTest(error=True):
            expect(page.locator("main").get_by_role("alert")).to_contain_text(
                copy["couldNotSaveSessionCheckBrowserStorageSettings"]
            )
        with self.subTest(address=True):
            self.assertEqual(urlparse(page.url).path, "/auth/callback")
            self.assertFalse(bool(urlparse(page.url).query), "Callback error URL retains query parameters")
        with self.subTest(no_exception=True):
            self.assertEqual(self.errors, [])
        with self.subTest(no_leak=True):
            text = page.locator("body").inner_text()
            for token in PAIR.values():
                self.assertNotIn(token, text)
            expect(page.get_by_text(copy["signingYouIn"], exact=True)).to_have_count(0)
        with self.subTest(layout=True):
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), self.width)
            for text in page.locator("main").evaluate_all("els => els.flatMap(" + TEXT + ")"):
                self.assertGreaterEqual(text["size"], 12, text)
        with self.subTest(keyboard=True):
            link = page.get_by_role("link", name=copy["backToLogin"], exact=True)
            expect(link).to_have_attribute("href", "/login")
            for _ in range(30):
                page.keyboard.press("Tab")
                if link.evaluate("el => el === document.activeElement"):
                    break
            action = link.evaluate(ACTION)
            self.assertTrue(action["focused"], action)
            self.assertTrue(action["focusVisible"], action)
            self.assertTrue(action["hit"], action)
            self.assertGreaterEqual(action["width"], 44, action)
            self.assertGreaterEqual(action["height"], 44, action)
            page.keyboard.press("Enter")
            page.wait_for_url(self.options.base_url + "/login")
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.http.requests, [])

    def test_storage_denied_shows_recovery_without_query_tokens(self):
        for self.locale in ("en", "ru"):
            for self.width in (320, 390, 1280):
                for mode in ("getter", "write", "second"):
                    with self.subTest(locale=self.locale, width=self.width, mode=mode):
                        page = self.open_callback(PAIR, mode, PREVIOUS if mode == "second" else None)
                        try:
                            with self.subTest(error_visible=True):
                                expect(page.locator("main").get_by_role("alert")).to_be_visible()
                            self.capture(page, mode)
                            if mode != "getter":
                                with self.subTest(no_partial_pair=True):
                                    self.assertEqual(page.evaluate("window.__callbackTokens()"), {
                                        "access_token": None, "refresh_token": None,
                                    })
                            self.assert_recovery(page)
                        finally:
                            page.context.close()

    def test_complete_pair_saves_both_tokens_and_navigates_to_profile(self):
        self.locale, self.width = "en", 390
        page = self.open_callback(PAIR, previous=PREVIOUS)
        page.wait_for_url(self.options.base_url + "/profile")
        expect(page.locator("main")).to_be_visible()
        for key, value in PAIR.items():
            self.assertEqual(page.evaluate("key => localStorage.getItem(key)", key), value)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.http.unexpected, [])
        self.capture(page, "success")

    def test_missing_pair_preserves_storage_and_profile_navigation(self):
        self.locale, self.width = "en", 390
        for query in ({}, {"access_token": PAIR["access_token"]},
                      {"refresh_token": PAIR["refresh_token"]}):
            for previous in ({}, PREVIOUS):
                with self.subTest(query_keys=list(query), previous=bool(previous)):
                    page = self.open_callback(query, previous=previous)
                    try:
                        page.wait_for_url(self.options.base_url + "/profile")
                        expect(page.locator("main")).to_be_visible()
                        for key in PAIR:
                            self.assertEqual(
                                page.evaluate("key => localStorage.getItem(key)", key), previous.get(key)
                            )
                        self.assertEqual(self.errors, [])
                        self.assertEqual(self.http.unexpected, [])
                    finally:
                        page.context.close()


if __name__ == "__main__":
    OAuthCallbackTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
