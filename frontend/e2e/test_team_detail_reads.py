"""Recover anonymous team detail reads through the actual route and native controls."""

import json
import re
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_project_detail_reads import TARGET
from test_public_chat_accessibility import FOCUS

TEAM_ID = "00000000-0000-4000-8000-000000000041"
MISSING_ID = "00000000-0000-4000-8000-000000000042"
AGENT_ID = "00000000-0000-4000-8000-000000000043"
PROJECT_ID = "00000000-0000-4000-8000-000000000044"
TITLE, OWNER, PROJECT = "Synthetic readable team", "Synthetic Team Owner", "Synthetic Linked Project"
SENTINEL = "Synthetic internal team failure"
DETAIL = f"/teams/{TEAM_ID}"
MISSING = f"/teams/{MISSING_ID}"
API = f"/api/v1/teams/{TEAM_ID}"
TEAM = {
    "id": TEAM_ID,
    "name": TITLE,
    "description": "Synthetic team overview.",
    "avatar_url": None,
    "creator_name": OWNER,
    "created_at": "2026-10-20T09:00:00Z",
    "members": [
        {
            "id": "synthetic-membership",
            "agent_id": AGENT_ID,
            "user_id": None,
            "name": OWNER,
            "handle": "synthetic-owner",
            "role": "owner",
            "member_type": "agent",
            "joined_at": "2026-10-20T09:00:00Z",
        }
    ],
    "projects": [
        {
            "id": PROJECT_ID,
            "title": PROJECT,
            "description": "Synthetic linked project.",
            "status": "active",
            "repo_url": None,
            "deploy_url": None,
            "agent_name": "Synthetic agent",
        }
    ],
}
COPY = {
    "en": (
        "Loading team",
        "Could not load the data. Check your connection and try again.",
        "Retry",
        "Team not found",
        "Back to teams",
        "members (1)",
        "Projects (1)",
    ),
    "ru": (
        "Загрузка команды",
        "Не удалось загрузить данные. Проверьте соединение и повторите попытку.",
        "Повторить",
        "Команда не найдена",
        "Вернуться к командам",
        "участников (1)",
        "Проекты (1)",
    ),
}


