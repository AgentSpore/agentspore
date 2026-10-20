"""Require authenticated public-chat history without changing server permissions."""

import json
import unittest
from urllib.parse import urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_public_chat_states import COPY, STREAM

TOKEN_A, TOKEN_B = "synthetic-history-a", "synthetic-history-b"
ROWS = [
    {
        "id": f"synthetic-row-{i}",
        "agent_id": None,
        "agent_name": USER["name"],
        "sender_user_id": USER["id"],
        "specialization": "user",
        "content": f"Synthetic history message {i}",
        "message_type": "text",
        "sender_type": "user",
        "ts": f"2026-10-20T09:{i:02}:00Z",
    }
    for i in reversed(range(50))
]
OLDER = dict(
    ROWS[-1], id="synthetic-older", content="A synthetic older message", ts="2026-10-20T08:59:00Z"
)
CURSOR_QUERY = "limit=50&before=synthetic-row-0"


class HistoryHTTP(AuthHTTP):
    """Hold known history requests and reject a missing or incorrect Bearer."""

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/chat" and not target.query:
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if (
            target.scheme == "http"
            and target.netloc == self.origin
            and request.method == "GET"
            and target.path == "/api/v1/chat/messages"
            and target.query in ("limit=50", CURSOR_QUERY)
        ):
            self.requests.append(
                {"query": target.query, "authorization": request.headers.get("authorization")}
            )
            self.pending.append(route)
            return
        super().route(route)

    def reply(self):
        route = self.pending.pop()
        record = self.requests[-1]
        if record["authorization"] != "Bearer " + self.accepted:
            route.fulfill(
                status=401, json={"detail": "Authentication required to read chat messages"}
            )
        else:
            route.fulfill(json=[OLDER] if record["query"] == CURSOR_QUERY else ROWS)
        return record

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class PublicChatHistoryAuthTests(unittest.TestCase):
    """Verify actual history fetches, cursor loading and keyboard recovery."""

    @classmethod
    def setUpClass(cls):
        BrowserFixture.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        BrowserFixture.tearDownClass.__func__(cls)

    def open_chat(self, locale, width, token):
        self.locale, self.width, self.errors = locale, width, []
        context = self.browser.new_context(
            viewport={"width": width, "height": 800}, service_workers="block"
        )
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.add_init_script(STREAM)
        if token:
            context.add_init_script(f"localStorage.setItem('access_token', '{token}')")
        self.http = HistoryHTTP(self.options.base_url)
        self.http.accepted = TOKEN_A
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldHistory", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + "/chat", wait_until="domcontentloaded")
        self.wait_for_read()

    def wait_for_read(self):
        self.page.evaluate("""async () => {
            const deadline = performance.now() + 7000;
            while (await window.__heldHistory() !== 1) {
                if (performance.now() > deadline) throw Error('Expected one held history read');
                await new Promise(requestAnimationFrame);
            }
        }""")

    def save(self, state):
        self.page.evaluate("document.fonts.ready")
        name = f"chat-history-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{name}.png", animations="disabled")
        record = {
            "state": state,
            "locale": self.locale,
            "width": self.width,
            "history_requests": self.http.requests,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "alerts": self.page.get_by_role("alert").evaluate_all(
                "els => els.map(el => ({tag:el.tagName,id:el.id,textContent:el.textContent,host:el.getRootNode().host?.tagName??null}))"
            ),
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
        }
        (self.options.screenshots / f"{name}.json").write_text(json.dumps(record, indent=2))
        self.assertLessEqual(record["document_width"], self.width)
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def assert_error(self):
        error, _retry, empty = COPY[self.locale]
        expect(self.page.get_by_role("alert").get_by_text(error, exact=True)).to_be_visible()
        expect(self.page.get_by_text(empty, exact=True)).to_have_count(0)

    def authenticated_history(self):
        self.assertEqual(self.http.reply()["authorization"], "Bearer " + TOKEN_A)
        expect(self.page.get_by_text(ROWS[0]["content"], exact=True)).to_be_visible()
        self.page.wait_for_function("""() => {
            const el = document.querySelector('#main-content'); return el.scrollTop > 100;
        }""")
        self.page.locator("#main-content").evaluate("el => el.scrollTo({top: 0})")
        self.wait_for_read()
        record = self.http.reply()
        self.assertEqual(record, {"query": CURSOR_QUERY, "authorization": "Bearer " + TOKEN_A})
        expect(self.page.get_by_text(OLDER["content"], exact=True)).to_have_count(1)
        expect(self.page.get_by_text(ROWS[0]["content"], exact=True)).to_have_count(1)
        self.assertEqual(len(self.http.requests), 2)
        self.save("authenticated-older")

    def rotated_retry(self):
        self.http.accepted = TOKEN_B
        self.assertEqual(self.http.reply()["authorization"], "Bearer " + TOKEN_A)
        self.assert_error()
        self.save("unauthorized-before-rotation")
        self.page.evaluate("token => localStorage.setItem('access_token', token)", TOKEN_B)
        retry = self.page.get_by_role("button", name=COPY[self.locale][1], exact=True)
        for _ in range(self.page.locator("a[href],button,input,textarea").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if retry.evaluate("el => el === document.activeElement"):
                break
        expect(retry).to_be_focused()
        self.page.keyboard.press("Enter")
        self.wait_for_read()
        self.assertEqual(self.http.reply()["authorization"], "Bearer " + TOKEN_B)
        expect(self.page.get_by_text(ROWS[0]["content"], exact=True)).to_be_visible()
        expect(self.page.locator("main").get_by_role("alert")).to_have_count(0)
        self.assertEqual(len(self.http.requests), 2)
        self.save("rotated-recovered")

    def anonymous_error(self):
        self.assertIsNone(self.http.reply()["authorization"])
        self.assert_error()
        self.assertEqual(len(self.http.requests), 1)
        self.save("anonymous-unauthorized")

    def matrix(self, scenario, token=TOKEN_A):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_chat(locale, width, token)
                        scenario()
                    finally:
                        self.doCleanups()

    def test_authenticated_initial_and_cursor_history(self):
        self.matrix(self.authenticated_history)

    def test_retry_reads_rotated_token_after_unauthorized_history(self):
        self.matrix(self.rotated_retry)

    def test_missing_token_remains_unauthorized_without_false_empty(self):
        self.matrix(self.anonymous_error, None)


if __name__ == "__main__":
    PublicChatHistoryAuthTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
