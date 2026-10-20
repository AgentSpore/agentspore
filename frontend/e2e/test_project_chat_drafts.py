"""Preserve real project Discussion drafts across held POST/PATCH success."""

import json
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_chat_ownership import (
    ACCOUNT_A,
    COPY,
    DISPLAY,
    OwnershipHTTP,
    PROJECT_ID,
    synthetic_session,
)
from test_public_chat_states import STREAM

SUBMITTED = "Submitted synthetic project draft"
NEXT_DRAFT = "A newer normal project draft"
PATH = f"/api/v1/chat/project/{PROJECT_ID}/human-messages"
EDITING = {"en": "Editing message", "ru": "Редактирование сообщения"}


class ProjectDraftHTTP(OwnershipHTTP):
    """Reuse accepted project reads and hold only the exact synthetic mutation."""

    def __init__(self, base_url):
        super().__init__(base_url, "project")
        self.writes = []

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/auth/me" and not target.query:
            if request.headers.get("authorization") != "Bearer " + synthetic_session("initial"):
                return self.reject(route)
            route.fulfill(json=dict(USER, id=ACCOUNT_A, name=DISPLAY))
            return
        if request.method in {"POST", "PATCH"}:
            return self.hold_write(route)
        return super().route(route)

    def hold_write(self, route):
        request, target = route.request, urlparse(route.request.url)
        expected_path = PATH + ("/ownership-a" if request.method == "PATCH" else "")
        expected_body = {"content": SUBMITTED}
        if request.method == "POST":
            expected_body.update(name=DISPLAY, message_type="text")
        if (
            target.path != expected_path
            or target.query
            or request.headers.get("authorization") != "Bearer " + synthetic_session("initial")
            or request.post_data_json != expected_body
        ):
            return self.reject(route)
        self.writes.append(
            {
                "method": request.method,
                "path": target.path,
                "body": request.post_data_json,
                "synthetic_auth_matched": True,
            }
        )
        self.pending.append((route, request.method))


class ProjectChatDraftTests(BrowserFixture, unittest.TestCase):
    """Use the actual editor/composer, keyboard sends and delayed outward responses."""

    options: Namespace

    def open_chat(self, locale, width):
        self.locale, self.width, self.errors = locale, width, []
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
        self.http = ProjectDraftHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__pendingProjectWrites", lambda: len(self.http.pending))
        self.page.goto(
            self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded"
        )
        self.page.get_by_role("button", name=COPY[locale][2], exact=True).click()
        self.main = self.page.locator("#main-content")
        self.input = self.main.locator("form textarea")
        expect(self.input).to_be_visible()
        expect(
            self.row("ownership-a").get_by_role("button", name=COPY[locale][0], exact=True)
        ).to_have_count(1)

    def row(self, content):
        return self.main.get_by_text(content, exact=True).locator(
            "xpath=ancestor::div[contains(concat(' ', @class, ' '), ' group ')][1]"
        )

    def wait_pending(self):
        self.page.evaluate("""async () => {
            const deadline=performance.now()+7000;
            while(await window.__pendingProjectWrites()!==1) {
                if(performance.now()>deadline)throw Error('Expected one held project write');
                await new Promise(requestAnimationFrame);
            }
        }""")
        self.assertEqual(len(self.http.writes), 1)

    def save(self, scenario, state):
        self.page.evaluate("document.fonts.ready")
        record = {
            "locale": self.locale,
            "width": self.width,
            "scenario": scenario,
            "state": state,
            "draft": self.input.input_value(),
            "editing": self.main.get_by_text(EDITING[self.locale], exact=True).count() == 1,
            "writes": self.http.writes,
            "held_writes": len(self.http.pending),
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
        }
        stem = f"project-draft-{self.locale}-{self.width}-{scenario}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertLessEqual(record["document_width"], self.width)

    def complete(self, method):
        route, actual_method = self.http.pending.pop()
        self.assertEqual(actual_method, method)
        with self.page.expect_request_finished(lambda request: request == route.request):
            route.fulfill(json={"status": "ok", "message_id": "ownership-a"})
        self.page.evaluate("""async () => {
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
        }""")

    def exercise(self, scenario):
        method = "POST" if scenario == "post-aba" else "PATCH"
        editing = method == "PATCH"
        if editing:
            self.row("ownership-a").get_by_role(
                "button", name=COPY[self.locale][0], exact=True
            ).click()
            expect(self.input).to_have_value("ownership-a")
        self.input.fill(SUBMITTED)
        self.save(scenario, "before")
        self.input.press("Enter")
        self.wait_pending()
        if scenario == "patch-escape":
            self.input.press("Escape")
            self.input.fill(NEXT_DRAFT)
        else:
            self.input.fill("Intermediate synthetic draft")
            self.input.fill(SUBMITTED)
        self.input.press("Enter")
        self.page.evaluate("""async () => {
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
        }""")
        self.assertEqual(len(self.http.writes), 1)
        expected = {
            "method": method,
            "path": PATH + ("/ownership-a" if editing else ""),
            "body": {"content": SUBMITTED},
            "synthetic_auth_matched": True,
        }
        if method == "POST":
            expected["body"].update(name=DISPLAY, message_type="text")
        self.assertEqual(self.http.writes, [expected])
        self.save(scenario, "pending")
        self.complete(method)
        try:
            expect(self.input).to_have_value(
                NEXT_DRAFT if scenario == "patch-escape" else SUBMITTED
            )
            expect(self.main.get_by_text(EDITING[self.locale], exact=True)).to_have_count(
                1 if scenario == "patch-aba" else 0
            )
            if editing:
                expect(self.row(SUBMITTED)).to_have_count(1)
                expect(self.main.get_by_text("ownership-a", exact=True)).to_have_count(0)
                expect(self.main.get_by_text("ownership-b", exact=True)).to_have_count(1)
            self.assertEqual(self.http.pending, [])
            self.assertEqual(len(self.http.writes), 1)
        finally:
            self.save(scenario, "after-observed")

    def matrix(self, scenario):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_chat(locale, width)
                        self.exercise(scenario)
                    finally:
                        self.doCleanups()

    def test_post_success_preserves_same_text_new_revision(self):
        self.matrix("post-aba")

    def test_patch_success_updates_row_preserving_same_text_editor(self):
        self.matrix("patch-aba")

    def test_patch_success_preserves_normal_draft_after_escape(self):
        self.matrix("patch-escape")


if __name__ == "__main__":
    ProjectChatDraftTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
