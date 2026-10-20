"""Keep chat account verification distinct from sign-out and recover at HTTP boundary."""

import json
import re
import unittest
from argparse import Namespace

from auth_form_fixtures import BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_chat_ownership import (
    ACCOUNT_A,
    AUTHOR_ROWS,
    COPY as ACTION_COPY,
    OwnershipHTTP,
    PROJECT_ID,
    synthetic_session,
)
from test_public_chat_accessibility import FOCUS
from test_public_chat_states import STREAM

COPY = {
    "en": (
        "Checking your account…",
        "Could not verify your account.",
        "Retry",
        "Sign in to join the conversation",
        "Want to join the discussion?",
    ),
    "ru": (
        "Проверяем аккаунт…",
        "Не удалось проверить аккаунт.",
        "Повторить",
        "Войдите, чтобы участвовать в разговоре",
        "Хотите участвовать в обсуждении?",
    ),
}


class ChatProfileStateTests(BrowserFixture, unittest.TestCase):
    """Hold Header and chat-profile reads, then retry only the chat hook."""

    options: Namespace

    def open_chat(self, kind, locale, width):
        self.kind, self.locale, self.width = kind, locale, width
        self.errors, self.retry_focus = [], None
        self.context = self.browser.new_context(
            viewport={"width": width, "height": 900}, service_workers="block"
        )
        self.context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.context.add_init_script(STREAM)
        self.context.add_init_script(
            "localStorage.setItem('access_token', " + json.dumps(synthetic_session("initial")) + ")"
        )
        self.http = OwnershipHTTP(self.options.base_url, kind)
        self.addCleanup(self.http.close, self.context)
        self.context.route("**/*", self.http.route)
        self.page = self.context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldProfiles", lambda: len(self.http.pending))
        path = "/chat" if kind == "public" else f"/projects/{PROJECT_ID}"
        self.page.goto(self.options.base_url + path, wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        if kind == "project":
            self.page.get_by_role("button", name=ACTION_COPY[locale][2], exact=True).click()
        expect(self.main.get_by_text("ownership-a", exact=True)).to_be_visible()
        self.wait_held(2)
        self.assertEqual(len(self.http.profile_requests), 2)

    def wait_held(self, count):
        self.page.evaluate(
            """async count => {
            const deadline=performance.now()+7000;
            while(await window.__heldProfiles()!==count) {
                if(performance.now()>deadline)throw Error('Unexpected held profile count');
                await new Promise(requestAnimationFrame);
            }
        }""",
            count,
        )

    def row(self, identifier):
        return self.main.get_by_text(identifier, exact=True).locator(
            "xpath=ancestor::div[contains(concat(' ', @class, ' '), ' group ')][1]"
        )

    def composer(self):
        return (
            self.page.locator("form textarea")
            if self.kind == "public"
            else self.main.locator("form textarea")
        )

    def notice(self, state):
        text = COPY[self.locale][0 if state == "checking" else 1]
        role = "status" if state == "checking" else "alert"
        pattern = re.compile(r"^\s*" + re.escape(text) + r"\s*$") if state == "checking" else text
        return self.page.get_by_role(role).filter(has_text=pattern)

    def assert_unconfirmed(self):
        expect(self.composer()).to_have_count(0)
        for author in AUTHOR_ROWS:
            for label in ACTION_COPY[self.locale][:2]:
                expect(
                    self.row(author["id"]).get_by_role("button", name=label, exact=True)
                ).to_have_count(0)
        signed_out = COPY[self.locale][3 if self.kind == "public" else 4]
        scope = self.page if self.kind == "public" else self.main
        expect(scope.get_by_text(signed_out, exact=True)).to_have_count(0)

    def save(self, state, failure):
        self.page.evaluate("document.fonts.ready")
        actions = []
        for author in AUTHOR_ROWS:
            for label in ACTION_COPY[self.locale][:2]:
                if self.row(author["id"]).get_by_role("button", name=label, exact=True).count():
                    actions.append({"id": author["id"], "action": label})
        record = {
            "kind": self.kind,
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "failure": failure,
            "profile_request_count": len(self.http.profile_requests),
            "held_profiles": len(self.http.pending),
            "retry_focus": self.retry_focus,
            "checking": self.notice("checking").all_text_contents(),
            "error": self.notice("error").all_text_contents(),
            "action_rows": actions,
            "composer_count": self.composer().count(),
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
        }
        stem = f"chat-profile-{self.kind}-{self.locale}-{self.width}-{failure}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertLessEqual(record["document_width"], self.width)

    def fail_initial(self, failure):
        while self.http.pending:
            route, _epoch = self.http.pending.pop(0)
            if failure == "http":
                route.fulfill(status=503, json={"detail": "Synthetic account verification failed"})
            else:
                route.abort("failed")
        error = self.notice("error")
        expect(error.get_by_text(COPY[self.locale][1], exact=True)).to_be_visible()
        expect(self.notice("checking")).to_have_count(0)
        self.assert_unconfirmed()
        return error.get_by_role("button", name=COPY[self.locale][2], exact=True)

    def native_retry(self, button):
        for _ in range(self.page.locator("a[href],button,input,textarea").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if button.evaluate("el=>el===document.activeElement"):
                break
        expect(button).to_be_focused()
        self.retry_focus = button.evaluate(FOCUS)
        box = button.bounding_box()
        self.assertIsNotNone(box)
        if box is None:
            self.fail("Focused Retry has no bounding box")
        self.retry_focus["box"] = box
        self.assertTrue(all(self.retry_focus[key] for key in ("active", "visible", "painted")))
        self.assertGreaterEqual(box["width"], 44)
        self.assertGreaterEqual(box["height"], 44)
        fonts = self.notice("error").evaluate("""el => {
            const w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),sizes=[];
            while(w.nextNode())if(w.currentNode.textContent.trim())
                sizes.push(parseFloat(getComputedStyle(w.currentNode.parentElement).fontSize));
            return sizes;
        }""")
        self.retry_focus["notice_fonts"] = fonts
        self.assertTrue(fonts and min(fonts) >= 12)
        self.page.keyboard.press("Enter")
        self.wait_held(1)
        self.assertEqual(len(self.http.profile_requests), 3)
        expect(self.notice("checking")).to_be_visible()
        expect(self.notice("error")).to_have_count(0)
        self.assert_unconfirmed()

    def resolve_ready(self):
        route, epoch = self.http.pending.pop()
        self.assertEqual(epoch, "initial")
        route.fulfill(json=dict(USER, id=ACCOUNT_A, name="Synthetic verified owner"))
        expect(self.composer()).to_be_visible()
        expect(self.notice("checking")).to_have_count(0)
        expect(self.notice("error")).to_have_count(0)
        for author in AUTHOR_ROWS:
            for label in ACTION_COPY[self.locale][:2]:
                control = self.row(author["id"]).get_by_role("button", name=label, exact=True)
                expect(control).to_have_count(1 if author.get("sender_user_id") == ACCOUNT_A else 0)
        self.assertEqual(len(self.http.profile_requests), 3)
        self.assertEqual(self.http.pending, [])

    def exercise(self, failure):
        self.save("initial-observed", failure)
        expect(self.notice("checking")).to_be_visible()
        expect(
            self.notice("checking").get_by_text(COPY[self.locale][0], exact=True)
        ).to_be_visible()
        self.assert_unconfirmed()
        button = self.fail_initial(failure)
        self.save("error", failure)
        self.native_retry(button)
        self.save("retry-checking", failure)
        self.resolve_ready()
        self.save("ready", failure)

    def test_profile_check_failure_and_native_retry_restore_chat(self):
        for kind in ("public", "project"):
            for locale in COPY:
                for width in (320, 390, 1280):
                    for failure in ("http", "network"):
                        with self.subTest(kind=kind, locale=locale, width=width, failure=failure):
                            try:
                                self.open_chat(kind, locale, width)
                                self.exercise(failure)
                            finally:
                                self.doCleanups()


if __name__ == "__main__":
    ChatProfileStateTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