class TeamReadHTTP(AuthHTTP):
    """Hold only team detail GETs; all API and SSE traffic stays synthetic."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.calls = []

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path in {DETAIL, MISSING} and not target.query:
            return True
        query = parse_qs(target.query, keep_blank_values=True)
        if (
            request.method == "GET"
            and target.path in {f"/agents/{AGENT_ID}", f"/projects/{PROJECT_ID}"}
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and set(query) == {"_rsc"}
            and all(len(value) == 1 for value in query.values())
        ):
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin or request.method != "GET":
            return self.reject(route)
        if target.path.startswith("/api/"):
            return self.api(route, target)
        return super().route(route)

    def api(self, route, target):
        request = route.request
        if request.headers.get("authorization") is not None:
            return self.reject(route)
        key = target.path + (f"?{target.query}" if target.query else "")
        self.calls.append(key)
        if target.path in {API, f"/api/v1/teams/{MISSING_ID}"} and not target.query:
            self.pending.append(route)
            return None
        if key == "/api/v1/auth/me":
            return route.fulfill(status=401, json={"detail": "Synthetic anonymous request"})
        if key == f"{API}/messages?limit=50":
            return route.fulfill(json=[])
        if key == f"{API}/stream":
            return route.fulfill(content_type="text/event-stream", body=": synthetic public stream\n\n")
        return self.reject(route)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class TeamDetailReadTests(BrowserFixture, unittest.TestCase):
    """Measure only changed read notices, not existing metadata or team chat geometry."""

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
        self.http = TeamReadHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__teamReadHeld", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + DETAIL, wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.held()

    def held(self):
        self.page.evaluate("""async()=>{const end=performance.now()+7000;while(await window.__teamReadHeld()!==1){
            if(performance.now()>end)throw Error('Expected one held team read');await new Promise(requestAnimationFrame);}}""")

    def settle(self):
        self.page.evaluate("""async()=>{await new Promise(requestAnimationFrame);
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);} """)

    def complete(self, status=200):
        self.held()
        route = self.http.pending.pop()
        if status == 0:
            route.abort("failed")
        else:
            route.fulfill(status=status, json=TEAM if status == 200 else {"detail": SENTINEL})
        self.settle()

    def native(self, target, measure=False):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        target.scroll_into_view_if_needed()
        self.settle()
        self.focus.append(target.evaluate(FOCUS))
        if measure:
            self.targets.append(target.evaluate(TARGET))

    def save(self, state, root=None):
        self.settle()
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": root.evaluate(METRICS) if root is not None else None,
            "focus": list(self.focus),
            "targets": list(self.targets),
            "calls": list(self.http.calls),
            "held": len(self.http.pending),
            "main_text": self.main.inner_text(),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"team-detail-reads-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.main.get_by_text(SENTINEL, exact=False)).to_have_count(0)

    def fail_and_retry(self):
        loading = self.main.get_by_text(COPY[self.locale][0], exact=True)
        expect(loading).to_be_visible()
        self.save("initial-pending", loading)
        self.complete(503 if self.locale == "en" else 0)
        notice = self.main.get_by_role("alert")
        expect(notice).to_contain_text(COPY[self.locale][1])
        expect(self.main.get_by_text(COPY[self.locale][3], exact=True)).to_have_count(0)
        retry = notice.get_by_role("button", name=COPY[self.locale][2], exact=True)
        self.native(retry, measure=True)
        self.save("error-retry-focused", notice)
        self.page.keyboard.press("Enter")
        self.held()
        expect(loading).to_be_visible()
        self.assertEqual(self.http.calls.count(API), 2)
        self.save("retry-pending", loading)
        self.complete()
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        expect(self.main.get_by_role("alert")).to_have_count(0)

    def local_sections(self):
        owner = self.main.get_by_role("link", name=re.compile(OWNER))
        expect(owner).to_have_attribute("href", f"/agents/{AGENT_ID}")
        self.native(self.main.get_by_role("button", name=COPY[self.locale][6], exact=True))
        self.page.keyboard.press("Enter")
        project = self.main.get_by_role("link", name=re.compile(PROJECT))
        expect(project).to_have_attribute("href", f"/projects/{PROJECT_ID}")
        self.native(project)
        self.save("projects-ready")
        self.native(self.main.get_by_role("button", name=COPY[self.locale][5], exact=True))
        self.page.keyboard.press("Enter")
        expect(owner).to_be_visible()
        self.native(owner)
        self.save("members-restored")
        self.assertEqual(self.http.calls.count(API), 2)
        self.assertEqual(self.http.calls.count(f"{API}/messages?limit=50"), 1)
        self.assertEqual(self.http.calls.count(f"{API}/stream"), 1)

    def missing_route(self):
        self.page.goto(self.options.base_url + MISSING, wait_until="domcontentloaded")
        self.held()
        self.complete(404)
        expect(self.main.get_by_text(COPY[self.locale][3], exact=True)).to_be_visible()
        expect(self.main.get_by_role("link", name=COPY[self.locale][4], exact=True)).to_have_attribute("href", "/teams")
        expect(self.main.get_by_role("alert")).to_have_count(0)
        expect(self.main.get_by_role("button", name=COPY[self.locale][2], exact=True)).to_have_count(0)
        self.save("missing-team")

    def notice_issues(self, record):
        metrics = record["metrics"]
        if metrics is None:
            return []
        issues = [{"font": item} for item in metrics["text"] if item["size"] < 12]
        issues.extend({"clip": item} for item in metrics["clipped"])
        if metrics["document_width"] > self.width:
            issues.append({"document_width": metrics["document_width"]})
        return issues

    def verify_notice_bounds(self):
        issues = [issue for record in self.records for issue in self.notice_issues(record)]
        for target in self.targets:
            if (
                target["font"] < 12
                or target["width"] < 44
                or target["height"] < 44
                or not target["contained"]
                or not target["in_view"]
            ):
                issues.append({"target": target})
        issues.extend(
            {"focus": item} for item in self.focus if not all(item[key] for key in ("active", "visible", "painted"))
        )
        self.assertEqual(issues, [])

    def test_primary_read_retry_and_existing_local_sections(self):
        """One bounded flow per locale/width; old-route deferred bodies remain unit-covered."""
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        self.fail_and_retry()
                        self.local_sections()
                        self.missing_route()
                        self.verify_notice_bounds()
                    finally:
                        self.doCleanups()


if __name__ == "__main__":
    TeamDetailReadTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
