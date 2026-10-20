"""Verify only Showcase catalog states, readable metadata and native outbound controls."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS as AGENT_METRICS
from test_project_cards import PROJECTS as CARD_PROJECTS
from test_public_chat_accessibility import FOCUS

TITLE = "Synthetic verdict service — понятный сервис для исследования семейных решений и полезных повседневных действий"
HANDLE = "synthetic-research-agent-0123456789012345678901234"
TECH = "SyntheticTechnologyWithARealisticallyLongUnbrokenLabel0123456789"
DESCRIPTION = "A deliberate two-line catalog preview. Полное описание доступно в проекте. " * 5
REPO = "https://github.com/SyntheticResearchToolsOrganization/verdict"
DEMO = "https://showcase-fixture.invalid/verdict"
LIVE = dict(
    CARD_PROJECTS[0],
    title=TITLE,
    description=DESCRIPTION,
    agent_handle=HANDLE,
    repo_url=REPO,
    deploy_url=DEMO,
    tech_stack=[TECH, "TypeScript", "Python", "PostgreSQL", "Redis"],
)
ARCHIVED = dict(
    LIVE,
    id="00000000-0000-4000-8000-000000000102",
    title="Synthetic archived otkrytka",
    repo_url="https://github.com/SyntheticResearchToolsOrganization/otkrytka",
    status="archived",
    deploy_url=None,
)
UNLISTED = dict(
    LIVE,
    id="00000000-0000-4000-8000-000000000103",
    title="Synthetic unlisted tool",
    repo_url="https://github.com/SyntheticResearchToolsOrganization/unlisted-tool",
)
PROJECTS = [LIVE, ARCHIVED, UNLISTED]
SENTINEL = "Synthetic private failure sentinel"
COPY = {
    "en": (
        "home",
        "catalog apps",
        "No catalog apps found. The services above remain available to explore.",
        "Catalog unavailable. You can still read the service instructions above.",
        "Retry",
        "Stale — last updated",
    ),
    "ru": (
        "главная",
        "проектов в каталоге",
        "В каталоге пока нет проектов. Инструкции сервисов выше доступны.",
        "Каталог недоступен. Инструкции сервисов выше можно прочитать.",
        "Повторить",
        "Устарело: последнее обновление",
    ),
}
METRICS = AGENT_METRICS.replace(
    "if(el.closest('a[href^=\"/agents/\"]'))", "if(n.textContent.trim()!==" + json.dumps(DESCRIPTION.strip()) + ")"
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


class CatalogHTTP(AuthHTTP):
    def __init__(self, base_url, held):
        super().__init__(base_url)
        self.held, self.calls, self.external_stubbed = held, [], []

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/showcase" and not target.query:
            return True
        query = parse_qs(target.query, keep_blank_values=True)
        if (
            request.method == "GET"
            and target.path in {"/docs/connect", "/docs/glossary"}
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and set(query) == {"_rsc"}
            and all(len(v) == 1 for v in query.values())
        ):
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if request.method == "GET" and request.url in {REPO, DEMO} and request.resource_type == "document":
            self.external_stubbed.append({"url": request.url, "method": "GET"})
            route.fulfill(
                status=200,
                content_type="text/html",
                headers={"Content-Security-Policy": "default-src 'none'; img-src data:"},
                body="<!doctype html><html><head><link rel='icon' href='data:,'></head><body>Synthetic popup</body></html>",
            )
            return
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/projects" and target.query == "limit=200":
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.calls.append({"method": "GET", "path": target.path, "query": target.query})
            if self.held:
                self.pending.append(route)
            else:
                route.fulfill(json=PROJECTS)
            return
        return super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class ShowcaseCatalogTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width, held):
        self.locale, self.width, self.errors, self.collected, self.focus = locale, width, [], [], []
        self.context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        self.context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = CatalogHTTP(self.options.base_url, held)
        self.addCleanup(self.http.close, self.context)
        self.context.route("**/*", self.http.route)
        self.page = self.context.new_page()
        self.page.clock.install(time="2026-10-20T12:00:00Z")
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldCatalog", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + "/showcase", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.catalog = self.main.locator('section[aria-labelledby="project-catalog"]')
        self.home = self.main.get_by_role("link", name=COPY[locale][0], exact=True)
        self.cards = self.catalog.locator("h3")

    def settle(self):
        self.page.evaluate("""async()=>{
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
                Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        }""")

    def save(self, state):
        self.settle()
        self.catalog.scroll_into_view_if_needed()
        metrics = {"catalog": self.catalog.evaluate(METRICS), "breadcrumb": self.home.locator("..").evaluate(METRICS)}
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "focus": self.focus,
            "calls": self.http.calls,
            "external_stubbed": self.http.external_stubbed,
            "unknown_http": self.http.unexpected,
            "page_errors": self.errors,
            "titles": self.cards.all_text_contents(),
        }
        self.collected.append(record)
        stem = f"showcase-catalog-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def issues(self):
        issues = []
        for record in self.collected:
            for scope, metrics in record["metrics"].items():
                for text in metrics["text"]:
                    if text["size"] < 12:
                        issues.append({"state": record["state"], "scope": scope, "small_text": text})
                for target in metrics["targets"]:
                    if target["width"] < 44 or target["height"] < 44 or not target["contained"]:
                        issues.append({"state": record["state"], "scope": scope, "target": target})
                issues.extend({"state": record["state"], "clip": item} for item in metrics["clipped"])
                issues.extend(
                    {"state": record["state"], "ellipsis": item} for item in metrics["essential"] if item["ellipsis"]
                )
                if metrics["document_width"] > self.width:
                    issues.append({"state": record["state"], "document_width": metrics["document_width"]})
        for focus in self.focus:
            if not all(focus[key] for key in ("active", "visible", "painted")):
                issues.append({"focus": focus})
        return issues

    def native(self, target):
        for _ in range(self.page.locator("a[href],button,input,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.focus.append(target.evaluate(FOCUS))

    def popup(self, url):
        link = self.catalog.locator(f'a[href="{url}"]')
        expect(link).to_have_attribute("target", "_blank")
        rel = link.get_attribute("rel") or ""
        self.assertTrue({"noopener", "noreferrer"} <= set(rel.split()))
        self.native(link)
        with self.context.expect_page() as opened:
            self.page.keyboard.press("Enter")
        popup = opened.value
        popup.wait_for_load_state("domcontentloaded")
        self.assertEqual(popup.url, url)
        popup.close()

    def populated_flow(self):
        expect(self.cards).to_have_count(2)
        expect(self.catalog.get_by_text(TITLE, exact=True)).to_be_visible()
        expect(self.catalog.get_by_text(HANDLE, exact=True)).to_have_count(2)
        self.save("populated")
        self.native(self.home)
        before = len(self.http.calls)
        self.popup(REPO)
        self.popup(DEMO)
        self.assertEqual(self.http.external_stubbed, [{"url": REPO, "method": "GET"}, {"url": DEMO, "method": "GET"}])
        self.assertEqual(len(self.http.calls), before)
        self.save("native-actions")
        self.assertEqual(self.issues(), [])

    def held(self):
        self.page.evaluate("""async()=>{const end=performance.now()+7000;
            while(await window.__heldCatalog()!==1){if(performance.now()>end)throw Error('Expected one held catalog read');
                await new Promise(requestAnimationFrame);}}""")
        self.assertEqual(len(self.http.pending), 1)

    def reply(self, body=None, failure=False):
        self.held()
        route = self.http.pending.pop()
        if failure and self.locale == "ru":
            route.abort("failed")
        elif failure:
            route.fulfill(status=503, json={"detail": SENTINEL})
        else:
            route.fulfill(json=body)
        self.settle()

    def zero_issues(self):
        count = self.catalog.get_by_text("0 " + COPY[self.locale][1], exact=True)
        return [{"unknown_count_rendered_zero": True}] if count.count() else []

    def state_flow(self):
        self.held()
        behavior = self.zero_issues()
        expect(self.cards).to_have_count(0)
        expect(self.catalog.get_by_text(COPY[self.locale][2], exact=True)).to_have_count(0)
        expect(self.catalog.get_by_text(COPY[self.locale][3], exact=True)).to_have_count(0)
        self.save("pending")
        self.reply(failure=True)
        expect(self.catalog.get_by_text(COPY[self.locale][3], exact=True)).to_be_visible()
        expect(self.catalog.get_by_text(COPY[self.locale][2], exact=True)).to_have_count(0)
        behavior.extend(self.zero_issues())
        expect(self.catalog.get_by_text(SENTINEL, exact=False)).to_have_count(0)
        self.save("unavailable")
        retry = self.catalog.get_by_role("button", name=COPY[self.locale][4], exact=True)
        expect(retry).to_have_count(1)
        self.native(retry)
        self.page.keyboard.press("Enter")
        self.reply([] if self.locale == "en" else [UNLISTED])
        expect(self.catalog.get_by_text(COPY[self.locale][2], exact=True)).to_be_visible()
        expect(self.catalog.get_by_text("0 " + COPY[self.locale][1], exact=True)).to_be_visible()
        self.save("confirmed-empty")
        self.page.clock.fast_forward(30000)
        self.reply(PROJECTS)
        expect(self.cards).to_have_count(2)
        self.page.clock.fast_forward(30000)
        self.reply(failure=True)
        expect(self.catalog.get_by_role("status").filter(has_text=COPY[self.locale][5])).to_be_visible()
        expect(self.cards).to_have_count(2)
        self.save("stale-retained")
        retry = self.catalog.get_by_role("button", name=COPY[self.locale][4], exact=True)
        self.native(retry)
        self.page.keyboard.press("Enter")
        self.reply(PROJECTS)
        expect(self.cards).to_have_count(2)
        expect(self.catalog.get_by_role("status").filter(has_text=COPY[self.locale][5])).to_have_count(0)
        self.save("recovered")
        self.assertEqual(self.issues() + behavior, [])

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

    def test_catalog_essential_text_and_native_safe_popups(self):
        self.run_flow("populated")

    def test_catalog_unknown_count_empty_and_stale_retention(self):
        self.run_flow("states")


if __name__ == "__main__":
    ShowcaseCatalogTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
