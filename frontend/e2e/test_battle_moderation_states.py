"""Freeze the existing moderation queue with synthetic HTTP and native keyboard input."""

import json
import unittest
from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse

import test_battle_entry_states as entry
import test_battles_accessibility as browser
from auth_form_fixtures import AuthHTTP
from playwright.sync_api import expect
from test_battles_data_accessibility import TEXT

QUEUE = "/api/v1/battles/tasks/moderation"
ROWS = [
    {
        "id": f"00000000-0000-4000-8000-{800 + index:012d}",
        "title": f"Synthetic task {index} — " + "Long moderation title " * 4,
        "prompt": "Synthetic prompt " * (40 if index == 0 else 3),
        "rubric": [
            {
                "key": "correctness",
                "weight": 1234,
                "description": "Synthetic criterion " * 8,
            }
        ]
        if index == 0
        else [],
        "category": "backend",
        "difficulty": "hard",
        "status": "quarantine",
        "author_user_id": None if index == 0 else "00000000-0000-4000-8000-000000000899",
        "validation_reason": "Synthetic validator note " * 8 if index == 0 else None,
        "validation_verdict": None,
        "quarantine_battles": 1234 if index == 0 else 0,
        "use_count": 5,
        "settled_battles": 1200 if index == 0 else 0,
        "decisive_battles": 999 if index == 0 else 0,
        "created_at": "2026-10-20T00:00:00Z",
    }
    for index in range(2)
]
VISIBLE_TEXT = TEXT.replace(
    "if (!text ||", "if (el.closest('.battle-disclosure:not([data-open])') || !text ||"
)


