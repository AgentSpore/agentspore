"""Distinguish missing/unavailable agent reads and recover partial core sections."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_detail_width import RESOURCES
from test_public_chat_accessibility import FOCUS

RESOURCE = RESOURCES["agent"]
if not isinstance(RESOURCE, dict):
    raise TypeError("Expected Agent mapping fixture")
NAME = "Synthetic readable agent"
AGENT = dict(RESOURCE, name=NAME, bio="Synthetic short biography", skills=[])
PAGE = f"/agents/{AGENT['id']}"
ENDPOINTS = {
    ("/api/v1" + PAGE, ""): "profile",
    ("/api/v1/activity", f"agent_id={AGENT['id']}&limit=50"): "activity",
    ("/api/v1" + PAGE + "/model-usage", ""): "models",
    ("/api/v1" + PAGE + "/github-activity", "limit=50"): "github",
    ("/api/v1" + PAGE + "/badges", ""): "badges",
}
PAYLOADS = {
    "profile": AGENT,
    "activity": [],
    "models": RESOURCES["model_usage_empty"],
    "github": {"activities": [], "total": 0},
    "badges": [],
}
SENTINEL = "Synthetic private failure sentinel"
COPY = {
    "en": (
        "Could not load the data. Check your connection and try again.",
        "Retry",
        "Agent not found",
        "Some profile sections could not be loaded.",
        "Activity Timeline",
        "Model Usage",
        "GitHub Activity",
        "Badges",
        "No activity recorded yet",
    ),
    "ru": (
        "Не удалось загрузить данные. Проверьте соединение и повторите попытку.",
        "Повторить",
        "Агент не найден",
        "Не удалось загрузить некоторые разделы профиля.",
        "История активности",
        "Использование моделей",
        "Активность в GitHub",
        "Значки",
        "Активности пока нет",
    ),
}


class AgentReadHTTP(AuthHTTP):
    def __init__(self, base_url):
        super().__init__(base_url)
        self.calls, self.blog_calls = [], []

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == PAGE and not target.query:
            return True
        query = parse_qs(target.query, keep_blank_values=True)
        if (
            request.method == "GET"
            and target.path in {PAGE + "/chat", "/docs/connect", "/docs/glossary"}
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
        key = ENDPOINTS.get((target.path, target.query))
        if request.method == "GET" and key:
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.calls.append({"key": key, "path": target.path, "query": target.query, "anonymous": True})
            self.pending.append((route, key))
            return
        if (
            request.method == "GET"
            and target.path == f"/api/v1/blog/agents/{AGENT['id']}/posts"
            and target.query == "limit=10&offset=0"
            and request.headers.get("authorization") is None
        ):
            self.blog_calls.append({"path": target.path, "query": target.query})
            route.fulfill(json={"posts": [], "total": 0, "limit": 10, "offset": 0})
            return
        return super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop()[0].abort("failed")
        context.close()


class AgentDetailStateTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width):
        self.locale, self.width, self.errors, self.collected, self.focus = locale, width, [], [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = AgentReadHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__agentReadHeld", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + PAGE, wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.held()

    def settle(self):
        self.page.evaluate("""async()=>{
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
                Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        }""")

    def held(self):
        self.page.evaluate("""async()=>{const end=performance.now()+7000;
            while(await window.__agentReadHeld()!==5){if(performance.now()>end)throw Error('Expected five held agent reads');
                await new Promise(requestAnimationFrame);}}""")
        self.assertEqual(sorted(key for _, key in self.http.pending), sorted(PAYLOADS))

    def release(self, failed=None, not_found=False):
        self.held()
        for route, key in list(self.http.pending):
            self.http.pending.remove((route, key))
            if key == "profile" and not_found:
                route.fulfill(status=404, json={"detail": "Synthetic missing agent"})
            elif key in (failed or set()):
                if self.locale == "ru":
                    route.abort("failed")
                else:
                    route.fulfill(status=503, json={"detail": SENTINEL})
            else:
                route.fulfill(json=PAYLOADS[key])
        self.settle()

    def notice(self, index):
        return self.main.get_by_role("alert").filter(has_text=COPY[self.locale][index])

    def save(self, state, root=None):
        self.settle()
        if root is not None and root.count():
            root.scroll_into_view_if_needed()
            metrics = root.evaluate(METRICS)
        else:
            metrics = None
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "focus": self.focus,
            "calls": self.http.calls,
            "blog_calls": self.http.blog_calls,
            "unknown_http": self.http.unexpected,
            "page_errors": self.errors,
            "profile_visible": self.main.get_by_role("heading", name=NAME, exact=True).count(),
            "alerts": self.main.get_by_role("alert").all_text_contents(),
        }
        self.collected.append(record)
        stem = f"agent-detail-reads-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.main.get_by_text(SENTINEL, exact=False)).to_have_count(0)

    def geometry_issues(self):
        issues = []
        for record in self.collected:
            metrics = record["metrics"]
            if metrics is None:
                continue
            for text in metrics["text"]:
                if text["size"] < 12:
                    issues.append({"state": record["state"], "small_text": text})
            for target in metrics["targets"]:
                if target["width"] < 44 or target["height"] < 44 or not target["contained"]:
                    issues.append({"state": record["state"], "target": target})
            issues.extend({"state": record["state"], "clip": item} for item in metrics["clipped"])
            if metrics["document_width"] > self.width:
                issues.append({"state": record["state"], "document_width": metrics["document_width"]})
        for focus in self.focus:
            if not all(focus[key] for key in ("active", "visible", "painted")):
                issues.append({"focus": focus})
        return issues

    def native(self, target):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.focus.append(target.evaluate(FOCUS))

    def retry(self, notice):
        button = notice.get_by_role("button", name=COPY[self.locale][1], exact=True)
        expect(button).to_have_count(1)
        self.native(button)
        self.page.keyboard.press("Enter")
        self.held()
        self.assertEqual(len(self.http.calls), 10)
        self.release()

    def primary_flow(self):
        expect(self.main).to_have_attribute("tabindex", "-1")
        expect(self.main.get_by_role("heading", name=NAME, exact=True)).to_have_count(0)
        self.save("initial-pending", self.main)
        self.release({"profile"})
        generic = self.notice(0)
        self.save("primary-unavailable-observed", self.main)
        expect(generic).to_be_visible()
        expect(self.main.get_by_text(COPY[self.locale][2], exact=True)).to_have_count(0)
        self.retry(generic)
        expect(self.main.get_by_role("heading", name=NAME, exact=True)).to_be_visible()
        self.assertEqual(len(self.http.blog_calls), 1)
        self.save("primary-recovered")
        self.page.reload(wait_until="domcontentloaded")
        self.release(not_found=True)
        expect(self.main.get_by_text(COPY[self.locale][2], exact=True)).to_be_visible()
        expect(self.notice(0)).to_have_count(0)
        back = self.main.locator('a[href="/"]')
        expect(back).to_have_count(1)
        self.native(back)
        self.save("not-found", self.main)
        self.assertEqual(self.geometry_issues(), [])

    def partial_flow(self):
        self.save("auxiliary-pending", self.main)
        self.release({"activity", "models", "github", "badges"})
        notice = self.notice(3)
        self.save("partial-observed", notice)
        expect(self.main.get_by_role("heading", name=NAME, exact=True)).to_be_visible()
        expect(notice).to_be_visible()
        for label in COPY[self.locale][4:8]:
            expect(notice).to_contain_text(label)
        expect(self.main.get_by_text(COPY[self.locale][8], exact=True)).to_have_count(0)
        self.assertEqual(len(self.http.blog_calls), 1)
        self.retry(notice)
        expect(self.main.get_by_role("heading", name=NAME, exact=True)).to_be_visible()
        expect(self.notice(3)).to_have_count(0)
        expect(self.main.get_by_text(COPY[self.locale][8], exact=True)).to_be_visible()
        self.save("partial-recovered")
        self.assertEqual(self.geometry_issues(), [])

    def run_flow(self, kind):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        if kind == "primary":
                            self.primary_flow()
                        else:
                            self.partial_flow()
                    finally:
                        self.doCleanups()

    def test_primary_unavailable_retry_and_explicit_not_found(self):
        self.run_flow("primary")

    def test_auxiliary_failures_preserve_profile_and_retry_current_batch(self):
        self.run_flow("partial")


if __name__ == "__main__":
    AgentDetailStateTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
