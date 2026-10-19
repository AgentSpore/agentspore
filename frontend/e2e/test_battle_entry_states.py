"""Exercise task submissions and demo entry on a loopback build with synthetic HTTP."""

import json
import re
import unittest
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import test_battles_accessibility as browser
from auth_form_fixtures import AuthHTTP
from playwright.sync_api import expect
from test_battles_data_accessibility import ACTION, TEXT

AGENT_ID = "00000000-0000-4000-8000-000000000711"
BATTLE_ID = "00000000-0000-4000-8000-000000000712"
LAYOUTS = [(locale, width) for locale in ("en", "ru") for width in (320, 390, 1280)]
SOURCE = (Path(__file__).resolve().parents[1] / "src/lib/i18n/battles.ts").read_text()
RUSSIAN = {
    json.loads(key): json.loads(value)
    for key, value in re.findall(
        r'^\s*("(?:[^"\\]|\\.)*"): ("(?:[^"\\]|\\.)*"),?$',
        SOURCE.split("  ru: {", 1)[1],
        re.MULTILINE,
    )
}
STATUSES = {
    "draft": "Draft",
    "ready": "In rated pool",
    "retired": "Retired from pool",
    "pending_validation": "Under review",
    "quarantine": "Quarantine (unrated)",
    "rejected": "Rejected",
}
ROWS = [
    {
        "id": f"00000000-0000-4000-8000-{index:012d}",
        "title": f"Synthetic {status}",
        "prompt": "Synthetic private prompt must not render",
        "category": "backend",
        "difficulty": "hard",
        "status": status,
        "validation_reason": reason,
        "quarantine_battles": 1234,
        "use_count": 5,
        "approved_at": "2026-10-19T09:00:00Z" if status == "ready" else None,
        "created_at": "2026-10-19T08:00:00Z",
    }
    for index, (status, reason) in enumerate(
        [
            (status, "title_empty" if status == "rejected" else None)
            for status in STATUSES
        ]
        + [("rejected", "synthetic_unknown_reason")],
        720,
    )
]


