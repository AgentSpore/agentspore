"""Exercise native project login lifecycle through synthetic outward HTTP only."""

import base64
import json
import re
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import PASSWORD, USER, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_chat_ownership import PROJECT_ID, OwnershipHTTP
from test_project_detail_reads import CONTRIBUTOR, OWNERSHIP_DATA, PROJECT_DATA, TARGET
from test_public_chat_accessibility import FOCUS

BASE = f"/api/v1/projects/{PROJECT_ID}"
TITLE = "Synthetic local login project"
SENTINEL = "Synthetic private authentication failure"
ACCOUNT_B = "00000000-0000-4000-8000-0000000000b1"
EMAILS = {"A": "synthetic-a@example.invalid", "B": "synthetic-b@example.invalid"}
NAME_B = "Synthetic confirmed B"
COPY = {
    "en": (
        "Contributors",
        "Request to join",
        "Sign in to continue",
        "Email",
        "Password",
        "close",
        "Sign in",
        "Verify your email before signing in.",
    ),
    "ru": (
        "Участники",
        "Подать заявку",
        "Войдите для продолжения",
        "Email",
        "Пароль",
        "закрыть",
        "Войти",
        "Ошибка соединения",
    ),
}


def synthetic_pair(actor):
    payload = {"sub": ACCOUNT_B if actor == "B" else "00000000-0000-4000-8000-0000000000a1", "email": EMAILS[actor]}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    return {
        "access_token": f"{header}.{encoded}.c3ludGhldGlj",
        "refresh_token": f"synthetic-refresh-{actor}",
        "token_type": "bearer",
    }


class LoginHTTP(OwnershipHTTP):
    def __init__(self, base_url):
        super().__init__(base_url, "project")
        self.logins, self.reads, self.profiles, self.aborted = [], [], [], 0

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if target.path == "/api/v1/auth/login" and request.method == "POST" and not target.query:
            return self.login(route)
        if request.method == "GET" and not target.query and target.path.startswith("/api/"):
            return self.read(route, target.path)
        return super().route(route)

    def login(self, route):
        request, body = route.request, route.request.post_data_json
        actor = next((key for key, email in EMAILS.items() if body == {"email": email, "password": PASSWORD}), None)
        if actor is None or request.headers.get("authorization") is not None:
            return self.reject(route)
        self.logins.append(
            {"actor": actor, "method": "POST", "path": "/api/v1/auth/login", "exact_body": True, "anonymous": True}
        )
        self.pending.append((route, actor))
        return None

    def read(self, route, path):
        header = route.request.headers.get("authorization")
        if path == "/api/v1/auth/me":
            if header != "Bearer " + synthetic_pair("B")["access_token"]:
                return self.reject(route)
            self.profiles.append("B")
            return route.fulfill(json=dict(USER, id=ACCOUNT_B, email=EMAILS["B"], name=NAME_B))
        if path == BASE + "/contributors":
            if header not in (None, "Bearer " + synthetic_pair("B")["access_token"]):
                return self.reject(route)
            self.reads.append("B" if header else "anonymous")
            return route.fulfill(json={"contributors": [CONTRIBUTOR], "total": 1})
        payloads = {
            BASE: dict(PROJECT_DATA, title=TITLE),
            BASE + "/ownership": dict(OWNERSHIP_DATA, project_title=TITLE),
        }
        if path not in payloads or header is not None:
            return self.reject(route)
        return route.fulfill(json=payloads[path])

    def failed(self, request):
        if urlparse(request.url).path == "/api/v1/auth/login":
            self.aborted += 1

    def count(self, key):
        return {
            "login": len(self.logins),
            "aborted": self.aborted,
            "profile": len(self.profiles),
            "contributors-B": self.reads.count("B"),
        }[key]


class ProjectLoginTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width):
        self.locale, self.width, self.errors, self.records, self.focus, self.targets = locale, width, [], [], [], []
        self.native_tabs = []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = LoginHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("requestfailed", self.http.failed)
        self.page.expose_function("__loginCount", self.http.count)
        self.page.goto(self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        expect(self.page.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        tab = self.page.get_by_role("button", name=re.compile("^" + COPY[locale][0] + r"(?:\s|$)"))
        self.native(tab, measure=False)
        self.page.keyboard.press("Enter")
        self.opener = self.main.get_by_role("button", name=COPY[locale][1], exact=True)
        expect(self.opener).to_be_visible()
        self.settle()

    def settle(self):
        self.page.evaluate("""async()=>{await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}""")

    def wait_count(self, key, expected):
        self.page.evaluate(
            """async([key,n])=>{const end=performance.now()+7000;
            while(await window.__loginCount(key)<n){if(performance.now()>end)throw Error('Expected local login HTTP state');
            await new Promise(requestAnimationFrame);}}""",
            [key, expected],
        )

    def native(self, target, measure=True):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        target.scroll_into_view_if_needed()
        self.settle()
        if measure:
            self.focus.append(target.evaluate(FOCUS))
            self.targets.append(target.evaluate(TARGET))

    def session_state(self, actor=None):
        return self.page.evaluate(
            """expected=>{const access=localStorage.getItem('access_token'),refresh=localStorage.getItem('refresh_token');
            const raw=localStorage.getItem('auth');const legacy=raw?JSON.parse(raw):null;
            return {access:access===expected?.access_token,refresh:refresh===expected?.refresh_token,
                legacy:legacy?.token===expected?.access_token&&legacy?.userId===expected?.sub&&legacy?.email===expected?.email,
                absent:!access&&!refresh&&!raw};}""",
            dict(synthetic_pair(actor), sub=ACCOUNT_B, email=EMAILS[actor]) if actor else None,
        )

    def save(self, state, surface=None):
        self.settle()
        root = surface if surface is not None else self.main
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": root.evaluate(METRICS) if surface is not None else None,
            "focus": list(self.focus),
            "native_tab_states": list(self.native_tabs),
            "targets": list(self.targets),
            "session": self.session_state("B"),
            "logins": list(self.http.logins),
            "contributor_reads": list(self.http.reads),
            "profile_reads": len(self.http.profiles),
            "aborted_login_requests": self.http.aborted,
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
        }
        self.records.append(record)
        stem = f"project-detail-login-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.page.get_by_text(SENTINEL, exact=False)).to_have_count(0)

    def open_modal(self, capture=False):
        self.native(self.opener, measure=False)
        self.page.keyboard.press("Enter")
        heading = self.page.get_by_role("heading", name=COPY[self.locale][2], exact=True)
        expect(heading).to_be_visible()
        self.dialog = self.page.get_by_role("dialog", name=COPY[self.locale][2], exact=True)
        if capture:
            surface = self.dialog if self.dialog.count() else heading.locator("../..")
            self.save("opened-observed", surface)
        expect(self.dialog).to_be_visible()
        self.assertTrue(self.dialog.evaluate("el=>el.tagName==='DIALOG'&&el.matches(':modal')"))
        self.email = self.dialog.get_by_label(COPY[self.locale][3], exact=True)
        self.password = self.dialog.get_by_label(COPY[self.locale][4], exact=True)
        expect(self.email).to_be_focused()
        expect(self.password).to_have_attribute("type", "password")
        expect(self.dialog.get_by_role("button", name=COPY[self.locale][5], exact=True)).to_be_visible()

    def keyboard_boundary(self):
        for key in ["Tab"] * 6 + ["Shift+Tab"]:
            self.page.keyboard.press(key)
            state = self.dialog.evaluate("""el=>({modal:el.matches(':modal'),inside:el.contains(document.activeElement),
                chrome:!document.hasFocus()&&document.activeElement===document.body})""")
            self.native_tabs.append(state)
            self.assertTrue(state["modal"] and (state["inside"] or state["chrome"]))
            if state["inside"]:
                active = self.page.locator(":focus")
                self.focus.append(active.evaluate(FOCUS))
                self.targets.append(active.evaluate(TARGET))
        self.assertFalse(self.opener.evaluate("el=>{el.focus();return document.activeElement===el}"))
        self.page.keyboard.press("Escape")
        expect(self.dialog).to_have_count(0)
        expect(self.opener).to_be_focused()
        self.assertTrue(self.session_state()["absent"])
        self.open_modal()

    def start_login(self, actor):
        self.email.fill(EMAILS[actor])
        self.password.fill(PASSWORD)
        expected = len(self.http.logins) + 1
        self.password.press("Enter")
        self.wait_count("login", expected)
        expect(self.dialog.locator('button[type="submit"]')).to_be_disabled()
        count = len(self.http.logins)
        self.page.keyboard.press("Enter")
        self.settle()
        self.assertEqual(len(self.http.logins), count)

    def complete(self, status):
        self.assertEqual(len(self.http.pending), 1)
        route, actor = self.http.pending.pop()
        if status == 0:
            route.abort("failed")
        elif status >= 400:
            route.fulfill(status=status, json={"detail": SENTINEL})
        else:
            route.fulfill(json=synthetic_pair(actor))
        self.settle()

    def failure_retry_and_abort(self):
        self.start_login("A")
        self.complete(403 if self.locale == "en" else 0)
        self.save("failed", self.dialog)
        expect(self.dialog.get_by_role("alert")).to_have_text(COPY[self.locale][7])
        expect(self.email).to_have_value(EMAILS["A"])
        expect(self.password).to_have_value(PASSWORD)
        expect(self.dialog.get_by_role("button", name=COPY[self.locale][6], exact=True)).to_be_enabled()
        self.start_login("A")
        self.save("retry-pending", self.dialog)
        before = self.http.aborted
        self.page.keyboard.press("Escape")
        expect(self.dialog).to_have_count(0)
        expect(self.opener).to_be_focused()
        self.wait_count("aborted", before + 1)
        self.assertTrue(self.session_state()["absent"])
        self.save("closed-aborted")

    def success_in_new_modal(self):
        self.open_modal()
        self.save("reopened", self.dialog)
        self.start_login("B")
        self.assertEqual([entry["actor"] for entry in self.http.logins], ["A", "A", "B"])
        active = [(route, actor) for route, actor in self.http.pending if actor == "B"]
        self.assertEqual(len(active), 1)
        route, actor = active[0]
        self.http.pending.remove((route, actor))
        route.fulfill(json=synthetic_pair("B"))
        expect(self.dialog).to_have_count(0)
        self.wait_count("profile", 2)
        self.wait_count("contributors-B", 1)
        expect(self.page.get_by_text(EMAILS["B"], exact=True)).to_be_visible()
        self.confirm_header()
        self.assertEqual(self.session_state("B"), {"access": True, "refresh": True, "legacy": True, "absent": False})
        self.save("authenticated-B")

    def confirm_header(self):
        header = self.page.locator("header")
        if self.width == 1280:
            expect(header.get_by_role("button").filter(has_text=NAME_B)).to_be_visible()
            return
        menu = header.get_by_role("button", name="Menu" if self.locale == "en" else "Меню", exact=True)
        self.native(menu, measure=False)
        self.page.keyboard.press("Enter")
        panel_id = menu.get_attribute("aria-controls")
        self.assertTrue(panel_id)
        panel = self.page.locator(f'[id="{panel_id}"]')
        expect(panel.get_by_text(NAME_B, exact=True)).to_be_visible()
        expect(panel.get_by_text(EMAILS["B"], exact=True)).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(menu).to_have_attribute("aria-expanded", "false")
        expect(menu).to_be_focused()

    def metric_issues(self, metrics):
        issues = [{"font": item} for item in metrics["text"] if item["size"] < 12]
        issues.extend(
            {"target": item}
            for item in metrics["targets"]
            if item["width"] < 44 or item["height"] < 44 or not item["contained"]
        )
        issues.extend({"clip": item} for item in metrics["clipped"])
        return issues

    def verify(self):
        issues = [
            issue for record in self.records if record["metrics"] for issue in self.metric_issues(record["metrics"])
        ]
        issues.extend(
            {"width": record["document_width"]} for record in self.records if record["document_width"] > self.width
        )
        issues.extend(
            {"focus": value} for value in self.focus if not all(value[key] for key in ("active", "visible", "painted"))
        )
        issues.extend(
            {"target": item}
            for item in self.targets
            if item["font"] < 12 or item["width"] < 44 or item["height"] < 44 or not item["contained"]
        )
        self.assertEqual(issues, [])

    def test_native_local_login_errors_close_abort_and_canonical_account(self):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        self.open_modal(capture=True)
                        self.keyboard_boundary()
                        self.failure_retry_and_abort()
                        self.success_in_new_modal()
                        self.verify()
                    finally:
                        self.doCleanups()


if __name__ == "__main__":
    ProjectLoginTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
