"""Keep public-chat history and drafts when older reads or deletes fail."""

import json
import unittest
from urllib.parse import urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_public_chat_history_auth import CURSOR_QUERY, OLDER, ROWS, TOKEN_A
from test_public_chat_states import STREAM

COPY = {
    "en": {
        "older": "Could not load older messages.",
        "delete": "Could not delete message.",
        "retry": "Retry",
        "remove": "Delete",
    },
    "ru": {
        "older": "Не удалось загрузить предыдущие сообщения.",
        "delete": "Не удалось удалить сообщение.",
        "retry": "Повторить",
        "remove": "Удалить",
    },
}
DRAFT = "A synthetic draft preserved through operation failures"
LIVE = dict(
    ROWS[0],
    id="synthetic-operation-live",
    content="A new synthetic stream message",
    ts="2026-10-20T10:00:00Z",
)
DELETE_PATH = "/api/v1/chat/human-messages/" + ROWS[0]["id"]
TEXT_METRICS = """el => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT), sizes = [];
    while (walker.nextNode()) {
        const text = walker.currentNode;
        if (text.textContent.trim()) sizes.push(parseFloat(getComputedStyle(text.parentElement).fontSize));
    }
    return sizes;
}"""


class OperationHTTP(AuthHTTP):
    """Hold only authenticated cursor reads and deletion of the named synthetic row."""

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/chat" and not target.query:
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        same_origin = target.scheme == "http" and target.netloc == self.origin
        history = (
            request.method == "GET"
            and target.path == "/api/v1/chat/messages"
            and target.query in ("limit=50", CURSOR_QUERY)
        )
        deletion = (
            request.method == "DELETE"
            and target.path == DELETE_PATH
            and not target.query
        )
        if same_origin and (history or deletion):
            record = {
                "method": request.method,
                "path": target.path,
                "query": target.query,
                "authorization": request.headers.get("authorization"),
            }
            self.requests.append(record)
            if record["authorization"] != "Bearer " + TOKEN_A:
                route.fulfill(
                    status=401, json={"detail": "Synthetic authentication required"}
                )
            elif history and target.query == "limit=50":
                route.fulfill(json=ROWS)
            else:
                self.pending.append(route)
            return
        super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class PublicChatOperationErrorTests(unittest.TestCase):
    """Exercise visible recovery without bypassing React scroll or keyboard actions."""

    @classmethod
    def setUpClass(cls):
        BrowserFixture.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        BrowserFixture.tearDownClass.__func__(cls)

    def open_chat(self, locale, width):
        self.locale, self.width, self.errors, self.control_metrics = (
            locale,
            width,
            [],
            [],
        )
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
        context.add_init_script(f"localStorage.setItem('access_token', '{TOKEN_A}')")
        self.http = OperationHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldOperations", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + "/chat", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.input = self.page.locator("form textarea")
        expect(self.main.get_by_text(ROWS[0]["content"], exact=True)).to_have_count(1)
        self.page.wait_for_function(
            "() => document.querySelector('#main-content').scrollTop > 100"
        )
        self.input.fill(DRAFT)
        self.assertEqual(self.http.requests[0]["authorization"], "Bearer " + TOKEN_A)

    def held(self, method):
        self.page.evaluate("""async () => {
            const deadline = performance.now() + 7000;
            while (await window.__heldOperations() !== 1) {
                if (performance.now() > deadline) throw Error('Expected one held chat operation');
                await new Promise(requestAnimationFrame);
            }
        }""")
        record = self.http.requests[-1]
        self.assertEqual(record["method"], method)
        self.assertEqual(record["authorization"], "Bearer " + TOKEN_A)
        self.assertEqual(record["query"], CURSOR_QUERY if method == "GET" else "")
        return self.http.pending.pop()

    def complete(self, route, failure=None):
        if failure == "network":
            with self.page.expect_event(
                "requestfailed", predicate=lambda req: req == route.request
            ):
                route.abort("failed")
        else:
            with self.page.expect_request_finished(lambda req: req == route.request):
                route.fulfill(
                    status=503 if failure else 200,
                    json={"detail": "Synthetic operation rejected"}
                    if failure
                    else (
                        [OLDER]
                        if route.request.method == "GET"
                        else {"status": "ok", "message_id": ROWS[0]["id"]}
                    ),
                )
        self.page.evaluate("""async () => {
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
        }""")

    def tab_to(self, control):
        for _ in range(
            self.page.locator("a[href],button,input,textarea").count() * 2 + 4
        ):
            self.page.keyboard.press("Tab")
            if control.evaluate("el => el === document.activeElement"):
                break
        expect(control).to_be_focused()
        self.assertTrue(control.evaluate("el => el.matches(':focus-visible')"))

    def keep_rows(self, deleted=False):
        contents = self.main.locator("p").all_text_contents()
        for row in ROWS:
            if deleted and row["id"] == ROWS[0]["id"]:
                self.assertNotIn(row["content"], contents)
            else:
                self.assertEqual(contents.count(row["content"]), 1)
        expect(self.input).to_have_value(DRAFT)

    def error_controls(self, operation, scope):
        alert = scope.get_by_role("alert").filter(has_text=COPY[self.locale][operation])
        expect(
            alert.get_by_text(COPY[self.locale][operation], exact=True)
        ).to_be_visible()
        retry = alert.get_by_role("button", name=COPY[self.locale]["retry"], exact=True)
        self.tab_to(retry)
        expect(retry).to_be_visible()
        box = retry.bounding_box()
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box["width"], 44)
        self.assertGreaterEqual(box["height"], 44)
        sizes = alert.evaluate(TEXT_METRICS)
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes), 12)
        self.control_metrics.append(
            {"operation": operation, "retry_box": box, "text_sizes": sizes}
        )
        return retry

    def save(self, operation, failure, phase):
        self.page.evaluate("document.fonts.ready")
        name = (
            f"chat-operation-{self.locale}-{self.width}-{operation}-{failure}-{phase}"
        )
        self.page.screenshot(
            path=self.options.screenshots / f"{name}.png", animations="disabled"
        )
        record = {
            "locale": self.locale,
            "width": self.width,
            "operation": operation,
            "failure": failure,
            "phase": phase,
            "requests": self.http.requests,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "new_error_controls": self.control_metrics,
            "draft": self.input.input_value(),
            "document_width": self.page.evaluate(
                "document.documentElement.scrollWidth"
            ),
        }
        (self.options.screenshots / f"{name}.json").write_text(
            json.dumps(record, indent=2)
        )
        self.assertLessEqual(record["document_width"], self.width)
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def older_history(self, failure):
        self.main.evaluate("el => el.scrollTo({top: 0})")
        pending = self.held("GET")
        self.page.evaluate(
            "msg => window.__chatStream.onmessage({data: JSON.stringify(msg)})", LIVE
        )
        self.main.evaluate("""async el => {
            el.scrollTo({top: 1}); await new Promise(requestAnimationFrame);
            el.scrollTo({top: 0}); await new Promise(requestAnimationFrame);
        }""")
        self.assertEqual(len(self.http.requests), 2)
        self.complete(pending, failure)
        self.keep_rows()
        expect(self.main.get_by_text(LIVE["content"], exact=True)).to_have_count(1)
        retry = self.error_controls("older", self.main)
        self.save("older", failure, "error")
        self.page.keyboard.press("Enter")
        self.page.keyboard.press("Enter")
        self.complete(self.held("GET"))
        expect(self.main.get_by_text(OLDER["content"], exact=True)).to_have_count(1)
        expect(
            self.main.get_by_text(COPY[self.locale]["older"], exact=True)
        ).to_have_count(0)
        expect(self.main.get_by_text(LIVE["content"], exact=True)).to_have_count(1)
        self.keep_rows()
        self.assertEqual(len(self.http.requests), 3)
        self.assertEqual(
            [r["query"] for r in self.http.requests[1:]], [CURSOR_QUERY, CURSOR_QUERY]
        )
        expect(retry).to_have_count(0)
        self.save("older", failure, "recovered")

    def delete_message(self, failure):
        row = self.main.locator(f'[data-message-id="{ROWS[0]["id"]}"]')
        button = row.get_by_role("button", name=COPY[self.locale]["remove"], exact=True)
        button.focus()
        self.page.keyboard.press("Enter")
        pending = self.held("DELETE")
        self.page.keyboard.press("Enter")
        self.assertEqual(len(self.http.requests), 2)
        self.complete(pending, failure)
        self.keep_rows()
        button.focus()
        retry = self.error_controls("delete", row)
        self.save("delete", failure, "error")
        self.page.keyboard.press("Enter")
        self.page.keyboard.press("Enter")
        self.complete(self.held("DELETE"))
        expect(row.get_by_text("[deleted]", exact=True)).to_have_count(1)
        expect(row.get_by_role("alert")).to_have_count(0)
        expect(retry).to_have_count(0)
        self.keep_rows(deleted=True)
        self.assertEqual(len(self.http.requests), 3)
        self.assertEqual(
            [r["path"] for r in self.http.requests[1:]], [DELETE_PATH, DELETE_PATH]
        )
        self.save("delete", failure, "recovered")

    def matrix(self, scenario):
        for locale in COPY:
            for width in (320, 390, 1280):
                for failure in ("http", "network"):
                    with self.subTest(locale=locale, width=width, failure=failure):
                        try:
                            self.open_chat(locale, width)
                            scenario(failure)
                        finally:
                            self.doCleanups()

    def test_older_failure_preserves_rows_draft_and_live_message_until_retry(self):
        self.matrix(self.older_history)

    def test_delete_failure_preserves_only_target_until_successful_retry(self):
        self.matrix(self.delete_message)


if __name__ == "__main__":
    PublicChatOperationErrorTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
