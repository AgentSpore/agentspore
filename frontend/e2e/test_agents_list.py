"""Exercise actual agents-list HTTP, native filtering/navigation and rendered bounds."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_detail_width import RESOURCES
from test_public_chat_accessibility import FOCUS

RESOURCE = RESOURCES["agent"]
if not isinstance(RESOURCE, dict):
    raise TypeError("Expected Agent mapping fixture")
NAME = "Synthetic Research Agent — помощник для обсуждения исследовательских идей " * 2
HANDLE = "synthetic-research-agent-0123456789012345678901234"
PROVIDER = "synthetic-provider-for-public-research-agents"
LIVE = dict(
    RESOURCE,
    name=NAME,
    handle=HANDLE,
    model_provider=PROVIDER,
    specialization="programmer",
    is_active=False,
    last_heartbeat="2026-10-20T11:00:00Z",
    karma=987654321,
    code_commits=987654321,
    projects_created=987654321,
    reviews_done=987654321,
)
STALE = dict(
    RESOURCE,
    id="00000000-0000-4000-8000-000000000002",
    name="Synthetic quiet reviewer",
    handle="synthetic-quiet-reviewer",
    specialization="reviewer",
    is_active=True,
    last_heartbeat="2026-10-18T11:00:00Z",
    karma=999999999,
)
MISSING = dict(
    RESOURCE,
    id="00000000-0000-4000-8000-000000000003",
    name="Synthetic without heartbeat",
    handle="synthetic-no-heartbeat",
    specialization="analyst",
    is_active=True,
    last_heartbeat=None,
)
AGENTS = [STALE, LIVE, MISSING]
DETAIL = f"/agents/{LIVE['id']}"
SENTINEL = "Synthetic private failure sentinel"
COPY = {
    "en": (
        "Search...",
        "All",
        "Active",
        "No agents match your filter",
        "Loading agents...",
        "Could not load the data. Check your connection and try again.",
        "Retry",
        "No agents yet",
        "Stale — last updated",
    ),
    "ru": (
        "Поиск...",
        "Все",
        "Активные",
        "Нет агентов, соответствующих фильтру",
        "Загрузка агентов...",
        "Не удалось загрузить данные. Проверьте соединение и повторите попытку.",
        "Повторить",
        "Агентов пока нет",
        "Устарело: последнее обновление",
    ),
}


class AgentsHTTP(AuthHTTP):
    def __init__(self, base_url, held):
        super().__init__(base_url)
        self.held, self.calls = held, []

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path in {"/agents", DETAIL} and not target.query:
            return True
        query = parse_qs(target.query, keep_blank_values=True)
        if (
            request.method == "GET"
            and target.path == DETAIL
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") is None
            and set(query) == {"_rsc"}
            and all(len(value) == 1 for value in query.values())
        ):
            return True
        pages = {f"/agents/{agent['id']}" for agent in AGENTS} | {"/docs/connect", "/docs/glossary"}
        if (
            request.method == "GET"
            and target.path in pages
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and set(query) == {"_rsc"}
            and all(len(value) == 1 for value in query.values())
        ):
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/agents/leaderboard" and target.query == "limit=100":
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.calls.append({"method": "GET", "path": target.path, "query": target.query})
            if self.held:
                self.pending.append(route)
            else:
                route.fulfill(json=AGENTS)
            return
        detail = {
            ("/api/v1" + DETAIL, ""): LIVE,
            ("/api/v1/activity", f"agent_id={LIVE['id']}&limit=50"): [],
            ("/api/v1" + DETAIL + "/model-usage", ""): RESOURCES["model_usage_empty"],
            ("/api/v1" + DETAIL + "/github-activity", "limit=50"): {"activities": [], "total": 0},
            ("/api/v1" + DETAIL + "/badges", ""): [],
            (f"/api/v1/blog/agents/{LIVE['id']}/posts", "limit=10&offset=0"): {
                "posts": [],
                "total": 0,
                "limit": 10,
                "offset": 0,
            },
        }
        if request.method == "GET" and (target.path, target.query) in detail:
            route.fulfill(json=detail[(target.path, target.query)])
            return
        return super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


METRICS = """main=>{
    const text=[],targets=[],clipped=[],essential=[];
    const painted=el=>{for(let n=el;n;n=n.parentElement){const s=getComputedStyle(n);
        if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0||s.clipPath==='inset(50%)')return false;
        const c=s.clip.match(/-?[0-9.]+px/g)?.map(parseFloat);
        if(/^(absolute|fixed)$/.test(s.position)&&c?.length===4&&(c[2]<=c[0]||c[1]<=c[3]))return false;}
        return el.getClientRects().length>0;};
    const bounds=el=>{const result=[{left:0,right:innerWidth}];
        const row=el.closest('a[href^="/agents/"]');if(row){const r=row.getBoundingClientRect();result.push({left:r.left,right:r.right});}
        for(let n=el.parentElement;n;n=n.parentElement)if(/^(hidden|clip|auto|scroll)$/.test(getComputedStyle(n).overflowX)){
            const r=n.getBoundingClientRect(),s=getComputedStyle(n),left=r.left+parseFloat(s.borderLeftWidth),right=r.right-parseFloat(s.borderRightWidth);
            result.push({left,right});}return result;};
    for(const el of main.querySelectorAll('a[href],button,input'))if(painted(el)){
        const r=el.getBoundingClientRect();targets.push({name:el.getAttribute('aria-label')||el.textContent.trim()||el.title,
            width:r.width,height:r.height,contained:bounds(el).every(b=>r.left>=b.left&&r.right<=b.right)});}
    const walker=document.createTreeWalker(main,NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){const n=walker.currentNode,el=n.parentElement;
        if(!n.textContent.trim()||el.closest('svg,[aria-hidden="true"]')||!painted(el))continue;
        text.push({text:n.textContent.trim(),size:parseFloat(getComputedStyle(el).fontSize)});
        const range=document.createRange();range.selectNodeContents(n);
        for(const r of range.getClientRects())for(const b of bounds(el))
            if(r.left<b.left||r.right>b.right)clipped.push({text:n.textContent.trim(),left:r.left,right:r.right,bound:b});
        if(el.closest('a[href^="/agents/"]')){const s=getComputedStyle(el);
            essential.push({text:n.textContent.trim(),ellipsis:s.textOverflow==='ellipsis'&&el.scrollWidth>el.clientWidth});}
    }
    return {text,targets,clipped,essential,document_width:document.documentElement.scrollWidth};
}"""


class AgentsListTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width, held):
        self.locale, self.width, self.errors, self.collected = locale, width, [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = AgentsHTTP(self.options.base_url, held)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.clock.install(time="2026-10-20T12:00:00Z")
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldAgents", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + "/agents", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.search = self.main.get_by_role("textbox", name=COPY[locale][0], exact=True)
        self.rows = self.main.locator('a[href^="/agents/"]')

    def settle(self):
        self.page.evaluate("""async()=>{
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
                Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        }""")

    def save(self, state, extra=None):
        self.settle()
        metrics = self.main.evaluate(METRICS)
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "extra": extra,
            "calls": self.http.calls,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "rows": self.rows.evaluate_all("els=>els.map(el=>el.getAttribute('href'))"),
            "search": self.search.input_value() if self.search.count() else None,
        }
        self.collected.append(record)
        stem = f"agents-list-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def geometry_issues(self):
        issues = []
        for record in self.collected:
            metrics = record["metrics"]
            for item in metrics["text"]:
                if item["size"] < 12:
                    issues.append({"state": record["state"], "small_text": item})
            for item in metrics["targets"]:
                if item["width"] < 44 or item["height"] < 44 or not item["contained"]:
                    issues.append({"state": record["state"], "target": item})
            issues.extend({"state": record["state"], "clip": item} for item in metrics["clipped"])
            issues.extend(
                {"state": record["state"], "ellipsis": item} for item in metrics["essential"] if item["ellipsis"]
            )
            if metrics["document_width"] > self.width:
                issues.append({"state": record["state"], "document_width": metrics["document_width"]})
        return issues

    def held(self):
        self.page.evaluate("""async()=>{const end=performance.now()+7000;
            while(await window.__heldAgents()!==1){if(performance.now()>end)throw Error('Expected one held agents read');
                await new Promise(requestAnimationFrame);}}""")
        self.assertEqual(len(self.http.pending), 1)

    def reply(self, body=None, fail=False):
        self.held()
        route = self.http.pending.pop()
        if fail and self.locale == "ru":
            route.abort("failed")
        elif fail:
            route.fulfill(status=503, json={"detail": SENTINEL})
        else:
            route.fulfill(json=body)
        self.settle()

    def tab_to(self, target):
        for _ in range(self.page.locator('a[href],button,input,[tabindex="0"]').count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        return target.evaluate(FOCUS)

    def filter_button(self, index):
        return self.main.get_by_role("button", name=COPY[self.locale][index], exact=True)

    def populated_flow(self):
        expect(self.rows).to_have_count(3)
        self.save("populated")
        self.assertEqual(self.rows.first.get_attribute("href"), DETAIL)
        count = len(self.http.calls)
        self.search.fill("RESEARCH")
        expect(self.rows).to_have_count(1)
        self.search.fill("REVIEWER")
        expect(self.rows).to_have_count(1)
        expect(self.rows.first).to_have_attribute("href", f"/agents/{STALE['id']}")
        self.search.fill("synthetic-no-match")
        expect(self.main.get_by_text(COPY[self.locale][3], exact=True)).to_be_visible()
        expect(self.rows).to_have_count(0)
        self.save("no-match")
        self.search.fill("")
        expect(self.rows).to_have_count(3)
        active_focus = self.tab_to(self.filter_button(2))
        self.page.keyboard.press("Enter")
        expect(self.filter_button(2)).to_have_attribute("aria-pressed", "true")
        expect(self.rows).to_have_count(1)
        expect(self.rows.first).to_have_attribute("href", DETAIL)
        self.save("active", {"focus": active_focus})
        self.filter_button(1).click()
        expect(self.rows).to_have_count(3)
        self.assertEqual(len(self.http.calls), count)
        search_focus = self.tab_to(self.search)
        row_focus = self.tab_to(self.rows.first)
        self.save("row-focused", {"search_focus": search_focus, "row_focus": row_focus})
        issues = self.geometry_issues()
        for focus in (active_focus, search_focus, row_focus):
            if not all(focus[key] for key in ("active", "visible", "painted")):
                issues.append({"focus": focus})
        self.page.keyboard.press("Enter")
        expect(self.page).to_have_url(self.options.base_url + DETAIL)
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(issues, [])

    def state_flow(self):
        behavior = []
        self.held()
        expect(self.main.get_by_text(COPY[self.locale][4], exact=True)).to_be_visible()
        expect(self.main.get_by_text(COPY[self.locale][7], exact=True)).to_have_count(0)
        self.save("loading")
        self.reply(fail=True)
        expect(self.main.get_by_role("alert")).to_be_visible()
        expect(self.main.get_by_text(SENTINEL, exact=False)).to_have_count(0)
        self.save("error")
        retry = self.main.get_by_role("button", name=COPY[self.locale][6], exact=True)
        retry_focus = self.tab_to(retry)
        self.page.keyboard.press("Enter")
        self.reply([])
        expect(self.main.get_by_text(COPY[self.locale][7], exact=True)).to_be_visible()
        expect(self.search).to_have_count(0)
        self.save("empty", {"retry_focus": retry_focus})
        self.page.clock.fast_forward(30000)
        self.reply(AGENTS)
        expect(self.rows).to_have_count(3)
        self.filter_button(2).click()
        self.search.fill("synthetic-no-match")
        expect(self.main.get_by_text(COPY[self.locale][3], exact=True)).to_be_visible()
        self.page.clock.fast_forward(30000)
        self.reply(fail=True)
        expect(self.main.get_by_role("status").filter(has_text=COPY[self.locale][8])).to_be_visible()
        self.save("stale-no-match")
        if not self.main.get_by_text(COPY[self.locale][3], exact=True).count():
            behavior.append({"stale_no_match_missing": True})
        expect(self.search).to_have_value("synthetic-no-match")
        expect(self.filter_button(2)).to_have_attribute("aria-pressed", "true")
        self.search.fill("")
        expect(self.rows).to_have_count(1)
        retry = self.main.get_by_role("button", name=COPY[self.locale][6], exact=True)
        stale_focus = self.tab_to(retry)
        self.page.keyboard.press("Enter")
        self.reply(AGENTS)
        expect(self.rows).to_have_count(1)
        expect(self.filter_button(2)).to_have_attribute("aria-pressed", "true")
        expect(self.main.get_by_role("status").filter(has_text=COPY[self.locale][8])).to_have_count(0)
        self.save("recovered", {"focus": stale_focus})
        issues = self.geometry_issues() + behavior
        for focus in (retry_focus, stale_focus):
            if not all(focus[key] for key in ("active", "visible", "painted")):
                issues.append({"focus": focus})
        self.assertEqual(issues, [])

    def run_flow(self, kind):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width, kind == "states")
                        if kind == "states":
                            self.state_flow()
                        else:
                            self.populated_flow()
                    finally:
                        self.doCleanups()

    def test_populated_search_heartbeat_filter_and_native_navigation(self):
        self.run_flow("populated")

    def test_loading_error_empty_and_stale_recovery(self):
        self.run_flow("states")


if __name__ == "__main__":
    AgentsListTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
