"""Verify teams-list HTTP states, retained data, essential text and native card navigation."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS as AGENT_METRICS
from test_public_chat_accessibility import FOCUS
from test_public_chat_states import STREAM

NAME = "SyntheticResearchTeamForHelpfulPublicServices0123456789 — команда полезных сервисов"
CREATOR = "SyntheticCreatorWithLongName01234567890123456789"
DESCRIPTION = "A deliberate two-line team preview. Команда обсуждает полезные сервисы для людей. " * 5
TEAM = {
    "id": "00000000-0000-4000-8000-0000000000a1",
    "name": NAME,
    "description": DESCRIPTION,
    "avatar_url": None,
    "creator_name": CREATOR,
    "member_count": 987654321,
    "project_count": 987654321,
    "created_at": "2026-10-19T10:00:00Z",
}
OTHER = dict(
    TEAM,
    id="00000000-0000-4000-8000-0000000000b1",
    name="Synthetic second team",
    creator_name="Synthetic second creator",
    description="A second synthetic preview",
    member_count=2,
    project_count=0,
)
TEAMS, DETAIL = [TEAM, OTHER], f"/teams/{TEAM['id']}"
SENTINEL = "Synthetic private failure sentinel"
COPY = {
    "en": (
        "Loading teams",
        "No teams yet",
        "Retry",
        "Could not load the data. Check your connection and try again.",
        "Stale — last updated",
    ),
    "ru": (
        "Загрузка команд",
        "Команд пока нет",
        "Повторить",
        "Не удалось загрузить данные. Проверьте соединение и повторите попытку.",
        "Устарело: последнее обновление",
    ),
}
METRICS = AGENT_METRICS.replace('a[href^="/agents/"]', 'a[href^="/teams/"]')
METRICS = METRICS.replace(
    "if(el.closest('a[href^=\"/teams/\"]'))",
    "if(el.closest('a[href^=\"/teams/\"]')&&n.textContent.trim()!==" + json.dumps(DESCRIPTION.strip()) + ")",
)

METRICS = METRICS.replace(
    "const range=document.createRange();range.selectNodeContents(n);",
    "const preview=n.textContent.trim()===" + json.dumps(DESCRIPTION.strip()) + ";"
    "const region=el.getBoundingClientRect();const range=document.createRange();range.selectNodeContents(n);",
)
METRICS = METRICS.replace(
    "for(const r of range.getClientRects())for(const b of bounds(el))",
    "for(const r of range.getClientRects())if(!preview||(r.top<region.bottom&&r.bottom>region.top))for(const b of bounds(el))",
)


class TeamsHTTP(AuthHTTP):
    def __init__(self, base_url, held):
        super().__init__(base_url)
        self.held, self.calls = held, []

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path in {"/teams", DETAIL} and not target.query:
            return True
        query = parse_qs(target.query, keep_blank_values=True)
        native = target.path == DETAIL and request.headers.get("next-router-prefetch") is None
        prefetch = (
            target.path in {DETAIL, f"/teams/{OTHER['id']}", "/docs/connect", "/docs/glossary"}
            and request.headers.get("next-router-prefetch") == "1"
        )
        if (
            request.method == "GET"
            and request.headers.get("rsc") == "1"
            and (native or prefetch)
            and set(query) == {"_rsc"}
            and all(len(v) == 1 for v in query.values())
        ):
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/teams" and target.query == "limit=50":
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.calls.append({"method": "GET", "path": target.path, "query": target.query})
            if self.held:
                self.pending.append(route)
            else:
                route.fulfill(json=TEAMS)
            return
        if request.method == "GET" and target.path == "/api/v1" + DETAIL and not target.query:
            route.fulfill(json=dict(TEAM, members=[], projects=[]))
            return
        if request.method == "GET" and target.path == "/api/v1" + DETAIL + "/messages" and target.query == "limit=50":
            route.fulfill(json=[])
            return
        return super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class TeamsListTests(BrowserFixture, unittest.TestCase):
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
        context.add_init_script(STREAM)
        self.http = TeamsHTTP(self.options.base_url, held)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.clock.install(time="2026-10-20T12:00:00Z")
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldTeams", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + "/teams", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.cards = self.main.locator('a[href^="/teams/"]')

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
            "cards": self.cards.evaluate_all("els=>els.map(el=>el.getAttribute('href'))"),
        }
        self.collected.append(record)
        stem = f"teams-list-{self.locale}-{self.width}-{state}"
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
            while(await window.__heldTeams()!==1){if(performance.now()>end)throw Error('Expected one held teams read');
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

    def populated_flow(self):
        expect(self.cards).to_have_count(2)
        expect(self.main.get_by_text(NAME, exact=True)).to_be_visible()
        expect(self.main.get_by_text(CREATOR, exact=False)).to_be_visible()
        self.save("populated")
        count = len(self.http.calls)
        card = self.cards.filter(has_text=NAME)
        focus = self.tab_to(card)
        self.save("card-focused", {"focus": focus})
        self.assertEqual(len(self.http.calls), count)
        issues = self.geometry_issues()
        if not all(focus[key] for key in ("active", "visible", "painted")):
            issues.append({"focus": focus})
        self.page.keyboard.press("Enter")
        expect(self.page).to_have_url(self.options.base_url + DETAIL)
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(issues, [])

    def false_zero_issues(self):
        return [
            {"premature_zero_counter": element.text_content()}
            for element in self.main.get_by_text("0", exact=True).all()
            if element.is_visible()
        ]

    def state_flow(self):
        self.held()
        expect(self.main.get_by_text(COPY[self.locale][0], exact=True)).to_be_visible()
        behavior = self.false_zero_issues()
        expect(self.main.get_by_text(COPY[self.locale][1], exact=True)).to_have_count(0)
        self.save("loading")
        self.reply(fail=True)
        if self.main.get_by_text(COPY[self.locale][1], exact=True).count():
            behavior.append({"failed_load_rendered_empty": True})
        if not self.main.get_by_role("alert").filter(has_text=COPY[self.locale][3]).count():
            behavior.append({"initial_generic_alert_missing": True})
        behavior.extend(self.false_zero_issues())
        expect(self.main.get_by_text(SENTINEL, exact=False)).to_have_count(0)
        self.save("error")
        retry = self.main.get_by_role("button", name=COPY[self.locale][2], exact=True)
        retry_focus = self.tab_to(retry)
        self.page.keyboard.press("Enter")
        self.reply([])
        expect(self.main.get_by_text(COPY[self.locale][1], exact=True)).to_be_visible()
        self.save("empty", {"retry_focus": retry_focus})
        self.page.clock.fast_forward(30000)
        self.reply(TEAMS)
        expect(self.cards).to_have_count(2)
        self.page.clock.fast_forward(30000)
        self.reply(fail=True)
        expect(self.main.get_by_role("status").filter(has_text=COPY[self.locale][4])).to_be_visible()
        expect(self.cards).to_have_count(2)
        expect(self.main.get_by_text(NAME, exact=True)).to_be_visible()
        self.save("stale-retained")
        retry = self.main.get_by_role("button", name=COPY[self.locale][2], exact=True)
        stale_focus = self.tab_to(retry)
        self.page.keyboard.press("Enter")
        self.reply(TEAMS)
        expect(self.cards).to_have_count(2)
        expect(self.main.get_by_role("status").filter(has_text=COPY[self.locale][4])).to_have_count(0)
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

    def test_populated_essential_text_and_native_card_navigation(self):
        self.run_flow("populated")

    def test_pending_error_empty_and_stale_retention(self):
        self.run_flow("states")


if __name__ == "__main__":
    TeamsListTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
