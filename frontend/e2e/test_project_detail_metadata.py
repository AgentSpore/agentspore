"""Measure remaining project metadata through real tabs and synthetic public reads."""

import json
import re
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import USER, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_chat_ownership import PROJECT_ID, OwnershipHTTP
from test_project_detail_reads import CONTRIBUTOR, OWNERSHIP_DATA, PROJECT_DATA
from test_public_chat_accessibility import FOCUS

BASE = f"/api/v1/projects/{PROJECT_ID}"
TITLE = "SyntheticReadableProjectTitle" * 4
HANDLE = "synthetic-project-creator-with-a-readable-long-handle"
TECH = "SyntheticTechnologyWithoutSpaces0123456789" * 3
HUMAN = "SyntheticReadableContributorName" * 3
FALLBACK = "synthetic-fallback-member-with-readable-name"
AGENT_NAMES = ("SyntheticOwnershipAgentAlpha" * 4, "SyntheticOwnershipAgentBeta" * 4)
REPO, DEMO = "https://project-fixture.invalid/repository", "https://project-fixture.invalid/demo"
AUTH = {"token": "synthetic-project-metadata", "userId": "synthetic-reader", "email": "reader@example.invalid"}
PROJECT = dict(
    PROJECT_DATA, title=TITLE, agent_handle=HANDLE, tech_stack=[TECH, "TypeScript"], repo_url=REPO, deploy_url=DEMO
)
MEMBERS = [
    dict(CONTRIBUTOR, user_name=HUMAN, role="admin", contribution_points=1234567),
    dict(
        CONTRIBUTOR,
        id="00000000-0000-4000-8000-000000000008",
        user_id="synthetic-other-member",
        user_name="",
        user_email=FALLBACK + "@example.invalid",
        role="contributor",
        contribution_points=7654321,
    ),
]
SHARES = [
    {
        "agent_id": f"00000000-0000-4000-8000-{index + 20:012d}",
        "agent_name": name,
        "owner_user_id": None,
        "owner_name": None,
        "wallet_address": None,
        "contribution_points": points,
        "share_pct": share,
    }
    for index, (name, points, share) in enumerate(zip(AGENT_NAMES, (625, 375), (62.5, 37.5), strict=True))
]
OWNERSHIP = dict(OWNERSHIP_DATA, project_title=TITLE, contributors=SHARES)
COPY = {
    "en": (
        "Overview",
        "Contributors",
        "Ownership",
        "Human Contributors",
        "Agent Contributors",
        "Sign in",
        "sign in",
        "sign out",
    ),
    "ru": ("Обзор", "Участники", "Владение", "Участники-люди", "Участники-агенты", "Войти", "войти", "выйти"),
}
DECORATIVE = {"/", "|", "S"}


def invalid_target(item):
    if not item["contained"]:
        return True
    return item["name"] != "@" + HANDLE and (item["width"] < 44 or item["height"] < 44)


class MetadataHTTP(OwnershipHTTP):
    def __init__(self, base_url):
        super().__init__(base_url, "project")
        self.signed, self.calls, self.external = False, [], []

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if request.method == "GET" and request.url in (REPO, DEMO) and request.resource_type == "document":
            self.external.append(request.url)
            return route.fulfill(
                content_type="text/html",
                headers={"Content-Security-Policy": "default-src 'none'"},
                body="<!doctype html><link rel='icon' href='data:,'><p>Synthetic destination</p>",
            )
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if target.path.startswith("/api/"):
            return self.read(route, target)
        return super().route(route)

    def read(self, route, target):
        request = route.request
        if request.method != "GET" or target.query:
            return self.reject(route)
        payloads = {BASE: PROJECT, BASE + "/ownership": OWNERSHIP}
        if target.path in payloads:
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.calls.append(target.path)
            return route.fulfill(json=payloads[target.path])
        if target.path in (BASE + "/contributors", "/api/v1/auth/me"):
            return self.read_session(route, target.path)
        return self.reject(route)

    def read_session(self, route, path):
        expected = "Bearer " + AUTH["token"] if self.signed else None
        if route.request.headers.get("authorization") != expected or path.endswith("/auth/me") and not self.signed:
            return self.reject(route)
        self.calls.append(path)
        data = (
            dict(USER, id=AUTH["userId"], email=AUTH["email"])
            if path.endswith("/auth/me")
            else {"contributors": MEMBERS if self.signed else [], "total": len(MEMBERS) if self.signed else 0}
        )
        return route.fulfill(json=data)


class ProjectMetadataTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width):
        self.locale, self.width, self.errors, self.records, self.focus = locale, width, [], [], []
        self.context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        self.context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = MetadataHTTP(self.options.base_url)
        self.addCleanup(self.http.close, self.context)
        self.context.route("**/*", self.http.route)
        self.page = self.context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.heading = self.page.get_by_role("heading", level=1, name=TITLE, exact=True)
        expect(self.heading).to_be_visible()
        self.nav = self.page.locator('a[href="/projects"]:not(header *)').locator("../..")
        self.hero = self.heading.locator("../..")
        self.settle()

    def settle(self):
        self.page.evaluate("""async()=>{await document.fonts.ready;
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);} """)

    def place(self, root):
        root.scroll_into_view_if_needed()
        root.evaluate(
            "el=>{const h=document.querySelector('header')?.getBoundingClientRect().bottom||0;window.scrollBy(0,el.getBoundingClientRect().top-h-16)}"
        )
        self.settle()

    def native(self, target, activate=False):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.place(target)
        self.focus.append(target.evaluate(FOCUS))
        if activate:
            self.page.keyboard.press("Enter")
            self.settle()

    def tab(self, index):
        target = self.page.get_by_role("button", name=re.compile("^" + COPY[self.locale][index] + r"(?:\s|$)"))
        self.native(target, activate=True)
        expect(target).to_have_attribute("aria-pressed", "true")

    def save(self, state, root):
        self.settle()
        self.place(root)
        metrics = root.evaluate(METRICS)
        metrics["text"] = [item for item in metrics["text"] if item["text"] not in DECORATIVE]
        metrics["clipped"] = [item for item in metrics["clipped"] if item["text"] not in DECORATIVE | {TITLE.lower()}]
        metrics["ellipsis"] = root.evaluate("""root=>[...root.querySelectorAll('*')].filter(el=>{
            const s=getComputedStyle(el);return s.textOverflow==='ellipsis'&&el.scrollWidth>el.clientWidth&&
            el.getClientRects().length&&s.visibility!=='hidden'&&Number(s.opacity)!==0;
            }).map(el=>el.textContent.trim())""")
        metrics["ellipsis"] = [text for text in metrics["ellipsis"] if text != TITLE.lower()]
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "focus": list(self.focus),
            "calls": list(self.http.calls),
            "external": list(self.http.external),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"project-metadata-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def popup(self, url):
        target = self.hero.locator(f'a[href="{url}"]')
        expect(target).to_have_attribute("target", "_blank")
        self.assertEqual(set((target.get_attribute("rel") or "").split()), {"noopener", "noreferrer"})
        self.native(target)
        with self.context.expect_page() as opened:
            self.page.keyboard.press("Enter")
        popup = opened.value
        popup.wait_for_load_state("domcontentloaded")
        self.assertEqual(popup.url, url)
        popup.close()

    def initial_metadata(self):
        for target in (
            self.nav.locator('a[href="/"]'),
            self.nav.locator('a[href="/projects"]'),
            self.nav.get_by_role("button", name=COPY[self.locale][6], exact=True),
        ):
            self.native(target)
        self.save("navigation-anonymous", self.nav)
        expect(self.heading).to_have_text(TITLE)
        creator = self.hero.locator(f'a[href="/agents/{PROJECT["creator_agent_id"]}"]')
        expect(creator).to_have_text("@" + HANDLE)
        self.native(creator)
        self.popup(DEMO)
        self.popup(REPO)
        self.save("hero", self.hero)
        expect(self.main.get_by_text(TECH, exact=True)).to_be_visible()
        self.save("overview", self.main)
        self.tab(1)
        empty = self.main.get_by_role("button", name=COPY[self.locale][5], exact=True)
        self.native(empty)
        self.save("empty-inline-sign-in", self.main)

    def populated_members(self):
        self.http.signed = True
        self.page.evaluate(
            "value=>{localStorage.setItem('auth',JSON.stringify(value));window.dispatchEvent(new Event('storage'));}",
            AUTH,
        )
        expect(self.main.get_by_text(HUMAN, exact=True)).to_be_visible()
        expect(self.main.get_by_text(FALLBACK, exact=True)).to_be_visible()
        self.native(self.nav.get_by_role("button", name=COPY[self.locale][7], exact=True))
        self.save("navigation-signed", self.nav)
        self.save("human-contributors", self.main)
        for member in MEMBERS:
            value = self.page.evaluate(
                "n=>new Intl.NumberFormat(document.documentElement.lang).format(n)", member["contribution_points"]
            )
            expect(self.main.get_by_text(re.compile("^" + re.escape(value) + r"\s"))).to_have_count(1)
        self.tab(2)
        for name in AGENT_NAMES:
            expect(self.main.get_by_text(name, exact=True)).to_be_visible()
        for value in ("62.5%", "37.5%"):
            expect(self.main.get_by_text(value, exact=True)).to_be_visible()
        total = self.page.evaluate("()=>new Intl.NumberFormat(document.documentElement.lang).format(1000)")
        expect(self.main.get_by_text(re.compile("^" + re.escape(total) + r"\s"))).to_have_count(1)
        self.save("agent-ownership", self.main)

    def metric_issues(self, record):
        metrics = record["metrics"]
        issues = [{"font": item} for item in metrics["text"] if item["size"] < 12]
        issues.extend({"target": item} for item in metrics["targets"] if invalid_target(item))
        issues.extend({"clip": item} for item in metrics["clipped"])
        issues.extend({"ellipsis": text} for text in metrics["ellipsis"])
        if metrics["document_width"] > self.width:
            issues.append({"document_width": metrics["document_width"]})
        return [{"state": record["state"], **issue} for issue in issues]

    def run_flow(self):
        self.initial_metadata()
        self.populated_members()
        self.assertEqual(self.http.external, [DEMO, REPO])
        self.assertEqual(self.http.calls.count(BASE), 1)
        self.assertEqual(self.http.calls.count(BASE + "/ownership"), 1)
        self.assertEqual(self.http.calls.count(BASE + "/contributors"), 2)
        issues = [issue for record in self.records for issue in self.metric_issues(record)]
        issues.extend(
            {"focus": value} for value in self.focus if not all(value[key] for key in ("active", "visible", "painted"))
        )
        self.assertEqual(issues, [])

    def test_remaining_metadata_wrap_targets_and_native_navigation(self):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        self.run_flow()
                    finally:
                        self.doCleanups()


if __name__ == "__main__":
    ProjectMetadataTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