class EntryHTTP(AuthHTTP):
    """Hold only the three real entry requests; reject unknown API traffic."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.reads, self.writes, self.detail = [], [], []

    def accepts_page(self, request, target):
        query = parse_qs(target.query, keep_blank_values=True)
        if target.path == "/login" and "_rsc" in query and "next" in query:
            return (
                request.method == "GET"
                and request.headers.get("rsc") == "1"
                and set(query) == {"next", "_rsc"}
                and query["next"] in (["/battles/tasks"], ["/battles/demo"])
                and len(query["_rsc"]) == 1
                and bool(query["_rsc"][0])
            )
        pages = {
            "/battles/tasks",
            "/battles/demo",
            "/battles/tasks/new",
            "/battles/new",
            f"/battles/{BATTLE_ID}",
        }
        if target.path in pages:
            return (
                request.method == "GET"
                and set(query) <= {"_rsc"}
                and all(len(values) == 1 for values in query.values())
                and (not query or request.headers.get("rsc") == "1")
            )
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        same = (
            target.scheme == "http"
            and target.netloc == self.origin
            and not target.query
        )
        if (
            same
            and request.method == "GET"
            and target.path
            in {"/api/v1/battles/tasks/mine", "/api/v1/users/me/external-agents"}
        ):
            self.reads.append(route)
        elif (
            same
            and request.method == "POST"
            and target.path == "/api/v1/battles/demo"
            and request.post_data_json
            == {"agent_a_id": AGENT_ID, "task_category": None, "task_difficulty": None}
        ):
            self.writes.append(route)
        elif (
            same
            and request.method == "GET"
            and target.path
            in {
                f"/api/v1/battles/{BATTLE_ID}",
                "/api/v1/battles/tasks",
                "/api/v1/battles/contenders",
            }
        ):
            self.detail.append(route)
        else:
            super().route(route)
            return
        self.pending.append(route)

    def fulfill(self, route, **response):
        route.fulfill(**response)
        self.pending.remove(route)

    def abort(self, route):
        route.abort("failed")
        self.pending.remove(route)

    def close(self, context):
        while self.pending:
            self.abort(self.pending[-1])
        context.close()


class BattleEntryTests(unittest.TestCase):
    """Keep real entry states, keyboard effects and rendered geometry under test."""

    @classmethod
    def setUpClass(cls):
        browser.BattleAccessibilityTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.BattleAccessibilityTests.tearDownClass.__func__(cls)

    def label(self, key):
        return RUSSIAN[key] if self.locale == "ru" else key

    def open_entry(self, path, layout, anonymous=False):
        self.locale, self.width = layout
        context = self.browser.new_context(
            viewport={"width": self.width, "height": 900}, service_workers="block"
        )
        context.add_cookies(
            [
                {
                    "name": "agentspore_locale",
                    "value": self.locale,
                    "url": self.options.base_url,
                }
            ]
        )
        if not anonymous:
            context.add_init_script(
                "localStorage.setItem('access_token', 'synthetic-browser-test')"
            )
        self.http, self.records = EntryHTTP(self.options.base_url), {}
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        page = context.new_page()
        self.page_errors = []
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.expose_function(
            "__syntheticEntryCounts",
            lambda: [len(self.http.reads), len(self.http.writes)],
        )
        page.set_default_timeout(7000)
        if anonymous:
            page.goto(self.options.base_url + path)
        else:
            with page.expect_request(
                lambda r: (
                    urlparse(r.url).path
                    in {
                        "/api/v1/battles/tasks/mine",
                        "/api/v1/users/me/external-agents",
                    }
                )
            ):
                page.goto(self.options.base_url + path)
        return page

    def save(self, page, tag):
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        self.records.update(
            text=page.locator("main").evaluate(TEXT),
            document_width=page.evaluate("document.documentElement.scrollWidth"),
            unexpected=self.http.unexpected,
            reads=[urlparse(r.request.url).path for r in self.http.reads],
            writes=[r.request.post_data_json for r in self.http.writes],
            page_errors=self.page_errors,
            contrast={"status": "unknown", "reason": "Paint and opacity not measured"},
        )
        target = self.options.screenshots / f"{tag}-{self.locale}-{self.width}"
        target.with_suffix(".json").write_text(
            json.dumps(self.records, ensure_ascii=False, indent=2)
        )
        page.screenshot(path=str(target.with_suffix(".png")), full_page=True)

    def audit(self, page, tag):
        self.save(page, tag)
        controls = page.locator("main a[href], main button, main select")
        actions = []
        for control in controls.all():
            if not control.is_visible():
                continue
            control.scroll_into_view_if_needed()
            record = control.evaluate(ACTION)
            record["inline_exception"] = record["href"] == "/battles/new"
            if control.is_enabled():
                record["keyboard"] = self.keyboard_action(page, control)
            actions.append(record)
        self.records["actions"] = actions
        self.save(page, tag)
        for record in actions:
            if not record["inline_exception"]:
                self.assertGreaterEqual(record["width"], 44, record)
                self.assertGreaterEqual(record["height"], 44, record)
            if "keyboard" in record:
                focus = record["keyboard"]
                self.assertTrue(
                    focus["focused"] and focus["focusVisible"] and focus["hit"], focus
                )
                self.assertTrue(focus["outline"] or focus["boxShadow"] != "none", focus)
                self.assertGreaterEqual(focus["x"], -1, focus)
                self.assertLessEqual(focus["right"], self.width + 1, focus)
                self.assertGreaterEqual(
                    focus["top"], max(0, focus["headerBottom"]) - 1, focus
                )
                self.assertLessEqual(
                    focus["bottom"], focus["viewportHeight"] + 1, focus
                )
        self.assertTrue(self.records["text"])
        self.assertEqual([r for r in self.records["text"] if r["size"] < 12], [])
        self.assertLessEqual(self.records["document_width"], self.width)
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.page_errors, [])

    def keyboard_action(self, page, control):
        for _ in range(100):
            page.keyboard.press("Tab")
            if control.evaluate("el => el === document.activeElement"):
                break
        record = control.evaluate(ACTION)
        if record["href"] == "/battles/new":
            record["fragments"] = (
                control.evaluate("""el => [...el.getClientRects()].map(r => {
                const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                return {x: r.x, y: r.y, width: r.width, height: r.height,
                    hit: hit === el || el.contains(hit)};
            })""")
            )
            record["hit"] = bool(record["fragments"]) and all(
                r["hit"] for r in record["fragments"]
            )
        record["outline"] = control.evaluate(
            "el => {const s = getComputedStyle(el); return s.outlineStyle !== 'none' "
            "&& parseFloat(s.outlineWidth) > 0}"
        )
        return record

    def agents(self):
        active = {
            "id": AGENT_ID,
            "name": "Synthetic active agent",
            "handle": "synthetic-active",
            "specialization": "programmer",
            "model_provider": "synthetic",
            "model_name": "synthetic",
            "is_active": True,
            "is_hosted": False,
            "karma": 0,
            "projects_created": 0,
            "github_connected": False,
            "created_at": None,
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }
        return [
            active,
            {
                **active,
                "id": AGENT_ID[:-1] + "3",
                "name": "Synthetic inactive agent",
                "last_heartbeat": None,
            },
        ]

    def resolve_read(self, page, state, payload):
        page.wait_for_function(
            "async () => (await window.__syntheticEntryCounts())[0] === 1"
        )
        self.assertEqual(len(self.http.reads), 1)
        route = self.http.reads[0]
        if state == "network":
            self.http.abort(route)
        else:
            self.http.fulfill(
                route,
                status=503 if state == "error" else 200,
                json={"detail": "Synthetic unavailable"}
                if state == "error"
                else payload,
            )

    def task_case(self, state, layout):
        page = self.open_entry("/battles/tasks", layout)
        tag = f"tasks-{state}"
        try:
            expect(page.locator("main h1")).to_have_text(self.label("My Submissions"))
            if state != "loading":
                self.resolve_read(page, state, ROWS if state == "populated" else [])
            if state == "loading":
                expect(page.locator("main .animate-pulse")).to_have_count(2)
            elif state == "error":
                expect(page.locator("main [role=alert]")).to_contain_text("HTTP 503")
            elif state == "empty":
                expect(
                    page.get_by_text(
                        self.label("You have not submitted any tasks yet"), exact=True
                    )
                ).to_be_visible()
            else:
                for status, label in STATUSES.items():
                    expect(
                        page.get_by_text(self.label(label), exact=True).first
                    ).to_be_visible()
                    expect(
                        page.get_by_text(f"Synthetic {status}", exact=True).first
                    ).to_be_visible()
                expect(
                    page.get_by_text("synthetic_unknown_reason", exact=False)
                ).to_be_visible()
                expect(
                    page.get_by_text(self.label("Title is empty"), exact=False)
                ).to_be_visible()
                expect(
                    page.get_by_text(self.label("Unrated battles played:"), exact=False)
                ).to_contain_text("1")
                expect(
                    page.get_by_text(
                        self.label("Approved — now included in the rated pool"),
                        exact=False,
                    )
                ).to_be_visible()
                expect(page.get_by_text(ROWS[0]["prompt"], exact=True)).to_have_count(0)
            self.audit(page, tag)
        finally:
            self.save(page, tag)
            self.http.close(page.context)

    def demo_case(self, state, layout):
        page = self.open_entry("/battles/demo", layout)
        tag = f"demo-{state}"
        try:
            expect(page.locator("main h1")).to_have_text(self.label("Demo Battle"))
            submit = page.get_by_role(
                "button", name=self.label("Start demo battle"), exact=True
            )
            if state == "loading":
                expect(
                    page.get_by_text(self.label("Loading your agents…"), exact=True)
                ).to_be_visible()
                expect(submit).to_be_disabled()
            else:
                empty = state in ("empty", "error", "network")
                self.resolve_read(page, state, [] if empty else self.agents())
                expect(
                    page.get_by_text(self.label("Loading your agents…"), exact=True)
                ).to_have_count(0)
                if empty:
                    expect(page.locator("main select")).to_have_count(0)
                    expect(submit).to_be_disabled()
                    expect(
                        page.get_by_text(
                            self.label(
                                'You have no connected agents of your own. Demo battles are only available to non-hosted (self-run) agents — set one up in the "My agents" section.'
                            ),
                            exact=True,
                        )
                    ).to_be_visible()
                    self.records["existing_agent_load_outcome"] = state
                else:
                    self.select_demo(page, submit, state)
            self.audit(page, tag)
        finally:
            self.save(page, tag)
            self.http.close(page.context)

    def select_demo(self, page, submit, state):
        select = page.get_by_label(self.label("Your agent"), exact=True)
        expect(
            select.locator('option[value="' + AGENT_ID[:-1] + '3"]')
        ).to_be_disabled()
        expect(select.locator(f'option[value="{AGENT_ID}"]')).to_be_enabled()
        if state == "validation":
            submit.focus()
            page.keyboard.press("Enter")
            expect(select).to_have_attribute("aria-invalid", "true")
            error_id = select.get_attribute("aria-describedby")
            expect(page.locator(f'[id="{error_id}"]')).to_have_text(
                self.label("Select your agent")
            )
            self.assertEqual(self.http.writes, [])
        else:
            select.focus()
            select.select_option(AGENT_ID)
            expect(select).to_have_value(AGENT_ID)
            expect(
                page.get_by_role("img", name="Synthetic active agent", exact=True)
            ).to_be_visible()
            expect(select).not_to_have_attribute("aria-invalid", "true")

    def submit_case(self, outcome, layout):
        page = self.open_entry("/battles/demo", layout)
        tag = f"submit-{outcome}"
        try:
            expect(page.locator("main h1")).to_be_visible()
            self.resolve_read(page, "populated", self.agents())
            submit = page.get_by_role(
                "button", name=self.label("Start demo battle"), exact=True
            )
            self.select_demo(page, submit, "selected")
            with page.expect_request(lambda r: r.method == "POST"):
                submit.focus()
                page.keyboard.press("Enter")
            pending = page.get_by_role(
                "button", name=self.label("Creating demo battle…"), exact=True
            )
            expect(pending).to_be_disabled()
            page.wait_for_function(
                "async () => (await window.__syntheticEntryCounts())[1] === 1"
            )
            self.save(page, tag + "-pending")
            self.assertEqual(len(self.http.writes), 1)
            self.assertEqual(
                self.http.writes[0].request.post_data_json,
                {
                    "agent_a_id": AGENT_ID,
                    "task_category": None,
                    "task_difficulty": None,
                },
            )
            if outcome == "submitting":
                page.keyboard.press("Enter")
                self.assertEqual(len(self.http.writes), 1)
            else:
                self.resolve_post(page, outcome)
            if outcome == "success":
                self.save(page, tag)
                self.assertEqual(self.http.unexpected + self.page_errors, [])
            else:
                self.audit(page, tag)
        finally:
            self.save(page, tag)
            self.http.close(page.context)

    def resolve_post(self, page, outcome):
        route = self.http.writes[0]
        errors = {
            "503": "The demo opponent is temporarily unavailable — try again later.",
            "409": "Not enough fresh tasks for a demo battle — try again later.",
            "detail": "Synthetic admission denied",
            "fallback": "HTTP 500",
            "network": "Failed to fetch",
        }
        if outcome == "network":
            self.http.abort(route)
        elif outcome == "success":
            self.http.fulfill(route, status=201, json={"id": BATTLE_ID})
            expect(page).to_have_url(self.options.base_url + f"/battles/{BATTLE_ID}")
            self.records["destination"] = f"/battles/{BATTLE_ID}"
            return
        else:
            status = int(outcome) if outcome in ("503", "409") else 500
            self.http.fulfill(
                route,
                status=status,
                json={"detail": errors[outcome]} if outcome == "detail" else {},
            )
        alert = page.locator("main [role=alert]")
        expect(alert).to_contain_text(self.label("Failed to create the demo battle"))
        expect(alert).to_contain_text(
            self.label(errors[outcome])
            if outcome in ("503", "409")
            else errors[outcome]
        )
        expect(
            page.get_by_role("button", name=self.label("Start demo battle"), exact=True)
        ).to_be_enabled()

    def test_tasks_states(self):
        for layout in LAYOUTS:
            for state in ("loading", "error", "empty", "populated"):
                with self.subTest(layout=layout, state=state):
                    self.task_case(state, layout)

    def test_demo_agent_states(self):
        for layout in LAYOUTS:
            for state in (
                "loading",
                "empty",
                "error",
                "network",
                "selected",
                "validation",
            ):
                with self.subTest(layout=layout, state=state):
                    self.demo_case(state, layout)

    def test_demo_submit_states(self):
        for layout in LAYOUTS:
            for outcome in (
                "submitting",
                "503",
                "409",
                "detail",
                "fallback",
                "network",
                "success",
            ):
                with self.subTest(layout=layout, outcome=outcome):
                    self.submit_case(outcome, layout)

    def test_anonymous_redirects(self):
        for layout in LAYOUTS:
            for path in ("/battles/tasks", "/battles/demo"):
                page = self.open_entry(path, layout, anonymous=True)
                try:
                    expect(page).to_have_url(
                        self.options.base_url + "/login?next=" + path
                    )
                    self.save(page, "anonymous-" + path.rsplit("/", 1)[1])
                    self.assertEqual(self.http.reads + self.http.writes, [])
                    self.assertEqual(self.http.unexpected + self.page_errors, [])
                finally:
                    self.save(page, "anonymous-" + path.rsplit("/", 1)[1])
                    self.http.close(page.context)

    def test_entry_navigation(self):
        for layout in LAYOUTS:
            for source, key, destination in (
                ("tasks", "Suggest a task", "/battles/tasks/new"),
                ("tasks", "Suggest your first", "/battles/tasks/new"),
                ("demo", "Create a regular challenge", "/battles/new"),
            ):
                with self.subTest(layout=layout, source=source, link=key):
                    page = self.open_entry("/battles/" + source, layout)
                    tag = "navigation-" + key.replace(" ", "-")
                    try:
                        self.resolve_read(page, "empty", [])
                        link = page.get_by_role(
                            "link", name=self.label(key), exact=True
                        )
                        expect(link).to_be_visible()
                        self.audit(page, tag)
                        self.records["navigation_focus"] = self.keyboard_action(
                            page, link
                        )
                        self.save(page, tag)
                        expect(link).to_be_focused()
                        page.keyboard.press("Enter")
                        expect(page).to_have_url(self.options.base_url + destination)
                        self.records["destination"] = destination
                        self.assertEqual(self.http.unexpected + self.page_errors, [])
                    finally:
                        self.save(page, tag + "-destination")
                        self.http.close(page.context)


if __name__ == "__main__":
    BattleEntryTests.options, unittest_args = browser.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
