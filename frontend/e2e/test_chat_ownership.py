"""Require server-confirmed author identity in both public and project chat."""

import base64
import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_detail_width import PROJECT_ID, RESOURCES
from test_public_chat_states import STREAM

ACCOUNT_A = "00000000-0000-4000-8000-0000000000a1"
ACCOUNT_B = "00000000-0000-4000-8000-0000000000b1"
DISPLAY = "Shared synthetic name"
AUTHOR_ROWS = [
    {"id": "ownership-a", "sender_user_id": ACCOUNT_A},
    {"id": "ownership-b", "sender_user_id": ACCOUNT_B},
    {"id": "ownership-null", "sender_user_id": None},
    {"id": "ownership-missing"},
]
COPY = {
    "en": ("Edit", "Delete", "Discussion"),
    "ru": ("Изменить", "Удалить", "Обсуждение"),
}
PROJECT_RESOURCE, OWNERSHIP_RESOURCE = RESOURCES["project"], RESOURCES["ownership"]
if not isinstance(PROJECT_RESOURCE, dict) or not isinstance(OWNERSHIP_RESOURCE, dict):
    fixture_error = "Expected mapping fixtures"
    raise TypeError(fixture_error)
OWNERSHIP = dict(OWNERSHIP_RESOURCE, contributors=[])
PROJECT = dict(
    PROJECT_RESOURCE,
    title="Synthetic ownership project",
    description="Synthetic chat authorization",
    tech_stack=[],
    agent_name="Synthetic agent",
    deploy_url=None,
    repo_url=None,
)


def synthetic_session(epoch):
    """Deliberately claim B locally while the server may confirm A."""
    payload = base64.b64encode(json.dumps({"sub": ACCOUNT_B}).encode()).decode()
    return f"synthetic.{payload}.{epoch}"


def chat_rows(kind):
    """Use the actual public/project history DTOs in newest-first API order."""
    rows = []
    for author in AUTHOR_ROWS:
        row = dict(author, content=author["id"], message_type="text", sender_type="user")
        if kind == "public":
            row.update(
                agent_id=None, agent_name=DISPLAY, specialization="user", ts="2026-10-20T09:00:00Z"
            )
        else:
            row.update(sender_name=DISPLAY, sender_handle=None, created_at="2026-10-20T09:00:00Z")
        rows.append(row)
    return list(reversed(rows))


class OwnershipHTTP(AuthHTTP):
    """Hold real profile reads and allow only the two inspected chat read contracts."""

    def __init__(self, base_url, kind):
        super().__init__(base_url)
        self.kind = kind
        self.profile_requests = []

    def accepts_page(self, request, target):
        pages = {"/chat", f"/projects/{PROJECT_ID}"}
        if request.method == "GET" and target.path in pages and not target.query:
            return True
        prefetch = f"/agents/{PROJECT['creator_agent_id']}"
        query = parse_qs(target.query)
        if (
            request.method == "GET"
            and target.path == prefetch
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and set(query) == {"_rsc"}
            and all(len(v) == 1 for v in query.values())
        ):
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method != "GET":
            return self.reject(route)
        if target.path == "/api/v1/auth/me" and not target.query:
            session = request.headers.get("authorization", "").removeprefix("Bearer ")
            epoch = session.rsplit(".", 1)[-1]
            if epoch not in {
                "initial",
                "renamed",
                "stale-switch",
                "replacement",
                "stale-logout",
            } or session != synthetic_session(epoch):
                return self.reject(route)
            self.profile_requests.append(epoch)
            self.pending.append((route, epoch))
            return
        history = (
            "/api/v1/chat/messages"
            if self.kind == "public"
            else f"/api/v1/chat/project/{PROJECT_ID}/messages"
        )
        if target.path == history and target.query == "limit=50":
            route.fulfill(json=chat_rows(self.kind))
            return
        responses = {
            f"/api/v1/projects/{PROJECT_ID}": PROJECT,
            f"/api/v1/projects/{PROJECT_ID}/ownership": OWNERSHIP,
            f"/api/v1/projects/{PROJECT_ID}/contributors": {"contributors": []},
        }
        if self.kind == "project" and target.path in responses and not target.query:
            route.fulfill(json=responses[target.path])
            return
        super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop()[0].abort("failed")
        context.close()


