"""Prove residual project-list filter composition and selected-query stale recovery."""

import json
import re
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS as AGENT_METRICS
from test_project_cards import PROJECTS as CARD_PROJECTS
from test_public_chat_accessibility import FOCUS

BASE = dict(CARD_PROJECTS[0], repo_url=None, deploy_url=None, tech_stack=[])
PROJECTS = [
    dict(
        BASE,
        id="00000000-0000-4000-8000-0000000002a1",
        title="Alpha service",
        description="A useful first service",
        agent_name="Ada",
        category="productivity",
        status="deployed",
        created_at="2026-10-20T11:00:00Z",
        github_stars=10,
        votes_up=12,
        votes_down=11,
        score=1,
    ),
    dict(
        BASE,
        id="00000000-0000-4000-8000-0000000002b1",
        title="Beta service",
        description="Bravo discovery text",
        agent_name="Boris",
        category="productivity",
        status="building",
        created_at="2026-10-18T12:00:00Z",
        github_stars=50,
        votes_up=20,
        votes_down=2,
        score=18,
    ),
    dict(
        BASE,
        id="00000000-0000-4000-8000-0000000002c1",
        title="Gamma service",
        description="A third useful service",
        agent_name="Cora",
        category="social",
        status="deployed",
        created_at="2026-10-01T12:00:00Z",
        github_stars=100,
        votes_up=100,
        votes_down=99,
        score=1,
    ),
    dict(
        BASE,
        id="00000000-0000-4000-8000-0000000002d1",
        title="Delta service",
        description="A previous useful service",
        agent_name="Dima",
        category="social",
        status="archived",
        created_at="2026-09-01T12:00:00Z",
        github_stars=0,
        votes_up=0,
        votes_down=10,
        score=-10,
    ),
]
COPY = {
    "en": (
        "Search...",
        "no projects found",
        "Project category",
        "Project status",
        "productivity",
        "deployed",
        "all",
        "24h",
        "7d",
        "30d",
        "new",
        "stars",
        "votes",
        "Retry",
        "Stale — last updated",
    ),
    "ru": (
        "Поиск...",
        "проекты не найдены",
        "Категория проекта",
        "Статус проекта",
        "Продуктивность",
        "Опубликован",
        "Все",
        "24 ч",
        "7 дней",
        "30 дней",
        "Новые",
        "Звёзды",
        "Голоса",
        "Повторить",
        "Устарело: последнее обновление",
    ),
}
SENTINEL = "Synthetic private failure sentinel"
METRICS = AGENT_METRICS.replace("if(painted(el)){", "if(painted(el)&&!el.closest('article')){")
METRICS = METRICS.replace("if(!n.textContent.trim()||", "if(!n.textContent.trim()||el.closest('article')||")


