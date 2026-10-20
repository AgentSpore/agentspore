"""Preserve public-chat drafts across delayed synthetic mutation responses."""

import json
import unittest
from urllib.parse import urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_public_chat_states import STREAM

SESSION = "synthetic-chat-owner"
MESSAGE = {
    "id": "synthetic-owned-message",
    "agent_id": None,
    "agent_name": USER["name"],
    "specialization": "user",
    "content": "An existing synthetic message",
    "message_type": "text",
    "sender_type": "user",
    "ts": "2026-10-20T09:00:00Z",
    "is_deleted": False,
}
COPY = {
    "en": {"edit": "Edit", "editing": "Editing message", "network": "Network error"},
    "ru": {"edit": "Изменить", "editing": "Редактирование сообщения", "network": "Ошибка сети"},
}


class MutationHTTP(AuthHTTP):
    """Hold only authenticated public-chat writes at the real HTTP boundary."""

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/chat" and not target.query:
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if (
            request.method == "GET"
            and target.path == "/api/v1/chat/messages"
            and target.query == "limit=50"
        ):
            route.fulfill(json=[MESSAGE])
            return
        paths = {
            "POST": "/api/v1/chat/human-message",
            "PATCH": "/api/v1/chat/human-messages/" + MESSAGE["id"],
        }
        if request.method in paths and target.path == paths[request.method] and not target.query:
            body = request.post_data_json
            if (
                isinstance(body, dict)
                and set(body) == {"content"}
                and isinstance(body["content"], str)
                and request.headers.get("authorization") == "Bearer " + SESSION
            ):
                self.requests.append({"method": request.method, "path": target.path, "body": body})
                self.pending.append(route)
                return
            return self.reject(route)
        super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class PublicChatMutationTests(unittest.TestCase):
    """Exercise real React drafts and edit sessions while HTTP writes remain held."""

    @classmethod
    def setUpClass(cls):
        BrowserFixture.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        BrowserFixture.tearDownClass.__func__(cls)

    def open_chat(self, locale, width):
        self.locale, self.width, self.errors = locale, width, []
        context = self.browser.new_context(
            viewport={"width": width, "height": 800}, service_workers="block"
        )
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.add_init_script(STREAM)
        context.add_init_script(f"localStorage.setItem('access_token', '{SESSION}')")
        self.http = MutationHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldChatMutations", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + "/chat", wait_until="domcontentloaded")
        expect(self.page.get_by_text(MESSAGE["content"], exact=True)).to_be_visible()
        self.input = self.page.locator("form textarea")
        expect(self.input).to_be_visible()
        self.submit = self.page.locator("form button[type='submit']")

    def held(self, method, content):
        self.page.evaluate("""async () => {
            const deadline = performance.now() + 7000;
            while (await window.__heldChatMutations() !== 1) {
                if (performance.now() > deadline) throw Error('Expected one held chat mutation');
                await new Promise(requestAnimationFrame);
            }
        }""")
        self.assertEqual(self.http.requests[-1]["method"], method)
        self.assertEqual(self.http.requests[-1]["body"], {"content": content})
        return self.http.pending.pop()

    def complete(self, route, outcome="ok"):
        if outcome == "network":
            with self.page.expect_event(
                "requestfailed", predicate=lambda request: request == route.request
            ):
                route.abort("failed")
        else:
            with self.page.expect_request_finished(lambda request: request == route.request):
                route.fulfill(
                    status=503 if outcome == "http" else 200,
                    json={"detail": "Synthetic edit rejected"} if outcome == "http" else {},
                )
        self.page.evaluate("""async () => {
            await new Promise(requestAnimationFrame);
            await new Promise(requestAnimationFrame);
        }""")

    def edit(self):
        control = self.page.get_by_role("button", name=COPY[self.locale]["edit"], exact=True)
        for _ in range(self.page.locator("a[href],button,input,textarea").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if control.evaluate("el => el === document.activeElement"):
                break
        expect(control).to_be_focused()
        self.page.keyboard.press("Enter")
        expect(self.input).to_be_focused()
        expect(self.input).to_have_value(MESSAGE["content"])

    def save(self, state):
        self.page.evaluate("document.fonts.ready")
        name = f"chat-mutation-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{name}.png", animations="disabled")
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "draft": self.input.input_value(),
            "requests": self.http.requests,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
            "input_focused": self.input.evaluate("el => el === document.activeElement"),
        }
        (self.options.screenshots / f"{name}.json").write_text(json.dumps(record, indent=2))
        self.assertLessEqual(record["document_width"], self.width)
        self.assertEqual(record["unexpected"], [])
        self.assertEqual(record["page_errors"], [])

    def post_draft(self, _variant):
        self.input.fill("  First submitted draft  ")
        self.input.press("Enter")
        pending = self.held("POST", "First submitted draft")
        expect(self.submit).to_be_disabled()
        self.input.fill("A newer unsent draft")
        self.save("post-pending")
        self.complete(pending)
        expect(self.submit).to_be_enabled()
        expect(self.input).to_have_value("A newer unsent draft")
        self.save("post-preserved")
        self.input.press("Enter")
        self.complete(self.held("POST", "A newer unsent draft"))
        expect(self.input).to_have_value("")
        self.assertEqual(len(self.http.requests), 2)
        self.save("post-new-draft-sent")

    def cancel_patch(self, _variant):
        self.edit()
        self.input.fill("A submitted edit")
        self.input.press("Enter")
        pending = self.held("PATCH", "A submitted edit")
        self.input.press("Escape")
        self.input.fill("A new conversation draft")
        self.save("patch-cancel-pending")
        self.complete(pending)
        expect(self.input).to_have_value("A new conversation draft")
        expect(self.page.get_by_text(COPY[self.locale]["editing"], exact=True)).to_have_count(0)
        expect(self.submit).to_be_enabled()
        self.assertEqual(len(self.http.requests), 1)
        self.save("patch-cancel-preserved")

    def patch_failure(self, failure):
        self.edit()
        self.input.fill("A retryable edit")
        self.input.press("Enter")
        pending = self.held("PATCH", "A retryable edit")
        self.input.fill("A newer retryable edit")
        self.complete(pending, failure)
        error = "Synthetic edit rejected" if failure == "http" else COPY[self.locale]["network"]
        expect(self.page.get_by_text(error, exact=True)).to_be_visible()
        expect(self.input).to_have_value("A newer retryable edit")
        expect(self.submit).to_be_enabled()
        self.save(f"patch-{failure}-retained")
        self.input.press("Enter")
        self.complete(self.held("PATCH", "A newer retryable edit"))
        expect(self.input).to_have_value("")
        expect(
            self.page.locator("main").get_by_text("A newer retryable edit", exact=True)
        ).to_be_visible()
        expect(self.page.get_by_text(error, exact=True)).to_have_count(0)
        self.assertEqual(len(self.http.requests), 2)
        self.save(f"patch-{failure}-retried")

    def reedit_same_message(self, _variant):
        self.edit()
        self.input.fill("An earlier edit")
        self.input.press("Enter")
        pending = self.held("PATCH", "An earlier edit")
        self.input.press("Escape")
        self.edit()
        self.input.fill("The current edit session")
        self.save("same-id-reedit-pending")
        self.complete(pending)
        expect(self.input).to_have_value("The current edit session")
        expect(self.page.get_by_text(COPY[self.locale]["editing"], exact=True)).to_be_visible()
        expect(self.submit).to_be_enabled()
        self.save("same-id-reedit-preserved")
        self.input.press("Enter")
        self.complete(self.held("PATCH", "The current edit session"))
        expect(self.input).to_have_value("")
        expect(
            self.page.locator("main").get_by_text("The current edit session", exact=True)
        ).to_be_visible()
        self.assertEqual(len(self.http.requests), 2)
        self.save("same-id-reedit-saved")

    def matrix(self, scenario, variants=(None,)):
        for locale in COPY:
            for width in (320, 390, 1280):
                for variant in variants:
                    with self.subTest(locale=locale, width=width, variant=variant):
                        try:
                            self.open_chat(locale, width)
                            scenario(variant)
                        finally:
                            self.doCleanups()

    def test_post_completion_preserves_new_draft(self):
        self.matrix(self.post_draft)

    def test_patch_completion_after_escape_preserves_new_draft(self):
        self.matrix(self.cancel_patch)

    def test_failed_patch_keeps_draft_for_successful_retry(self):
        self.matrix(self.patch_failure, ("http", "network"))

    def test_stale_patch_cannot_replace_new_edit_of_same_message(self):
        self.matrix(self.reedit_same_message)


if __name__ == "__main__":
    PublicChatMutationTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