class ModerationHTTP(AuthHTTP):
    """Hold only exact queue reads and the two task-action contracts."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.reads, self.writes = [], []
        self.responses = []

    def accepts_page(self, request, target):
        if target.path == "/battles/moderation":
            query = parse_qs(target.query, keep_blank_values=True)
            return (
                request.method == "GET"
                and set(query) <= {"_rsc"}
                and all(len(v) == 1 and v[0] for v in query.values())
                and (not query or request.headers.get("rsc") == "1")
            )
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin or target.query:
            super().route(route)
            return
        if request.method == "GET" and target.path == QUEUE and request.post_data is None:
            self.reads.append(route)
        elif request.method == "POST" and target.path in {
            f"/api/v1/battles/tasks/{row['id']}/{action}"
            for row in ROWS
            for action in ("approve", "reject")
        }:
            approve = target.path.endswith("/approve")
            valid = (
                request.post_data is None and "content-type" not in request.headers
                if approve
                else request.post_data
                == json.dumps({"reason": "Synthetic rejection"}, separators=(",", ":"))
                and request.headers.get("content-type") == "application/json"
            )
            if not valid:
                self.reject(route)
                return
            self.writes.append(route)
        else:
            super().route(route)
            return
        self.pending.append(route)

    def resolve(self, route, outcome):
        self.responses.append(
            {
                "path": urlparse(route.request.url).path,
                "outcome": len(outcome) if isinstance(outcome, list) else outcome,
            }
        )
        if outcome == "network":
            route.abort("failed")
        elif outcome == "json":
            route.fulfill(status=200, content_type="application/json", body="{")
        elif isinstance(outcome, int):
            route.fulfill(status=outcome, json={"detail": "Synthetic response"})
        else:
            route.fulfill(json=outcome)
        self.pending.remove(route)

    def close(self, context):
        while self.pending:
            self.resolve(self.pending[-1], "network")
        context.close()


class BattleModerationTests(unittest.TestCase):
    """Measure the finite queue states without inheriting unrelated browser tests."""

    label = entry.BattleEntryTests.label
    audit = entry.BattleEntryTests.audit
    keyboard_action = entry.BattleEntryTests.keyboard_action

    @classmethod
    def setUpClass(cls):
        browser.BattleAccessibilityTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.BattleAccessibilityTests.tearDownClass.__func__(cls)

    @contextmanager
    def queue(self, layout, tag):
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
        context.add_init_script("localStorage.setItem('access_token', 'synthetic-browser-test')")
        self.http, self.records, self.page_errors = (
            ModerationHTTP(self.options.base_url),
            {},
            [],
        )
        context.route("**/*", self.http.route)
        self.addCleanup(self.http.close, context)
        page = context.new_page()
        page.set_default_timeout(7000)
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.expose_function(
            "__moderationCounts", lambda: [len(self.http.reads), len(self.http.writes)]
        )
        try:
            page.goto(self.options.base_url + "/battles/moderation")
            self.wait_count(page, 0, 1)
            yield page
        finally:
            self.save(page, tag)
            self.http.close(context)

    def wait_count(self, page, index, count):
        page.wait_for_function(
            "async ([i,n]) => (await window.__moderationCounts())[i] === n",
            arg=[index, count],
        )

    def save(self, page, tag):
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        self.records.update(
            text=page.locator("main").evaluate(VISIBLE_TEXT),
            document_width=page.evaluate("document.documentElement.scrollWidth"),
            unexpected=self.http.unexpected,
            page_errors=self.page_errors,
            responses=self.http.responses,
            disabled=page.locator("main button:disabled").all_text_contents(),
            reads=[urlparse(r.request.url).path for r in self.http.reads],
            writes=[
                {"path": urlparse(r.request.url).path, "body": r.request.post_data}
                for r in self.http.writes
            ],
            contrast={"status": "unknown", "reason": "Paint and opacity not measured"},
        )
        target = self.options.screenshots / f"{tag}-{self.locale}-{self.width}"
        target.with_suffix(".json").write_text(
            json.dumps(self.records, ensure_ascii=False, indent=2)
        )
        page.screenshot(path=str(target.with_suffix(".png")), full_page=True)

    def shown(self, page, label):
        return page.get_by_text(self.label(label), exact=True)

    def button(self, page, label, index=0):
        return page.get_by_role("button", name=self.label(label), exact=True).nth(index)

    def activate(self, page, control):
        record = self.keyboard_action(page, control)
        self.records.setdefault("activations", []).append(record)
        self.assertTrue(record["focused"] and record["focusVisible"] and record["hit"], record)
        page.keyboard.press("Enter")

    def load(self, page, outcome):
        self.http.resolve(self.http.reads[-1], outcome)
        if isinstance(outcome, list):
            expect(self.button(page, "Approve")).to_be_visible() if outcome else expect(
                self.shown(page, "Queue is empty")
            ).to_be_visible()
        else:
            label = (
                "Not authorized" if outcome in (401, 403) else "Failed to load the moderation queue"
            )
            expect(self.shown(page, label)).to_be_visible()

    def test_initial_states(self):
        for layout in entry.LAYOUTS:
            for outcome in ("loading", 401, 403, 503, "network", "json", []):
                tag = f"initial-{outcome}"
                with (
                    self.subTest(layout=layout, state=tag),
                    self.queue(layout, tag) as page,
                ):
                    expect(page.locator("main h1")).to_have_text(
                        self.label("Task Moderation Queue")
                    )
                    if outcome == "loading":
                        expect(page.locator("main .animate-pulse")).to_have_count(2)
                    else:
                        self.load(page, outcome)
                    expect(
                        page.get_by_role("button", name=self.label("Approve"), exact=True)
                    ).to_have_count(0)
                    self.audit(page, tag)

    def test_retry_states(self):
        for layout in entry.LAYOUTS:
            for outcome in ([], ROWS, 401, 403, 503):
                tag = f"retry-{outcome if isinstance(outcome, int) else len(outcome)}"
                with (
                    self.subTest(layout=layout, state=tag),
                    self.queue(layout, tag) as page,
                ):
                    self.load(page, 503)
                    self.activate(page, self.button(page, "Try again"))
                    self.wait_count(page, 0, 2)
                    expect(self.shown(page, "Failed to load the moderation queue")).to_be_visible()
                    expect(self.button(page, "Try again")).to_be_enabled()
                    expect(page.locator("main .animate-pulse")).to_have_count(0)
                    self.audit(page, tag + "-pending")
                    self.load(page, outcome)
                    self.audit(page, tag)

    def test_row_states(self):
        for layout in entry.LAYOUTS:
            with self.subTest(layout=layout), self.queue(layout, "rows") as page:
                self.load(page, ROWS)
                self.assert_stat_labels(page)
                expect(page.get_by_text(self.label("awaiting review"), exact=False)).to_be_visible()
                expect(page.get_by_text(self.label(" · harvested"), exact=False)).to_have_count(1)
                expect(page.get_by_text(ROWS[0]["validation_reason"], exact=False)).to_be_visible()
                self.audit(page, "rows-closed")
                for label, key, expanded in (
                    ("Show prompt", "Enter", True),
                    ("Show rubric", "Space", True),
                    ("Collapse prompt", "Enter", False),
                    ("Show prompt", "Space", True),
                ):
                    index = 1 if "rubric" in label else 0
                    control = page.locator("main button[aria-expanded]").nth(index)
                    expect(control).to_have_text(self.label(label))
                    self.keyboard_action(page, control)
                    page.keyboard.press(key)
                    expect(control).to_have_attribute("aria-expanded", str(expanded).lower())
                    self.audit(page, f"rows-{label}-{expanded}")
                preview = ROWS[0]["prompt"][:420] + "…"
                expect(page.get_by_text(preview, exact=True)).to_be_visible()
                expect(page.get_by_text(ROWS[0]["prompt"], exact=True)).to_have_count(0)
                short = self.button(page, "Show prompt", 0)
                self.activate(page, short)
                expect(page.get_by_text(ROWS[1]["prompt"], exact=True)).to_be_visible()
                self.audit(page, "rows-short-prompt")

    def assert_stat_labels(self, page):
        records = []
        for label in ("Quarantine battles", "Settled", "Decisive"):
            node = page.get_by_text(self.label(label), exact=True).first
            records.append(
                node.evaluate("""el => {
                const range = document.createRange(); range.selectNodeContents(el);
                const cell = el.parentElement.getBoundingClientRect();
                return {text: el.innerText, cell: {left: cell.left, right: cell.right},
                    lines: [...range.getClientRects()].map(r => ({left: r.left, right: r.right}))};
            }""")
            )
        self.records["stat_labels"] = records
        self.save(page, "stat-labels")
        for record in records:
            for line in record["lines"]:
                self.assertGreaterEqual(line["left"], record["cell"]["left"] - 1, record)
                self.assertLessEqual(line["right"], record["cell"]["right"] + 1, record)

    def test_queue_limit(self):
        for layout in entry.LAYOUTS:
            with self.subTest(layout=layout), self.queue(layout, "limit") as page:
                rows = [
                    {**ROWS[1], "id": f"00000000-0000-4000-8000-{900 + i:012d}"} for i in range(100)
                ]
                self.load(page, rows)
                expect(
                    page.get_by_text(self.label(" (oldest first; more behind these)"), exact=False)
                ).to_be_visible()
                expect(
                    page.get_by_role("button", name=self.label("Approve"), exact=True)
                ).to_have_count(100)
                self.audit(page, "limit")

    def start_action(self, page, action):
        control = self.button(page, action)
        count = len(self.http.writes) + 1
        if action == "Reject":
            page.once("dialog", lambda dialog: dialog.accept("  Synthetic rejection  "))
        self.activate(page, control)
        self.wait_count(page, 1, count)
        expect(self.button(page, "Approve")).to_be_disabled()
        expect(self.button(page, "Reject")).to_be_disabled()
        expect(self.button(page, "Approve", 1)).to_be_enabled()
        expect(self.button(page, "Show prompt")).to_be_enabled()
        self.audit(page, f"{action}-pending")

    def test_action_outcomes(self):
        for layout in entry.LAYOUTS:
            for action, outcome in (
                ("Approve", 401),
                ("Reject", 403),
                ("Approve", 500),
                ("Reject", "network"),
                ("Approve", 200),
                ("Reject", 200),
            ):
                tag = f"{action}-{outcome}"
                with (
                    self.subTest(layout=layout, state=tag),
                    self.queue(layout, tag) as page,
                ):
                    self.load(page, ROWS)
                    self.start_action(page, action)
                    self.http.resolve(self.http.writes[-1], outcome)
                    if outcome == 200:
                        expect(page.get_by_text(ROWS[0]["title"], exact=True)).to_have_count(0)
                        expect(page.get_by_text(ROWS[1]["title"], exact=True)).to_be_visible()
                        self.assertEqual(len(self.http.reads), 1)
                    else:
                        expect(page.locator("main [role=status]")).to_have_text(
                            self.label(
                                "That action did not go through. The task is unchanged — try again."
                            )
                        )
                        expect(self.button(page, action)).to_be_enabled()
                    self.audit(page, tag)
                    self.finish_actions(page, action, outcome)

    def finish_actions(self, page, action, previous):
        self.start_action(page, action) if previous != 200 else self.activate(
            page, self.button(page, "Approve")
        )
        if previous != 200:
            expect(page.locator("main [role=status]")).to_have_count(0)
        self.wait_count(page, 1, 2)
        self.http.resolve(self.http.writes[-1], 200)
        if previous != 200:
            expect(page.get_by_text(ROWS[0]["title"], exact=True)).to_have_count(0)
        else:
            expect(self.shown(page, "Queue is empty")).to_be_visible()
        self.audit(page, f"{action}-{previous}-recovered")

    def test_conflict_refetch(self):
        for layout in entry.LAYOUTS:
            for action, outcome in (
                ("Approve", ROWS),
                ("Reject", ROWS[1:]),
                ("Approve", []),
                ("Reject", 401),
                ("Approve", 403),
                ("Reject", 503),
            ):
                tag = f"conflict-{action}-{outcome if isinstance(outcome, int) else len(outcome)}"
                with (
                    self.subTest(layout=layout, state=tag),
                    self.queue(layout, tag) as page,
                ):
                    self.load(page, ROWS)
                    self.start_action(page, action)
                    self.http.resolve(self.http.writes[-1], 409)
                    self.wait_count(page, 0, 2)
                    expect(self.button(page, action)).to_be_enabled()
                    expect(page.get_by_text(ROWS[0]["title"], exact=True)).to_be_visible()
                    expect(page.locator("main [role=status]")).to_have_count(0)
                    self.audit(page, tag + "-pending")
                    self.load(page, outcome)
                    self.audit(page, tag)

    def test_native_prompt_cancel_and_blank(self):
        for layout in entry.LAYOUTS:
            with self.subTest(layout=layout), self.queue(layout, "prompt") as page:
                self.load(page, ROWS)
                for reason in (None, "", "   "):
                    with page.expect_event("dialog") as pending:
                        page.once(
                            "dialog",
                            lambda dialog: (
                                dialog.dismiss() if reason is None else dialog.accept(reason)
                            ),
                        )
                        self.activate(page, self.button(page, "Reject"))
                    self.assertEqual(pending.value.type, "prompt")
                    self.records.setdefault("dialog_messages", []).append(pending.value.message)
                    self.assertEqual(
                        pending.value.message, self.label("Reason for rejecting this task:")
                    )
                    expect(self.button(page, "Approve")).to_be_enabled()
                    self.assertEqual(self.http.writes, [])
                    self.audit(page, f"prompt-{reason}")
                with page.expect_event("dialog") as pending:
                    page.once("dialog", lambda dialog: dialog.accept("  Synthetic rejection  "))
                    self.activate(page, self.button(page, "Reject"))
                self.records["dialog_messages"].append(pending.value.message)
                self.assertEqual(pending.value.type, "prompt")
                self.assertEqual(
                    pending.value.message, self.label("Reason for rejecting this task:")
                )
                self.wait_count(page, 1, 1)
                route = self.http.writes[0]
                self.assertEqual(route.request.post_data_json, {"reason": "Synthetic rejection"})
                self.http.resolve(route, 200)
                expect(page.get_by_text(ROWS[0]["title"], exact=True)).to_have_count(0)
                expect(page.get_by_text(ROWS[1]["title"], exact=True)).to_be_visible()
                self.audit(page, "prompt-trimmed-success")


if __name__ == "__main__":
    BattleModerationTests.options, unittest_args = browser.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
