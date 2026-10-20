"""Recover project Discussion reads and writes through actual visible controls."""

import json
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_chat_ownership import (
    ACCOUNT_A,
    ACCOUNT_B,
    COPY,
    DISPLAY,
    OwnershipHTTP,
    PROJECT_ID,
    synthetic_session,
)
from test_public_chat_accessibility import FOCUS
from test_public_chat_states import STREAM

BASE = f"/api/v1/chat/project/{PROJECT_ID}"
DRAFT, UPDATE = "Synthetic retained draft", "Synthetic amended row"
SENTINEL = "Synthetic private failure sentinel"
COPY_ERROR = {
    "en": (
        "Could not load messages.",
        "Could not load older messages.",
        "Failed to send",
        "Failed to edit",
        "Could not delete message.",
        "Retry",
        "No messages yet",
        "Editing message",
    ),
    "ru": (
        "Не удалось загрузить сообщения.",
        "Не удалось загрузить предыдущие сообщения.",
        "Не удалось отправить сообщение",
        "Не удалось изменить сообщение",
        "Не удалось удалить сообщение.",
        "Повторить",
        "Сообщений пока нет",
        "Редактирование сообщения",
    ),
}
OPERATIONS = ("initial", "older", "post", "patch", "delete")


def history_rows():
    rows = []
    for i in range(50):
        rows.append(
            {
                "id": f"error-row-{i}",
                "sender_user_id": ACCOUNT_A if i == 0 else ACCOUNT_B if i == 1 else None,
                "sender_name": DISPLAY if i < 2 else "Synthetic agent",
                "sender_handle": None,
                "sender_type": "human" if i < 2 else "agent",
                "content": f"Synthetic history {i}",
                "message_type": "text",
                "created_at": f"2026-10-20T09:{i:02}:00Z",
            }
        )
    return rows


class ProjectErrorHTTP(OwnershipHTTP):
    """Reuse accepted project reads, adding only exact faulted operation contracts."""

    def __init__(self, base_url, group):
        super().__init__(base_url, "project")
        self.active = "initial" if group == "read" else None
        self.rows, self.calls = history_rows(), []
        self.group, self.background_cursor_reads = group, 0

    def hold(self, route, operation):
        request = route.request
        target = urlparse(request.url)
        self.calls.append(
            {
                "operation": operation,
                "authorization_matched": True,
                "method": request.method,
                "path": target.path + ("?" + target.query if target.query else ""),
                "body": request.post_data_json if request.method in {"POST", "PATCH"} else None,
            }
        )
        self.pending.append((route, operation))

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/auth/me" and not target.query:
            if request.headers.get("authorization") != "Bearer " + synthetic_session("initial"):
                return self.reject(route)
            route.fulfill(json=dict(USER, id=ACCOUNT_A, name=DISPLAY))
            return
        if request.method == "GET" and target.path == BASE + "/messages":
            return self.history(route)
        if request.method in {"POST", "PATCH", "DELETE"}:
            return self.write(route)
        return super().route(route)

    def history(self, route):
        target = urlparse(route.request.url)
        if route.request.headers.get("authorization") is not None:
            return self.reject(route)
        if target.query == "limit=50":
            if self.active == "initial":
                return self.hold(route, "initial")
            route.fulfill(json=list(reversed(self.rows)))
            return
        if target.query == "limit=50&before=error-row-0":
            if self.active == "older":
                return self.hold(route, "older")
            if self.group == "write":
                self.background_cursor_reads += 1
                route.fulfill(json=[])
                return
        return self.reject(route)

    def write(self, route):
        request, target = route.request, urlparse(route.request.url)
        method = {"post": "POST", "patch": "PATCH", "delete": "DELETE"}.get(self.active)
        path = BASE + "/human-messages" + ("" if self.active == "post" else "/error-row-0")
        body = (
            {"name": DISPLAY, "content": DRAFT, "message_type": "text"}
            if self.active == "post"
            else {"content": UPDATE}
        )
        if (
            request.method != method
            or target.path != path
            or target.query
            or request.headers.get("authorization") != "Bearer " + synthetic_session("initial")
        ):
            return self.reject(route)
        if request.method != "DELETE" and request.post_data_json != body:
            return self.reject(route)
        self.hold(route, self.active)


