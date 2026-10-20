"""Measure agent metadata and exercise existing local GitHub filters through HTTP."""

import json
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agent_detail_states import AGENT, ENDPOINTS, PAGE, PAYLOADS, AgentReadHTTP
from test_agents_list import METRICS
from test_model_usage import MODEL_USAGE
from test_public_chat_accessibility import FOCUS

NAME = "SyntheticMetadataAgent" * 6
HANDLE = "synthetic-metadata-agent-with-readable-long-handle"
PROVIDER = "SyntheticModelProvider" * 4
MODEL = "SyntheticModelName" * 5
SKILL = "SyntheticPublicSkill" * 6
FIX = "Supplementary fix preview " * 12
BADGE_NAME = "SyntheticPublicBadge" * 4
BADGE_DESCRIPTION = "Synthetic badge description kept in title metadata."
DECORATIVE = {"/", "♦", "⚡", "≡", "✸", "⌥", "↑", "⌘", "✓", "↗", "⚑", "✦"}
PROFILE = dict(
    AGENT,
    name=NAME,
    handle=HANDLE,
    model_provider=PROVIDER,
    model_name=MODEL,
    skills=[SKILL, "Synthetic second skill"],
    karma=987654321,
    projects_created=123456789,
    code_commits=98765432,
    fork_count=56789012,
    dna_risk=7,
    dna_speed=6,
    dna_verbosity=5,
    dna_creativity=8,
)
TYPES = ("code_commit", "issue_closed", "issue_commented", "pull_request_created", "issue_disputed")
EVENTS = [
    {
        "id": f"00000000-0000-4000-8000-{30 + index:012d}",
        "action_type": kind,
        "description": f"Synthetic event {index} " + "ReadableEventDescription" * 6,
        "project_id": "00000000-0000-4000-8000-000000000002",
        "project_title": "SyntheticProjectTitle" * 5,
        "github_url": f"https://example.invalid/synthetic-event-{index}" if kind != "pull_request_created" else None,
        "pr_url": f"https://example.invalid/synthetic-pr-{index}" if kind == "pull_request_created" else None,
        "commit_sha": "0123456789abcdef0123456789abcdef01234567" if kind == "code_commit" else None,
        "branch": "synthetic-readable-branch-" * 3 if kind == "code_commit" else None,
        "issue_number": 1234567 if kind != "code_commit" else None,
        "commit_message": "Synthetic essential commit " + "ReadableCommitTitle" * 7 if kind == "code_commit" else None,
        "issue_title": "Synthetic essential issue " + "ReadableIssueTitle" * 7 if kind == "issue_closed" else None,
        "fix_description": FIX if kind == "issue_closed" else None,
        "issues_created": 12345 if kind == "issue_commented" else None,
        "created_at": "2026-10-20T09:00:00Z",
    }
    for index, kind in enumerate(TYPES)
]
TITLES = [event["commit_message"] or event["issue_title"] or event["description"] for event in EVENTS]
MODELS = ("synthetic/" + "ReadableModelAlpha" * 6, "synthetic/" + "ReadableModelBeta" * 6)
USAGE = dict(
    MODEL_USAGE,
    by_model=[{"model": model, "total_calls": count} for model, count in zip(MODELS, (3, 1), strict=True)],
    by_task=[
        {"model": model, "task_type": task, "call_count": count}
        for model, task, count in zip(MODELS, ("codegen", "review"), (3, 1), strict=True)
    ],
)
ACTIVITY = [
    {
        "id": "metadata-activity",
        "agent_id": AGENT["id"],
        "agent_name": NAME,
        "specialization": "programmer",
        "action_type": "code_commit",
        "description": "Synthetic activity " + "ReadableActivity" * 10,
        "project_id": "00000000-0000-4000-8000-000000000002",
        "metadata": {},
        "ts": "2026-10-20T09:00:00Z",
    }
]
BADGES = [
    {
        "badge_id": "synthetic-metadata-badge",
        "name": BADGE_NAME,
        "description": BADGE_DESCRIPTION,
        "icon": "✦",
        "category": "contribution",
        "rarity": "rare",
        "awarded_at": "2026-10-20T09:00:00Z",
    }
]
DATA = dict(
    PAYLOADS,
    profile=PROFILE,
    activity=ACTIVITY,
    models=USAGE,
    github={"activities": EVENTS, "count": len(EVENTS)},
    badges=BADGES,
)
COPY = {
    "en": (
        "Badges",
        "Agent DNA",
        "Activity Timeline",
        "GitHub Activity",
        "Model Usage",
        "All",
        "Commits",
        "Reviews",
        "Fixed",
        "Discussed",
        "PRs",
        "No events for this filter",
        "Message",
        "Copy ID",
    ),
    "ru": (
        "Значки",
        "ДНК агента",
        "История активности",
        "Активность в GitHub",
        "Использование моделей",
        "Все",
        "Коммиты",
        "Ревью",
        "Исправления",
        "Обсуждения",
        "PRs",
        "Для этого фильтра событий нет",
        "Сообщение",
        "Копировать ID",
    ),
}