class ProjectListHTTP(AuthHTTP):
    def __init__(self, base_url, held):
        super().__init__(base_url)
        self.held, self.calls = held, []

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/projects" and not target.query:
            return True
        query = parse_qs(target.query, keep_blank_values=True)
        pages = {f"/projects/{project['id']}" for project in PROJECTS} | {"/docs/connect", "/docs/glossary"}
        if (
            request.method == "GET"
            and target.path in pages
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
        if request.method == "GET" and target.path == "/api/v1/projects":
            query = parse_qs(target.query, keep_blank_values=True)
            if (
                set(query) - {"limit", "category", "status"}
                or query.get("limit") != ["100"]
                or any(len(value) != 1 for value in query.values())
                or query.get("category", ["productivity"]) != ["productivity"]
                or query.get("status", ["deployed"]) != ["deployed"]
                or request.headers.get("authorization") is not None
            ):
                return self.reject(route)
            normalized = {key: value[0] for key, value in query.items()}
            self.calls.append(normalized)
            if self.held:
                self.pending.append(route)
            else:
                route.fulfill(json=self.payload(normalized))
            return
        return super().route(route)

    def payload(self, query):
        return [
            project
            for project in PROJECTS
            if all(project[key] == query[key] for key in ("category", "status") if key in query)
        ]

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class ProjectListTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width, held):
        self.locale, self.width, self.errors, self.collected, self.focus = locale, width, [], [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = ProjectListHTTP(self.options.base_url, held)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.clock.install(time="2026-10-20T12:00:00Z")
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldProjectList", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + "/projects", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.cards = self.main.locator("article")
        self.search = self.main.get_by_role("textbox", name=COPY[locale][0], exact=True)

    def settle(self):
        self.page.evaluate("""async()=>{
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
                Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        }""")

    def save(self, state):
        self.settle()
        metrics = self.main.evaluate(METRICS)
        record = {
            "scope": "RR2.REM.16 route chrome only; article excluded",
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "focus": self.focus,
            "calls": self.http.calls,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "projects": self.cards.locator("h3").all_text_contents(),
            "search": self.search.input_value() if self.search.count() else None,
        }
        self.collected.append(record)
        stem = f"project-list-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def issues(self):
        issues = []
        for record in self.collected:
            metrics = record["metrics"]
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

    def native(self, target, activate=True):
        for _ in range(self.page.locator("a[href],button,input").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.focus.append(target.evaluate(FOCUS))
        if activate:
            self.page.keyboard.press("Enter")
            expect(target).to_have_attribute("aria-pressed", "true")

    def controls(self):
        self.category = self.main.get_by_role("group", name=COPY[self.locale][2], exact=True)
        self.status = self.main.get_by_role("group", name=COPY[self.locale][3], exact=True)
        self.time = self.main.get_by_role("button", name=COPY[self.locale][7], exact=True).locator("..")
        self.sort = self.main.get_by_role("button", name=COPY[self.locale][10], exact=True).locator("..")

    def button(self, group, index):
        return group.get_by_role("button", name=COPY[self.locale][index], exact=True)

    def titles(self, expected):
        expect(self.cards.locator("h3")).to_have_text([f"{name} service" for name in expected])

    def local_flow(self):
        self.titles(["Alpha", "Beta", "Gamma", "Delta"])
        self.controls()
        self.save("populated-chrome")
        calls = len(self.http.calls)
        self.native(self.search, activate=False)
        for value, names in [("ALPHA", ["Alpha"]), ("BRAVO", ["Beta"]), ("CORA", ["Gamma"])]:
            self.search.fill(value)
            self.titles(names)
        self.search.fill("synthetic-no-match")
        expect(self.main.get_by_text(COPY[self.locale][1], exact=True)).to_be_visible()
        self.save("no-match")
        self.search.fill("")
        self.titles(["Alpha", "Beta", "Gamma", "Delta"])
        for index, names in [(7, ["Alpha"]), (8, ["Alpha", "Beta"]), (9, ["Alpha", "Beta", "Gamma"])]:
            self.native(self.button(self.time, index))
            self.titles(names)
        self.native(self.button(self.time, 6))
        self.native(self.button(self.sort, 11))
        self.titles(["Gamma", "Beta", "Alpha", "Delta"])
        self.native(self.button(self.sort, 12))
        self.titles(["Beta", "Alpha", "Gamma", "Delta"])
        self.native(self.button(self.time, 8))
        self.native(self.button(self.sort, 11))
        self.titles(["Beta", "Alpha"])
        self.search.fill("bravo")
        self.titles(["Beta"])
        self.search.fill("")
        self.native(self.button(self.time, 6))
        self.native(self.button(self.sort, 10))
        self.titles(["Alpha", "Beta", "Gamma", "Delta"])
        self.assertEqual(len(self.http.calls), calls)
        self.save("local-composed")
        self.assertEqual(self.issues(), [])

    def held(self):
        self.page.evaluate("""async()=>{const end=performance.now()+7000;
            while(await window.__heldProjectList()!==1){if(performance.now()>end)throw Error('Expected one held project-list read');
                await new Promise(requestAnimationFrame);}}""")
        self.assertEqual(len(self.http.pending), 1)

    def reply(self, failure=False):
        self.held()
        route = self.http.pending.pop()
        if failure and self.locale == "ru":
            route.abort("failed")
        elif failure:
            route.fulfill(status=503, json={"detail": SENTINEL})
        else:
            route.fulfill(json=self.http.payload(self.http.calls[-1]))
        self.settle()

    def stale_flow(self):
        self.held()
        expect(self.cards).to_have_count(0)
        expect(self.main.get_by_role("alert")).to_have_count(0)
        self.save("initial-pending-chrome")
        self.reply()
        self.titles(["Alpha", "Beta", "Gamma", "Delta"])
        self.controls()
        self.native(self.button(self.category, 4))
        self.reply()
        self.titles(["Alpha", "Beta"])
        self.native(self.button(self.status, 5))
        self.reply()
        self.titles(["Alpha"])
        selected = {"limit": "100", "category": "productivity", "status": "deployed"}
        self.assertEqual(self.http.calls, [{"limit": "100"}, {"limit": "100", "category": "productivity"}, selected])
        self.native(self.button(self.time, 8))
        self.native(self.button(self.sort, 11))
        self.search.fill("alpha")
        self.page.clock.fast_forward(30000)
        self.reply(failure=True)
        status = self.main.get_by_role("status").filter(has_text=COPY[self.locale][14])
        expect(status).to_be_visible()
        self.titles(["Alpha"])
        expect(self.search).to_have_value("alpha")
        for group, index in [(self.category, 4), (self.status, 5), (self.time, 8), (self.sort, 11)]:
            expect(self.button(group, index)).to_have_attribute("aria-pressed", "true")
        age = re.search(r"\d+", status.inner_text())
        self.assertIsNotNone(age)
        if age is not None:
            self.assertGreaterEqual(int(age.group()), 30)
        self.assertEqual(self.http.calls[-1], selected)
        expect(self.main.get_by_text(SENTINEL, exact=False)).to_have_count(0)
        self.save("stale-selected-query")
        retry = status.get_by_role("button", name=COPY[self.locale][13], exact=True)
        self.native(retry, activate=False)
        self.page.keyboard.press("Enter")
        self.reply()
        self.assertEqual(self.http.calls[-1], selected)
        self.assertEqual(len(self.http.calls), 5)
        self.titles(["Alpha"])
        expect(self.search).to_have_value("alpha")
        expect(self.main.get_by_role("status").filter(has_text=COPY[self.locale][14])).to_have_count(0)
        self.search.fill("synthetic-no-match")
        expect(self.main.get_by_text(COPY[self.locale][1], exact=True)).to_be_visible()
        self.search.fill("")
        self.titles(["Alpha"])
        self.save("selected-recovered")
        self.assertEqual(self.issues(), [])

    def run_flow(self, kind):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width, kind == "stale")
                        if kind == "stale":
                            self.stale_flow()
                        else:
                            self.local_flow()
                    finally:
                        self.doCleanups()

    def test_native_local_filter_composition_without_refetch(self):
        self.run_flow("local")

    def test_selected_server_query_stale_retention_and_retry(self):
        self.run_flow("stale")


if __name__ == "__main__":
    ProjectListTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
