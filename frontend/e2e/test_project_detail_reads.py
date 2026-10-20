"""Recover public project, ownership and contributor reads through actual controls."""

import json
import re
import unittest
from argparse import Namespace
from collections import Counter
from urllib.parse import urlparse

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_chat_ownership import OWNERSHIP, PROJECT, PROJECT_ID, OwnershipHTTP
from test_public_chat_accessibility import FOCUS

TITLE = "Synthetic readable project"
DESCRIPTION = "Synthetic project overview remains available."
PROJECT_DATA = dict(PROJECT, title=TITLE, description=DESCRIPTION, tech_stack=[], repo_url=None, deploy_url=None)
OWNERSHIP_DATA = dict(OWNERSHIP, project_title=TITLE, contributors=[])
CONTRIBUTOR_NAME = "Synthetic contributor"
CONTRIBUTOR = {
    "id": "00000000-0000-4000-8000-000000000007",
    "role": "contributor",
    "contribution_points": 120,
    "joined_at": "2026-10-19T09:00:00Z",
    "user_id": "00000000-0000-4000-8000-000000000005",
    "user_name": CONTRIBUTOR_NAME,
    "user_email": "synthetic-contributor@example.invalid",
    "wallet_address": None,
}
SENTINEL = "Synthetic private failure sentinel"
PATHS = {
    f"/api/v1/projects/{PROJECT_ID}": "project",
    f"/api/v1/projects/{PROJECT_ID}/ownership": "ownership",
    f"/api/v1/projects/{PROJECT_ID}/contributors": "contributors",
}
COPY = {
    "en": (
        "Loading...",
        "Could not load the data. Check your connection and try again.",
        "Retry",
        "Project not found",
        "Overview",
        "Ownership",
        "Contributors",
        "No agent contributors yet.",
        "No contributors yet.",
        "No ownership data available.",
    ),
    "ru": (
        "Загрузка...",
        "Не удалось загрузить данные. Проверьте соединение и повторите попытку.",
        "Повторить",
        "Проект не найден",
        "Обзор",
        "Владение",
        "Участники",
        "Агентов-участников пока нет.",
        "Участников пока нет.",
        "Данные о владении недоступны.",
    ),
}
TARGET = """el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el),bounds=[{left:0,right:innerWidth}];
    for(let p=el.parentElement;p;p=p.parentElement)if(/^(hidden|clip|auto|scroll)$/.test(getComputedStyle(p).overflowX)){
        const b=p.getBoundingClientRect(),c=getComputedStyle(p);bounds.push({left:b.left+parseFloat(c.borderLeftWidth),right:b.right-parseFloat(c.borderRightWidth)});}
    const header=document.querySelector('header')?.getBoundingClientRect().bottom||0;
    return {text:el.textContent.trim(),width:r.width,height:r.height,font:parseFloat(s.fontSize),
        contained:bounds.every(b=>r.left>=b.left&&r.right<=b.right),in_view:r.top>=Math.max(0,header)&&r.bottom<=innerHeight};}"""


class ProjectReadsHTTP(OwnershipHTTP):
    def __init__(self, base_url):
        super().__init__(base_url, "project")
        self.calls = []

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        kind = PATHS.get(target.path)
        if kind and request.method == "GET" and not target.query:
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.calls.append({"kind": kind, "method": "GET", "path": target.path, "query": "", "anonymous": True})
            self.pending.append((route, kind))
            return None
        if target.path.startswith("/api/"):
            return self.reject(route)
        return super().route(route)