class MetadataHTTP(AgentReadHTTP):
    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        key = ENDPOINTS.get((target.path, target.query))
        if request.method == "GET" and key:
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.calls.append({"key": key, "path": target.path, "query": target.query})
            return route.fulfill(json=DATA[key])
        return super().route(route)


class AgentMetadataTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width):
        self.locale, self.width, self.errors, self.records, self.focus, self.semantic = locale, width, [], [], [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = MetadataHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.options.base_url + PAGE, wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        expect(self.main.get_by_role("heading", level=1, name=NAME, exact=True)).to_be_visible()
        self.settle()
        for value in (987654321, 123456789, 98765432, 56789012):
            formatted = self.page.evaluate("n=>new Intl.NumberFormat(document.documentElement.lang).format(n)", value)
            counter = self.main.get_by_text(formatted, exact=True)
            expect(counter).to_be_visible()
            rects = counter.evaluate("""el=>{const range=document.createRange();range.selectNodeContents(el);
                return [...range.getClientRects()].filter(r=>r.width&&r.height)
                    .map(r=>({x:r.x,y:r.y,width:r.width,height:r.height}));}""")
            self.assertEqual(len(rects), 1, f"Stat counter {formatted!r} must occupy one line: {rects}")
        expect(self.main.get_by_role("region", name="Blog" if locale == "en" else "Блог", exact=True)).to_be_visible()
        self.initial_count = self.request_count()
        self.assertEqual(self.initial_count, 6)

    def settle(self):
        self.page.evaluate("""async()=>{await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);} """)

    def request_count(self):
        return len(self.http.calls) + len(self.http.blog_calls)

    def section(self, index):
        return self.main.get_by_text(COPY[self.locale][index], exact=True).locator(
            "../../.." if index == 3 else "../.."
        )

    def save(self, state, root):
        self.settle()
        root.scroll_into_view_if_needed()
        root.evaluate(
            "el=>{const h=document.querySelector('header')?.getBoundingClientRect().bottom||0;window.scrollBy(0,el.getBoundingClientRect().top-h-16)}"
        )
        self.settle()
        metrics = root.evaluate(METRICS)
        metrics["text"] = [text for text in metrics["text"] if text["text"] not in DECORATIVE]
        metrics["ellipsis"] = root.evaluate("""root=>[...root.querySelectorAll('*')].filter(el=>{
            const s=getComputedStyle(el);return s.textOverflow==='ellipsis'&&el.scrollWidth>el.clientWidth&&
            el.getClientRects().length&&s.visibility!=='hidden'&&Number(s.opacity)!==0;
            }).map(el=>({text:el.textContent.trim(),title:el.title}))""")
        metrics["clipped"] = [
            text for text in metrics["clipped"] if text["text"] not in DECORATIVE and text["text"] != FIX.strip()
        ]
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "focus": list(self.focus),
            "filter_state": list(self.semantic),
            "request_count": self.request_count(),
            "calls": list(self.http.calls),
            "blog_calls": list(self.http.blog_calls),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"agent-metadata-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def native(self, target, activate=False):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.focus.append(target.evaluate(FOCUS))
        if activate:
            self.page.keyboard.press("Enter")

    def assert_destinations(self):
        home, agents = self.main.locator('a[href="/"]'), self.main.locator('a[href="/agents"]')
        dm = self.main.get_by_role("link", name=COPY[self.locale][12], exact=True)
        expect(dm).to_have_attribute("href", PAGE + "/chat")
        copy = self.main.get_by_role("button", name=COPY[self.locale][13], exact=True)
        for target in (home, agents, dm, copy):
            self.native(target)
        for event in EVENTS:
            href = event["github_url"] or event["pr_url"]
            link = self.github.locator(f'a[href="{href}"]')
            expect(link).to_have_count(1)
            expect(link).to_have_attribute("target", "_blank")
            self.assertEqual(set((link.get_attribute("rel") or "").split()), {"noopener", "noreferrer"})
        self.native(self.github.locator('a[href="https://example.invalid/synthetic-event-0"]'))

    def filter_events(self):
        expected = (TYPES, ("code_commit",), (), ("issue_closed",), ("issue_commented",), ("pull_request_created",))
        for index, types in enumerate(expected):
            target = self.github.get_by_role("button", name=COPY[self.locale][index + 5], exact=True)
            self.native(target, activate=True)
            for kind, title in zip(TYPES, TITLES, strict=True):
                expect(self.github.get_by_text(title, exact=True)).to_have_count(1 if kind in types else 0)
            empty = self.github.get_by_text(COPY[self.locale][11], exact=True)
            expect(empty).to_have_count(0 if types else 1)
            self.semantic.append(
                {
                    "filter": index,
                    "pressed": [
                        button.get_attribute("aria-pressed") for button in self.github.get_by_role("button").all()
                    ],
                }
            )
            self.assertEqual(self.request_count(), self.initial_count)
            if not types:
                self.save("filter-no-match", self.github)
        all_button = self.github.get_by_role("button", name=COPY[self.locale][5], exact=True)
        self.native(all_button, activate=True)
        for title in TITLES:
            expect(self.github.get_by_text(title, exact=True)).to_have_count(1)
        self.assertEqual(self.request_count(), self.initial_count)
        self.semantic.append(
            {
                "filter": 0,
                "pressed": [button.get_attribute("aria-pressed") for button in self.github.get_by_role("button").all()],
            }
        )
        self.save("filters-restored", self.github)

    def preserved_excerpts(self):
        for model in MODELS:
            entries = self.models.locator(f'[title="{model}"]')
            expect(entries).to_have_count(2)
            for entry in entries.all():
                expect(entry).to_have_text(model.split("/")[-1])
        badge = self.section(0).get_by_text(BADGE_NAME, exact=True).locator("../..")
        expect(badge).to_have_attribute("title", BADGE_NAME + " — " + BADGE_DESCRIPTION)
        expect(self.github.get_by_text(FIX, exact=True)).to_have_count(1)
        expect(self.main.get_by_text(str(AGENT["id"])[:8] + "...", exact=True)).to_have_count(1)

    def metric_issues(self, record):
        metrics = record["metrics"]
        issues = [{"font": item} for item in metrics["text"] if item["size"] < 12]
        issues.extend(
            {"target": item}
            for item in metrics["targets"]
            if item["width"] < 44 or item["height"] < 44 or not item["contained"]
        )
        issues.extend({"clip": item} for item in metrics["clipped"])
        issues.extend({"ellipsis": item} for item in metrics["ellipsis"])
        if metrics["document_width"] > self.width:
            issues.append({"document_width": metrics["document_width"]})
        return [{"state": record["state"], **issue} for issue in issues]

    def populated_flow(self):
        heading = self.main.get_by_role("heading", level=1, name=NAME, exact=True)
        self.save("breadcrumb", self.main.locator('a[href="/"]').locator(".."))
        self.save("identity", heading.locator("../../.."))
        self.save("metadata", self.main.get_by_text("@" + HANDLE, exact=True).last.locator("../.."))
        for index, state in enumerate(("badges", "dna", "activity")):
            self.save(state, self.section(index))
        self.github, self.models = self.section(3), self.section(4)
        self.save("github-all", self.github)
        self.save("models", self.models)
        self.assert_destinations()
        self.filter_events()
        self.preserved_excerpts()
        issues = [issue for record in self.records for issue in self.metric_issues(record)]
        issues.extend(
            {"focus": item} for item in self.focus if not all(item[k] for k in ("active", "visible", "painted"))
        )
        for state in self.semantic:
            if state["pressed"] != ["true" if i == state["filter"] else "false" for i in range(6)]:
                issues.append({"filter_state": state})
        self.assertEqual(issues, [])

    def test_metadata_geometry_and_six_local_github_filters(self):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        self.populated_flow()
                    finally:
                        self.doCleanups()


if __name__ == "__main__":
    AgentMetadataTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
