"""Recover current-account join requests and accepted contributor-list refreshes."""

import json
import re
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import USER, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_chat_ownership import PROJECT_ID, OwnershipHTTP
from test_project_detail_reads import CONTRIBUTOR, OWNERSHIP_DATA, PROJECT_DATA, TARGET
from test_public_chat_accessibility import FOCUS

BASE = f"/api/v1/projects/{PROJECT_ID}"
TITLE = "Synthetic join project"
MESSAGE = "I'd like to contribute to this project."
SENTINEL = "Synthetic private join failure sentinel"
AUTH = {
    "A": {
        "token": "synthetic-join-a",
        "email": "synthetic-a@example.invalid",
        "userId": "00000000-0000-4000-8000-0000000000a1",
    },
    "B": {
        "token": "synthetic-join-b",
        "email": "synthetic-b@example.invalid",
        "userId": "00000000-0000-4000-8000-0000000000b1",
    },
}
MEMBER_NAME = "Existing synthetic member"
MEMBER = dict(CONTRIBUTOR, user_name=MEMBER_NAME)
ROWS = {"contributors": [MEMBER], "total": 1}
COPY = {
    "en": (
        "Human Contributors",
        "Request to join",
        "Requesting...",
        "Could not request to join. Try again.",
        "Sign in again",
        "Your request is pending approval.",
        "Could not load the data. Check your connection and try again.",
        "Retry",
        "Contributors",
        "Sign in to continue",
    ),
    "ru": (
        "Участники-люди",
        "Подать заявку",
        "Отправка заявки...",
        "Не удалось отправить заявку. Попробуйте ещё раз.",
        "Войти снова",
        "Ваша заявка ожидает одобрения.",
        "Не удалось загрузить данные. Проверьте соединение и повторите попытку.",
        "Повторить",
        "Участники",
        "Войдите для продолжения",
    ),
}


class JoinHTTP(OwnershipHTTP):
    def __init__(self, base_url):
        super().__init__(base_url, "project")
        self.actor, self.hold_refresh, self.reads, self.joins, self.profiles = "A", False, [], [], []

    def principal(self, request):
        header = request.headers.get("authorization")
        if header is None and self.actor == "A" and not any(r["actor"] == "A" for r in self.reads):
            return "bootstrap"
        return self.actor if header == "Bearer " + AUTH[self.actor]["token"] else None

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if target.path == BASE + "/contributors/join" and request.method == "POST" and not target.query:
            if self.principal(request) != self.actor or request.post_data_json != {"message": MESSAGE}:
                return self.reject(route)
            self.joins.append(
                {
                    "method": "POST",
                    "path": target.path,
                    "actor": self.actor,
                    "body": request.post_data_json,
                    "authorization_matched": True,
                }
            )
            self.pending.append((route, "join"))
            return None
        if request.method == "GET":
            if not target.query:
                return self.read(route, target)
            if not target.path.startswith("/api/"):
                return super().route(route)
        return self.reject(route)

    def read(self, route, target):
        request = route.request
        if target.path == "/api/v1/auth/me":
            return self.read_profile(route)
        if target.path == BASE + "/contributors":
            return self.read_contributors(route)
        payloads = {
            BASE: dict(PROJECT_DATA, title=TITLE),
            BASE + "/ownership": dict(OWNERSHIP_DATA, project_title=TITLE),
        }
        if target.path in payloads:
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.reads.append({"kind": "project" if target.path == BASE else "ownership", "actor": None})
            return route.fulfill(json=payloads[target.path])
        if target.path.startswith("/api/"):
            return self.reject(route)
        return super().route(route)

    def read_profile(self, route):
        if self.principal(route.request) != self.actor:
            return self.reject(route)
        self.profiles.append(self.actor)
        return route.fulfill(json=dict(USER, id=AUTH[self.actor]["userId"], email=AUTH[self.actor]["email"]))

    def read_contributors(self, route):
        actor = self.principal(route.request)
        if actor is None:
            return self.reject(route)
        self.reads.append({"kind": "contributors", "actor": actor, "authorization_matched": actor != "bootstrap"})
        if self.hold_refresh:
            self.pending.append((route, "contributors"))
        else:
            route.fulfill(json=ROWS)
        return None

    def count(self, key):
        if key in ("join", "contributors"):
            return sum(kind == key for _, kind in self.pending)
        if key.startswith("profile-"):
            return self.profiles.count(key[-1])
        return sum(row["kind"] == "contributors" and row["actor"] == key for row in self.reads)


class ProjectJoinTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width):
        self.locale, self.width, self.errors, self.records, self.focus, self.targets = locale, width, [], [], [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.add_init_script("localStorage.setItem('auth', " + json.dumps(json.dumps(AUTH["A"])) + ")")
        self.http = JoinHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__joinCount", self.http.count)
        self.page.goto(self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        expect(self.page.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.wait_count("A")
        self.wait_count("profile-A")
        expect(self.page.get_by_text(AUTH["A"]["email"], exact=True)).to_be_visible()
        tab = self.page.get_by_role("button", name=re.compile("^" + COPY[locale][8] + r"(?:\s|$)"))
        self.native(tab, measure=False)
        self.page.keyboard.press("Enter")
        self.settle()
        self.header = self.main.get_by_text(COPY[locale][0], exact=True).locator("..")
        expect(self.main.get_by_text(MEMBER_NAME, exact=True)).to_be_visible()

    def wait_count(self, key, expected=1):
        self.page.evaluate(
            """async ([key,n])=>{const end=performance.now()+7000;while(await window.__joinCount(key)<n){
            if(performance.now()>end)throw Error('Expected join HTTP boundary state');await new Promise(requestAnimationFrame);}}""",
            [key, expected],
        )

    def settle(self):
        self.page.evaluate("""async()=>{await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);} """)

    def place(self, root):
        root.scroll_into_view_if_needed()
        root.evaluate(
            "el=>{const h=document.querySelector('header')?.getBoundingClientRect().bottom||0;window.scrollBy(0,el.getBoundingClientRect().top-h-16)}"
        )
        self.settle()

    def native(self, target, measure=True):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.place(target)
        if measure:
            self.focus.append(target.evaluate(FOCUS))
            self.targets.append(target.evaluate(TARGET))

    def button(self, index=1):
        return self.main.get_by_role("button", name=COPY[self.locale][index], exact=True)

    def save(self, state):
        self.settle()
        self.place(self.header)
        roots = [self.header]
        for index in (3, 4, 5, 6):
            message = self.main.get_by_text(COPY[self.locale][index], exact=True)
            if message.count() == 1:
                roots.append(message)
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": [root.evaluate(METRICS) for root in roots],
            "focus": list(self.focus),
            "targets": list(self.targets),
            "actor": self.http.actor,
            "reads": list(self.http.reads),
            "joins": list(self.http.joins),
            "profiles": list(self.http.profiles),
            "known_row": self.main.get_by_text(MEMBER_NAME, exact=True).count(),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"project-detail-join-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.main.get_by_text(SENTINEL, exact=False)).to_have_count(0)

    def start_join(self):
        before = len(self.http.joins)
        self.native(self.button())
        self.page.keyboard.press("Enter")
        self.wait_count("join")
        expect(self.button(2)).to_be_disabled()
        self.page.keyboard.press("Enter")
        self.settle()
        self.assertEqual(len(self.http.joins), before + 1)
        expect(self.main.get_by_text(MEMBER_NAME, exact=True)).to_be_visible()

    def complete(self, kind, status=200):
        self.wait_count(kind)
        matches = [(route, marker) for route, marker in self.http.pending if marker == kind]
        self.assertEqual(len(matches), 1)
        route, marker = matches[0]
        self.http.pending.remove((route, marker))
        if status == 0:
            route.abort("failed")
        elif status >= 400:
            route.fulfill(status=status, json={"detail": SENTINEL})
        elif kind == "join":
            route.fulfill(
                json={"status": "pending_approval", "message": "Your request is pending approval from 1 contributor(s)"}
            )
        else:
            route.fulfill(json=ROWS)
        self.settle()

    def first_failure(self):
        self.save("initial")
        before = list(self.http.reads)
        self.start_join()
        self.save("join-pending")
        self.complete("join", 503 if self.locale == "en" else 0)
        self.save("join-error-observed")
        self.assertEqual(self.http.reads, before)
        expect(self.main.get_by_text(COPY[self.locale][3], exact=True)).to_be_visible()
        expect(self.main.get_by_text(COPY[self.locale][5], exact=True)).to_have_count(0)
        expect(self.button()).to_be_enabled()

    def accepted_refresh(self):
        self.start_join()
        self.http.hold_refresh = True
        before_reads = len(self.http.reads)
        self.complete("join")
        self.wait_count("contributors")
        self.assertEqual(len(self.http.reads), before_reads + 1)
        self.assertEqual(self.http.reads[-1]["actor"], "A")
        expect(self.main.get_by_text(COPY[self.locale][5], exact=True)).to_be_visible()
        self.complete("contributors", 503)
        alert = self.main.get_by_role("alert").filter(has_text=COPY[self.locale][6])
        retry = alert.get_by_role("button", name=COPY[self.locale][7], exact=True)
        self.native(retry)
        self.save("accepted-refresh-error")
        expect(self.main.get_by_text(COPY[self.locale][5], exact=True)).to_be_visible()
        expect(self.main.get_by_text(MEMBER_NAME, exact=True)).to_be_visible()
        before_joins = list(self.http.joins)
        self.page.keyboard.press("Enter")
        self.wait_count("contributors")
        self.assertEqual(self.http.joins, before_joins)
        self.assertEqual(self.http.reads[-1]["actor"], "A")
        self.complete("contributors")
        expect(alert).to_have_count(0)
        self.save("accepted-refresh-recovered")
        expect(self.main.get_by_text(COPY[self.locale][5], exact=True)).to_be_visible()

    def replace_account(self):
        self.http.actor, self.http.hold_refresh = "B", False
        self.page.evaluate(
            "value=>{localStorage.setItem('auth',JSON.stringify(value));window.dispatchEvent(new Event('storage'));}",
            AUTH["B"],
        )
        self.wait_count("B")
        self.wait_count("profile-B")
        expect(self.page.get_by_text(AUTH["B"]["email"], exact=True)).to_be_visible()
        expect(self.main.get_by_text(COPY[self.locale][5], exact=True)).to_have_count(0)
        expect(self.button()).to_be_visible()
        expect(self.main.get_by_text(MEMBER_NAME, exact=True)).to_be_visible()

    def reauthentication(self):
        self.start_join()
        self.assertEqual(self.http.joins[-1]["actor"], "B")
        self.complete("join", 401)
        target = self.button(4)
        self.native(target)
        self.save("sign-in-required")
        before = list(self.http.joins)
        self.page.keyboard.press("Enter")
        expect(self.page.get_by_role("heading", name=COPY[self.locale][9], exact=True)).to_be_visible()
        self.assertEqual(self.http.joins, before)
        self.assertEqual([request["actor"] for request in self.http.joins], ["A", "A", "B"])

    def metric_issues(self, metrics):
        issues = [{"font": item} for item in metrics["text"] if item["size"] < 12]
        issues.extend(
            {"target": item}
            for item in metrics["targets"]
            if item["width"] < 44 or item["height"] < 44 or not item["contained"]
        )
        issues.extend({"clip": item} for item in metrics["clipped"])
        if metrics["document_width"] > self.width:
            issues.append({"document_width": metrics["document_width"]})
        return issues

    def verify(self):
        issues = [
            issue for record in self.records for metrics in record["metrics"] for issue in self.metric_issues(metrics)
        ]
        issues.extend(
            {"focus": value} for value in self.focus if not all(value[key] for key in ("active", "visible", "painted"))
        )
        for target in self.targets:
            if (
                target["font"] < 12
                or target["width"] < 44
                or target["height"] < 44
                or not target["contained"]
                or not target["in_view"]
            ):
                issues.append({"target": target})
        self.assertEqual(issues, [])

    def test_join_failures_and_accepted_refresh_belong_to_current_account(self):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        self.first_failure()
                        self.accepted_refresh()
                        self.replace_account()
                        self.reauthentication()
                        self.verify()
                    finally:
                        self.doCleanups()


if __name__ == "__main__":
    ProjectJoinTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