class ProjectDetailReadTests(BrowserFixture, unittest.TestCase):
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
        self.http = ProjectReadsHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__projectReadHeld", lambda kind: sum(k == kind for _, k in self.http.pending))
        self.page.goto(self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        for kind in PATHS.values():
            self.held(kind)

    def held(self, kind):
        self.page.evaluate(
            """async kind=>{const end=performance.now()+7000;while(await window.__projectReadHeld(kind)!==1){
            if(performance.now()>end)throw Error('Expected one held project read');await new Promise(requestAnimationFrame);}}""",
            kind,
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

    def complete(self, kind, status=200):
        self.held(kind)
        route, _ = next(item for item in self.http.pending if item[1] == kind)
        self.http.pending.remove((route, kind))
        if status == 0:
            route.abort("failed")
        elif status >= 400:
            route.fulfill(status=status, json={"detail": SENTINEL})
        else:
            payloads = {
                "project": PROJECT_DATA,
                "ownership": OWNERSHIP_DATA,
                "contributors": {"contributors": [CONTRIBUTOR], "total": 1},
            }
            route.fulfill(json=payloads[kind])
        self.settle()

    def button(self, index):
        return self.page.get_by_role("button", name=re.compile("^" + COPY[self.locale][index] + r"(?:\s|$)"))

    def save(self, state, root=None):
        self.settle()
        metrics = None
        if root is not None and root.count() == 1:
            self.place(root)
            metrics = root.evaluate(METRICS)
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "focus": list(self.focus),
            "targets": list(self.targets),
            "calls": list(self.http.calls),
            "held": [kind for _, kind in self.http.pending],
            "main_text": self.main.inner_text(),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"project-detail-reads-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.main.get_by_text(SENTINEL, exact=False)).to_have_count(0)

    def native(self, target):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.place(target)
        self.focus.append(target.evaluate(FOCUS))
        self.targets.append(target.evaluate(TARGET))

    def select(self, index):
        self.native(self.button(index))
        before = list(self.http.calls)
        self.page.keyboard.press("Enter")
        self.settle()
        self.assertEqual(self.http.calls, before)
        for key in (4, 5, 6):
            expect(self.button(key)).to_have_attribute("aria-pressed", "true" if key == index else "false")

    def retry(self, kind):
        notice = self.main.get_by_role("alert")
        retry = notice.get_by_role("button", name=COPY[self.locale][2], exact=True)
        if retry.count() == 1:
            self.native(retry)
        self.save(f"{kind}-error-observed", notice)
        expect(notice).to_contain_text(COPY[self.locale][1])
        expect(retry).to_be_focused()
        before = Counter(item["kind"] for item in self.http.calls)
        self.page.keyboard.press("Enter")
        self.held(kind)
        after = Counter(item["kind"] for item in self.http.calls)
        self.assertEqual(after, before + Counter({kind: 1}))

    def primary_flow(self):
        self.save("primary-pending", self.main)
        expect(self.main.get_by_text(COPY[self.locale][0], exact=True)).to_be_visible()
        self.complete("project", 503)
        self.retry("project")
        expect(self.main.get_by_text(COPY[self.locale][3], exact=True)).to_have_count(0)
        self.complete("project")
        expect(self.page.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        expect(self.main.get_by_text(DESCRIPTION, exact=True)).to_be_visible()
        self.held("ownership")
        self.held("contributors")
        self.save("overview-ownership-held")

    def ownership_flow(self):
        self.select(5)
        pending = self.main.get_by_role("status")
        self.save("ownership-pending", pending)
        expect(pending).to_have_text(COPY[self.locale][0])
        expect(self.main.get_by_text(COPY[self.locale][7], exact=True)).to_have_count(0)
        expect(self.main.get_by_text(COPY[self.locale][9], exact=True)).to_have_count(0)
        self.complete("ownership", 503)
        self.retry("ownership")
        self.complete("ownership")
        expect(self.main.get_by_role("alert")).to_have_count(0)
        empty = self.main.get_by_text(COPY[self.locale][7], exact=True)
        expect(empty).to_be_visible()
        expect(self.page.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.save("ownership-empty", empty)

    def contributors_flow(self):
        self.select(6)
        pending = self.main.get_by_role("status")
        self.save("contributors-pending", pending)
        expect(pending).to_have_text(COPY[self.locale][0])
        expect(self.main.get_by_text(COPY[self.locale][8], exact=False)).to_have_count(0)
        self.assertNotIn("(0)", self.button(6).inner_text())
        self.complete("contributors", 503 if self.locale == "en" else 0)
        self.retry("contributors")
        self.complete("contributors")
        expect(self.main.get_by_text(CONTRIBUTOR_NAME, exact=True)).to_be_visible()
        expect(self.button(6)).to_contain_text("(1)")
        expect(self.main.get_by_role("alert")).to_have_count(0)
        self.save("contributors-recovered")

    def metric_issues(self, record):
        metrics = record["metrics"]
        if metrics is None:
            return []
        issues = [{"font": text} for text in metrics["text"] if text["size"] < 12]
        issues.extend({"clip": item} for item in metrics["clipped"])
        if metrics["document_width"] > self.width:
            issues.append({"document_width": metrics["document_width"]})
        return issues

    def verify_geometry(self):
        issues = [issue for record in self.records for issue in self.metric_issues(record)]
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
            {"focus": focus} for focus in self.focus if not all(focus[key] for key in ("active", "visible", "painted"))
        )
        self.assertEqual(issues, [])

    def run_flow(self):
        self.primary_flow()
        self.ownership_flow()
        self.contributors_flow()
        before = list(self.http.calls)
        self.select(4)
        expect(self.main.get_by_text(DESCRIPTION, exact=True)).to_be_visible()
        self.select(5)
        expect(self.main.get_by_text(COPY[self.locale][7], exact=True)).to_be_visible()
        self.select(6)
        expect(self.main.get_by_text(CONTRIBUTOR_NAME, exact=True)).to_be_visible()
        self.assertEqual(self.http.calls, before)
        self.assertEqual(
            Counter(item["kind"] for item in self.http.calls), {"project": 2, "ownership": 2, "contributors": 2}
        )
        self.save("local-sections-restored")
        self.verify_geometry()

    def test_read_failures_recover_each_resource_and_sections_stay_local(self):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        self.run_flow()
                    finally:
                        self.doCleanups()


if __name__ == "__main__":
    ProjectDetailReadTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