class ChatOwnershipTests(BrowserFixture, unittest.TestCase):
    """Verify per-message authorship across profile confirmation and session changes."""

    options: Namespace

    def open_chat(self, kind, locale, width):
        self.kind, self.locale, self.width = kind, locale, width
        self.errors, self.confirmed = [], None
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
        self.http = OwnershipHTTP(self.options.base_url, kind)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function(
            "__ownershipProfiles",
            lambda key: sum(epoch == key for _route, epoch in self.http.pending),
        )
        path = "/chat" if kind == "public" else f"/projects/{PROJECT_ID}"
        self.page.goto(self.options.base_url + path, wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        if kind == "project":
            self.page.get_by_role("button", name=COPY[locale][2], exact=True).click()
        expect(self.main.get_by_text("ownership-a", exact=True)).to_be_visible()
        self.wait_profiles("initial")

    def wait_profiles(self, epoch):
        self.page.evaluate(
            """async key => {
            const deadline = performance.now() + 7000;
            while (await window.__ownershipProfiles(key) < 2) {
                if (performance.now() > deadline) throw Error('Expected Header and chat profile reads');
                await new Promise(requestAnimationFrame);
            }
        }""",
            epoch,
        )

    def resolve_profiles(self, epoch, owner, name=DISPLAY):
        selected = [(route, key) for route, key in self.http.pending if key == epoch]
        self.assertGreaterEqual(len(selected), 2)
        for route, key in selected:
            self.http.pending.remove((route, key))
            route.fulfill(json=dict(USER, id=owner, name=name))
        self.confirmed = {"id": owner, "name": name}
        self.page.evaluate("""async () => {
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
        }""")

    def switch_session(self, epoch):
        self.confirmed = None
        self.page.evaluate(
            """session => {
            localStorage.removeItem('auth'); localStorage.removeItem('access_token');
            if (session) {
                localStorage.setItem('access_token', session);
                localStorage.setItem('auth', JSON.stringify({token:session,
                    email:'claimed@example.invalid',userId:'00000000-0000-4000-8000-0000000000b1'}));
            }
            window.dispatchEvent(new Event('storage'));
        }""",
            synthetic_session(epoch) if epoch else None,
        )
        if epoch:
            self.wait_profiles(epoch)

    def row(self, identifier):
        return self.main.get_by_text(identifier, exact=True).locator(
            "xpath=ancestor::div[contains(concat(' ', @class, ' '), ' group ')][1]"
        )

    def assert_owner(self, owner):
        for author in AUTHOR_ROWS:
            expected = bool(owner and author.get("sender_user_id") == owner)
            for label in COPY[self.locale][:2]:
                expect(
                    self.row(author["id"]).get_by_role("button", name=label, exact=True)
                ).to_have_count(1 if expected else 0)
        expect(self.main.get_by_text(DISPLAY, exact=True)).to_have_count(4)

    def save(self, state, owner):
        self.assert_owner(owner)
        row = next(
            (a for a in AUTHOR_ROWS if owner and a.get("sender_user_id") == owner), AUTHOR_ROWS[0]
        )
        self.row(row["id"]).hover()
        if owner:
            control = self.row(row["id"]).get_by_role(
                "button", name=COPY[self.locale][0], exact=True
            )
            expect(control.locator("..")).to_have_css("opacity", "1")
        actions = []
        for author in AUTHOR_ROWS:
            buttons = self.row(author["id"]).get_by_role(
                "button", name=COPY[self.locale][0], exact=True
            )
            visible = buttons.evaluate_all("""els => els.some(el => {
                if (!el.getClientRects().length) return false;
                for (let n=el;n;n=n.parentElement) {
                    if (Number(getComputedStyle(n).opacity) === 0) return false;
                }
                return true;
            })""")
            if visible:
                actions.append(author["id"])
        self.page.evaluate("document.fonts.ready")
        stem = f"ownership-{self.kind}-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        record = {
            "route": self.kind,
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "fixture_confirmed_profile": self.confirmed,
            "pending_profiles": [epoch for _route, epoch in self.http.pending],
            "visible_action_row_ids": actions,
            "grouping_labels": self.main.get_by_text(DISPLAY, exact=True).all_text_contents(),
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
        }
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(actions, [row["id"]] if owner else [])
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertLessEqual(record["document_width"], self.width)

    def exercise_identity(self):
        self.save("initial-pending", None)
        self.resolve_profiles("initial", ACCOUNT_A)
        self.save("confirmed-a", ACCOUNT_A)
        self.switch_session("renamed")
        self.assert_owner(None)
        self.resolve_profiles("renamed", ACCOUNT_A, "Renamed synthetic owner")
        self.save("renamed-a", ACCOUNT_A)
        self.switch_session("stale-switch")
        self.assert_owner(None)
        self.switch_session("replacement")
        self.resolve_profiles("stale-switch", ACCOUNT_A)
        self.confirmed = None
        self.save("replacement-pending", None)
        self.resolve_profiles("replacement", ACCOUNT_B)
        self.save("confirmed-b", ACCOUNT_B)
        self.switch_session("stale-logout")
        self.switch_session(None)
        self.resolve_profiles("stale-logout", ACCOUNT_A)
        self.confirmed = None
        self.save("logout-stale-profile", None)

    def test_only_confirmed_message_author_controls_survive_identity_changes(self):
        for kind in ("public", "project"):
            for locale in COPY:
                for width in (320, 390, 1280):
                    with self.subTest(kind=kind, locale=locale, width=width):
                        try:
                            self.open_chat(kind, locale, width)
                            self.exercise_identity()
                        finally:
                            self.doCleanups()


if __name__ == "__main__":
    ChatOwnershipTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