class ProjectChatErrorTests(BrowserFixture, unittest.TestCase):
    """Read group uses HTTP503; write group uses network failures, without real accounts."""

    options: Namespace

    def open_chat(self, locale, width, group):
        self.locale, self.width, self.errors, self.retry_metrics = locale, width, [], None
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, service_workers="block"
        )
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.add_init_script(STREAM)
        context.add_init_script(
            "localStorage.setItem('access_token', " + json.dumps(synthetic_session("initial")) + ")"
        )
        self.http = ProjectErrorHTTP(self.options.base_url, group)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldProjectErrors", lambda: len(self.http.pending))
        self.page.goto(
            self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded"
        )
        self.page.get_by_role("button", name=COPY[locale][2], exact=True).click()
        self.main = self.page.locator("#main-content")
        self.input = self.main.locator("form textarea")
        expect(self.input).to_be_visible()
        self.panel = self.main.locator(".chat-panel")
        if group == "write":
            expect(self.panel.get_by_text("Synthetic history 0", exact=True)).to_be_visible()

    def row(self):
        return self.panel.get_by_text(self.http.rows[0]["content"], exact=True).locator(
            "xpath=ancestor::div[contains(concat(' ', @class, ' '), ' group ')][1]"
        )

    def wait_pending(self):
        self.page.evaluate("""async () => {
            const end=performance.now()+7000;
            while(await window.__heldProjectErrors()!==1){
                if(performance.now()>end)throw Error('Expected one held project operation');
                await new Promise(requestAnimationFrame);
            }
        }""")
        self.assertEqual(len(self.http.pending), 1)

    def call_count(self, operation):
        return sum(call["operation"] == operation for call in self.http.calls)

    def save(self, operation, state):
        self.page.evaluate("document.fonts.ready")
        record = {
            "locale": self.locale,
            "width": self.width,
            "operation": operation,
            "state": state,
            "draft": self.input.input_value(),
            "retry_metrics": self.retry_metrics,
            "editing": self.main.get_by_text(COPY_ERROR[self.locale][7], exact=True).count() == 1,
            "rows": self.panel.locator(".group").all_text_contents(),
            "calls": self.http.calls,
            "background_cursor_reads": self.http.background_cursor_reads,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
        }
        stem = f"project-errors-{self.locale}-{self.width}-{operation}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertLessEqual(record["document_width"], self.width)
        expect(self.main.get_by_text(SENTINEL, exact=False)).to_have_count(0)

    def complete(self, operation, failure=False):
        route, actual = self.http.pending.pop()
        self.assertEqual(actual, operation)
        if failure:
            if operation in {"initial", "older"}:
                route.fulfill(status=503, json={"detail": SENTINEL})
            else:
                route.abort("failed")
            return
        if operation == "initial":
            body = list(reversed(self.http.rows))
        elif operation == "older":
            older = dict(
                self.http.rows[0],
                id="error-row-older",
                content="Synthetic older history",
                created_at="2026-10-20T08:59:00Z",
            )
            self.http.rows.insert(0, older)
            body = [older]
        else:
            body = {"status": "ok", "message_id": "error-row-0"}
            if operation == "patch":
                self.http.rows[0]["content"] = UPDATE
            if operation == "delete":
                self.http.rows[0]["content"] = "[deleted]"
        self.http.active = None
        route.fulfill(json=body)

    def start(self, operation):
        self.retry_metrics = None
        self.http.active = operation
        if operation == "patch":
            self.row().get_by_role("button", name=COPY[self.locale][0], exact=True).click()
        self.input.fill(UPDATE if operation == "patch" else DRAFT)
        if operation == "older":
            self.panel.evaluate("""async el=>{el.scrollTo({top:1});await new Promise(requestAnimationFrame);
                el.scrollTo({top:0});await new Promise(requestAnimationFrame);}""")
        elif operation == "delete":
            button = self.row().get_by_role("button", name=COPY[self.locale][1], exact=True)
            button.click()
            button.dispatch_event("click")
        elif operation in {"post", "patch"}:
            self.input.press("Enter")
        self.wait_pending()
        self.assertEqual(self.call_count(operation), 1)

    def retained(self, operation):
        expect(self.input).to_have_value(UPDATE if operation == "patch" else DRAFT)
        if operation == "initial":
            expect(self.panel.get_by_text(COPY_ERROR[self.locale][6], exact=True)).to_have_count(0)
        else:
            for row in self.http.rows:
                expect(self.panel.get_by_text(row["content"], exact=True)).to_have_count(1)
        if operation == "patch":
            expect(self.main.get_by_text(COPY_ERROR[self.locale][7], exact=True)).to_be_visible()

    def focus_retry(self, button, alert):
        for _ in range(self.page.locator("a[href],button,input,textarea").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if button.evaluate("el=>el===document.activeElement"):
                break
        expect(button).to_be_focused()
        self.retry_metrics = button.evaluate(FOCUS)
        box = button.bounding_box()
        self.assertIsNotNone(box)
        if box is None:
            self.fail("Retry has no bounding box")
        self.retry_metrics["box"] = box
        self.assertTrue(all(self.retry_metrics[k] for k in ("active", "visible", "painted")))
        self.assertGreaterEqual(box["width"], 44)
        self.assertGreaterEqual(box["height"], 44)
        self.retry_metrics["notice_fonts"] = self.notice_fonts(alert)

    def notice_fonts(self, alert):
        fonts = alert.evaluate("""el=>{const w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),a=[];
            while(w.nextNode())if(w.currentNode.textContent.trim())a.push(parseFloat(getComputedStyle(w.currentNode.parentElement).fontSize));return a;}""")
        self.assertTrue(fonts and min(fonts) >= 12)
        return fonts

    def recovered(self, operation):
        expect(
            self.main.get_by_text(COPY_ERROR[self.locale][OPERATIONS.index(operation)], exact=True)
        ).to_have_count(0)
        expect(self.input).to_have_value("" if operation in {"post", "patch"} else DRAFT)
        for row in self.http.rows:
            expect(self.panel.get_by_text(row["content"], exact=True)).to_have_count(1)
        expect(self.main.get_by_text(COPY_ERROR[self.locale][7], exact=True)).to_have_count(0)
        self.assertEqual(self.call_count(operation), 2)
        self.assertEqual(self.http.pending, [])

    def exercise(self, operation):
        self.start(operation)
        try:
            self.retained(operation)
        finally:
            self.save(operation, "pending")
        self.complete(operation, failure=True)
        copy = COPY_ERROR[self.locale][OPERATIONS.index(operation)]
        alert = self.main.get_by_role("alert").filter(has_text=copy)
        expect(alert.get_by_text(copy, exact=True)).to_be_visible()
        self.retained(operation)
        if operation in {"initial", "older", "delete"}:
            button = alert.get_by_role("button", name=COPY_ERROR[self.locale][5], exact=True)
            self.focus_retry(button, alert)
        else:
            self.retry_metrics = {"notice_fonts": self.notice_fonts(alert)}
        self.save(operation, "error")
        if operation == "delete":
            for control in (
                alert,
                alert.get_by_role("button", name=COPY_ERROR[self.locale][5], exact=True),
            ):
                bounds = control.evaluate("""el=>{
                    const r=el.getBoundingClientRect(),clips=[];
                    for(let n=el.parentElement;n;n=n.parentElement){
                        if(['hidden','clip','auto','scroll'].includes(getComputedStyle(n).overflowX)){
                            const b=n.getBoundingClientRect(),left=b.left+n.clientLeft;
                            clips.push({left,right:left+n.clientWidth});
                        }
                    }return {left:r.left,right:r.right,clips};
                }""")
                self.assertTrue(
                    bounds["clips"], "DELETE notice must have a measured clipping ancestor"
                )
                for clip in bounds["clips"]:
                    self.assertGreaterEqual(bounds["left"], clip["left"], bounds)
                    self.assertLessEqual(bounds["right"], clip["right"], bounds)
        if operation in {"post", "patch"}:
            self.input.press("Enter")
        else:
            self.page.keyboard.press("Enter")
        self.wait_pending()
        self.assertEqual(self.call_count(operation), 2)
        self.complete(operation)
        try:
            self.recovered(operation)
        finally:
            self.save(operation, "recovered")

    def matrix(self, group):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_chat(locale, width, group)
                        for operation in (
                            ("initial", "older") if group == "read" else ("post", "patch", "delete")
                        ):
                            self.exercise(operation)
                    finally:
                        self.doCleanups()

    def test_history_errors_and_explicit_retries(self):
        self.matrix("read")

    def test_write_errors_preserve_drafts_and_retry_targets(self):
        self.matrix("write")


if __name__ == "__main__":
    ProjectChatErrorTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
