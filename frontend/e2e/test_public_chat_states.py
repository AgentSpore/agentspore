"""Exercise public-chat read recovery on an explicitly synthetic loopback stand."""

import json
import unittest
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect

COPY = {
    "en": ("Could not load messages.", "Retry", "The chat is quiet"),
    "ru": ("Не удалось загрузить сообщения.", "Повторить", "В чате тихо"),
}
MESSAGE = {
    "id": "synthetic-history",
    "agent_id": "synthetic-agent",
    "agent_name": "Synthetic agent",
    "specialization": "programmer",
    "content": "A synthetic idea from history",
    "message_type": "idea",
    "sender_type": "agent",
    "ts": "2026-10-20T09:00:00Z",
}
STREAM = """class SyntheticEventSource {
    constructor() { window.__chatStream = this; }
    close() { this.closed = true; }
}; window.EventSource = SyntheticEventSource;"""


class ChatHTTP(AuthHTTP):
    """Hold history reads; reject every unexpected request using the shared boundary."""

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/chat" and not target.query:
            return True
        query = parse_qs(target.query)
        if (
            request.method == "GET"
            and target.path == "/agents/" + MESSAGE["agent_id"]
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and set(query) == {"_rsc"}
            and all(len(value) == 1 for value in query.values())
        ):
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        target, request = urlparse(route.request.url), route.request
        if (
            target.scheme == "http"
            and target.netloc == self.origin
            and target.path == "/api/v1/chat/messages"
            and target.query == "limit=50"
            and request.method == "GET"
        ):
            self.requests.append({"method": request.method, "path": target.path})
            self.pending.append(route)
            return
        super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class PublicChatReadTests(unittest.TestCase):
    """Keep read-error recovery distinct from a successful empty chat."""

    @classmethod
    def setUpClass(cls):
        BrowserFixture.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        BrowserFixture.tearDownClass.__func__(cls)

    def open_chat(self, locale, width):
        context = self.browser.new_context(
            viewport={"width": width, "height": 800}, service_workers="block"
        )
        context.add_cookies(
            [
                {
                    "name": "agentspore_locale",
                    "value": locale,
                    "url": self.options.base_url,
                }
            ]
        )
        context.add_init_script(STREAM)
        http = ChatHTTP(self.options.base_url)
        self.addCleanup(http.close, context)
        context.route("**/*", http.route)
        page = context.new_page()
        page.set_default_timeout(7000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.expose_function("__pendingChatReads", lambda: len(http.pending))
        page.goto(self.options.base_url + "/chat", wait_until="domcontentloaded")
        self.wait_for_read(page)
        return page, http, errors

    def wait_for_read(self, page):
        page.evaluate("""async () => {
            const deadline = performance.now() + 7000;
            while (await window.__pendingChatReads() !== 1) {
                if (performance.now() > deadline) throw Error('Expected one held chat read');
                await new Promise(requestAnimationFrame);
            }
        }""")

    def tab_to(self, page, control):
        for _ in range(page.locator("a[href],button,input,textarea").count() * 2 + 4):
            page.keyboard.press("Tab")
            if control.evaluate("el => el === document.activeElement"):
                return
        self.fail("Native Tab did not reach the chat retry control")

    def save(self, page, locale, width, state):
        page.evaluate("document.fonts.ready")
        name = f"chat-read-{locale}-{width}-{state}"
        page.screenshot(
            path=self.options.screenshots / f"{name}.png", animations="disabled"
        )
        record = {
            "locale": locale,
            "width": width,
            "state": state,
            "document_width": page.evaluate("document.documentElement.scrollWidth"),
        }
        (self.options.screenshots / f"{name}.json").write_text(
            json.dumps(record, indent=2)
        )
        self.assertLessEqual(record["document_width"], width)

    def exercise_recovery(self, locale, width, failure):
        error, retry, empty = COPY[locale]
        page, http, errors = self.open_chat(locale, width)
        route = http.pending.pop()
        if failure == "http":
            route.fulfill(status=503, json={"detail": "Synthetic service unavailable"})
        else:
            route.abort("failed")
        expect(page.get_by_text(error, exact=True)).to_be_visible()
        expect(page.get_by_text(empty, exact=True)).to_have_count(0)
        button = page.get_by_role("button", name=retry, exact=True)
        self.tab_to(page, button)
        self.assertTrue(button.evaluate("el => el.matches(':focus-visible')"))
        self.save(page, locale, width, f"{failure}-error")
        page.keyboard.press("Enter")
        self.wait_for_read(page)
        event = dict(
            MESSAGE,
            id="synthetic-live",
            content="A new synthetic idea",
            ts="2026-10-20T09:01:00Z",
        )
        page.evaluate(
            "msg => window.__chatStream.onmessage({data: JSON.stringify(msg)})", event
        )
        http.pending.pop().fulfill(json=[MESSAGE])
        expect(page.get_by_text(MESSAGE["content"], exact=True)).to_be_visible()
        expect(page.get_by_text(event["content"], exact=True)).to_be_visible()
        expect(page.get_by_text(error, exact=True)).to_have_count(0)
        self.assertEqual(len(http.requests), 2)
        self.assertEqual(http.unexpected, [])
        self.assertEqual(errors, [])
        self.save(page, locale, width, f"{failure}-recovered")

    def test_initial_error_and_retry_preserve_live_messages(self):
        for locale in COPY:
            for width in (320, 390, 1280):
                for failure in ("http", "network"):
                    with self.subTest(locale=locale, width=width, failure=failure):
                        try:
                            self.exercise_recovery(locale, width, failure)
                        finally:
                            self.doCleanups()


if __name__ == "__main__":
    PublicChatReadTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
