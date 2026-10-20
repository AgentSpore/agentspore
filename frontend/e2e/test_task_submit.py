"""Exercise task submission outcomes against a loopback build with synthetic HTTP."""

import json
import unittest
from urllib.parse import parse_qs, urlparse

import auth_form_fixtures as auth
import test_battle_entry_states as entry
import test_battles_accessibility as browser
import test_battles_data_accessibility as data
from playwright.sync_api import expect

PATH = "/battles/tasks/new"
PAYLOAD = {
    "title": "Synthetic task",
    "prompt": (
        "Implement a deterministic cache with expiry and document its behavior. " * 2
    ).strip(),
    "category": "backend",
    "difficulty": "hard",
    "rubric": [{"key": "correctness", "description": "Handles expiry correctly"}],
}
ERRORS = {
    "429": "Daily submission limit reached (max 5 per day) — try again tomorrow.",
    "409": "The exact same task was just submitted — wait a moment and try again.",
    "detail": "Synthetic admission denied",
    "fallback": "HTTP 500",
    "network": "Failed to fetch",
}
PENDING = (
    "The task was saved and is waiting for automatic review. It cannot be used in battles yet."
)
PENDING_RU = "Задание сохранено и ожидает автоматической проверки. Пока оно не используется в боях."


class TaskHTTP(entry.EntryHTTP):
    """Hold task POSTs and allow only the observed submission navigation contracts."""

    def accepts_page(self, request, target):
        query = parse_qs(target.query, keep_blank_values=True)
        if target.path == "/login" and "next" in query:
            return (
                request.method == "GET"
                and query.get("next") == [PATH]
                and set(query) <= {"next", "_rsc"}
                and all(len(v) == 1 and bool(v[0]) for v in query.values())
                and ("_rsc" not in query or request.headers.get("rsc") == "1")
            )
        if target.path in {PATH, "/battles/tasks"}:
            return (
                request.method == "GET"
                and set(query) <= {"_rsc"}
                and all(len(v) == 1 and bool(v[0]) for v in query.values())
                and (not query or request.headers.get("rsc") == "1")
            )
        return (
            target.path
            in (
                "/ /workspace /projects /agents /showcase /dashboard "
                "/chat /battles /teams /blog /login /forgot-password"
            ).split()
            and set(query) == {"_rsc"}
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and auth.AuthHTTP.accepts_page(self, request, target)
        )

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        same = target.scheme == "http" and target.netloc == self.origin and not target.query
        if same and target.path == "/api/v1/battles/tasks" and request.method == "POST":
            self.writes.append(route)
            self.pending.append(route)
        elif same and target.path == "/api/v1/battles/tasks/mine" and request.method == "GET":
            route.fulfill(json=[])
        elif request.method == "GET":
            auth.AuthHTTP.route(self, route)
        else:
            self.reject(route)


class TaskSubmitTests(unittest.TestCase):
    """Keep TS.1–9 on the actual component, without inheriting other test cases."""

    keyboard_action = entry.BattleEntryTests.keyboard_action

    @classmethod
    def setUpClass(cls):
        browser.BattleAccessibilityTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.BattleAccessibilityTests.tearDownClass.__func__(cls)

    def label(self, key):
        if key == PENDING:
            return PENDING_RU if self.locale == "ru" else PENDING
        template = key.replace("Criterion 1:", "Criterion {number}:")
        template = template.replace("(max 5 per day)", "(max {limit} per day)")
        translated = entry.RUSSIAN.get(template, template) if self.locale == "ru" else template
        return translated.replace("{number}", "1").replace("{limit}", "5")

    def open_form(self, layout, tag, anonymous=False):
        self.doCleanups()
        self.locale, self.width = layout
        context = self.browser.new_context(
            viewport={"width": self.width, "height": 900}, service_workers="block"
        )
        context.add_cookies(
            [{"name": "agentspore_locale", "value": self.locale, "url": self.options.base_url}]
        )
        if not anonymous:
            context.add_init_script(
                "localStorage.setItem('access_token', 'synthetic-browser-test')"
            )
        self.http, self.records, self.page_errors = TaskHTTP(self.options.base_url), {}, []
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(7000)
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.addCleanup(self.http.close, context)
        self.addCleanup(self.save, page, tag + "-final")
        page.goto(self.options.base_url + PATH)
        return page

    def save(self, page, tag):
        page.evaluate("document.fonts.ready")
        self.records.update(
            text=page.locator("main").evaluate(data.TEXT),
            document_width=page.evaluate("document.documentElement.scrollWidth"),
            unexpected=self.http.unexpected,
            page_errors=self.page_errors,
            writes=[r.request.post_data_json for r in self.http.writes],
        )
        target = self.options.screenshots / f"{tag}-{self.locale}-{self.width}"
        target.with_suffix(".json").write_text(
            json.dumps(self.records, ensure_ascii=False, indent=2)
        )
        page.screenshot(path=str(target.with_suffix(".png")))

    def audit(self, page, tag, controls):
        actions = []
        for control in controls.all():
            if control.is_visible() and control.is_enabled():
                actions.append(self.keyboard_action(page, control))
                self.records["actions"] = actions
                self.save(page, tag + f"-focus-{len(actions)}")
        self.save(page, tag)
        self.assertTrue(self.records["text"])
        self.assertEqual([r for r in self.records["text"] if r["size"] < 12], [])
        self.assertLessEqual(self.records["document_width"], self.width)
        self.assertEqual(self.http.unexpected + self.page_errors, [])
        for action in actions:
            self.assertGreaterEqual(action["width"], 44, action)
            self.assertGreaterEqual(action["height"], 44, action)
            self.assertTrue(action["focused"] and action["focusVisible"] and action["hit"], action)
            self.assertTrue(action["outline"] or action["boxShadow"] != "none", action)
            self.assertGreaterEqual(action["x"], -1, action)
            self.assertLessEqual(action["right"], self.width + 1, action)
            self.assertGreaterEqual(action["top"], max(0, action["headerBottom"]) - 1, action)
            self.assertLessEqual(action["bottom"], action["viewportHeight"] + 1, action)

    def fields(self, page):
        return [
            page.get_by_label(self.label(key), exact=True)
            for key in (
                "Title",
                "Task text",
                "Category",
                "Criterion 1: key",
                "Criterion 1: description",
            )
        ]

    def fill_form(self, page):
        values = [PAYLOAD[key] for key in ("title", "prompt", "category")]
        values.extend(PAYLOAD["rubric"][0].values())
        for control, value in zip(self.fields(page), values, strict=True):
            control.fill("  " + value + "  ")
        hard = page.get_by_role("radio", name=self.label("Hard"), exact=True)
        self.keyboard_action(page, hard)
        page.keyboard.press("Space")

    def submit(self, page, tag):
        button = page.get_by_role("button", name=self.label("Submit task"), exact=True)
        self.keyboard_action(page, button)
        self.save(page, tag + "-before")
        expect(button).to_be_focused()
        page.keyboard.press("Enter")
        return button

    def start_post(self, page, tag):
        count = len(self.http.writes)
        self.fill_form(page)
        with page.expect_request(
            lambda r: r.method == "POST" and urlparse(r.url).path == "/api/v1/battles/tasks"
        ):
            self.submit(page, tag)
        pending = page.get_by_role("button", name=self.label("Submitting…"), exact=True)
        self.records["pending"] = pending.evaluate(data.ACTION)
        self.save(page, tag + "-pending")
        expect(pending).to_be_disabled()
        for axis in ("width", "height"):
            self.assertGreaterEqual(self.records["pending"][axis], 44)
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")
        page.evaluate(
            "() => new Promise(resolve => "
            "requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        self.assertEqual(len(self.http.writes), count + 1)
        self.assertEqual(self.http.writes[-1].request.post_data_json, PAYLOAD)
        return self.http.writes[-1]

    def test_validation_and_limits(self):
        for layout in (("en", 1280), ("ru", 320)):
            with self.subTest(layout=layout):
                page = self.open_form(layout, "validation")
                self.submit(page, "validation")
                self.save(page, "validation-empty")
                controls = self.fields(page)
                for control in [*controls, page.get_by_role("radiogroup")]:
                    with self.subTest(
                        field=control.get_attribute("aria-label") or control.get_attribute("id")
                    ):
                        expect(control).to_have_attribute("aria-invalid", "true")
                        error_id = control.get_attribute("aria-describedby")
                        self.assertTrue(error_id)
                        expect(page.locator(f'[id="{error_id}"]')).not_to_be_empty()
                for control, limit in zip(controls[:3], (300, 20000, 50), strict=True):
                    expect(control).to_have_attribute("maxlength", str(limit))
                self.fill_form(page)
                controls[1].fill("x" * 79)
                self.save(page, "validation-short")
                expect(controls[1]).to_have_attribute("aria-invalid", "true")
                expect(
                    page.get_by_role("button", name=self.label("Submit task"), exact=True)
                ).to_be_disabled()
                controls[1].fill("x" * 80)
                self.save(page, "validation-valid")
                expect(controls[1]).not_to_have_attribute("aria-invalid", "true")
                self.assertEqual(self.http.writes, [])
                self.audit(
                    page,
                    "validation",
                    page.get_by_role("button", name=self.label("Submit task"), exact=True),
                )
                self.doCleanups()

    def test_anonymous_redirect(self):
        for layout in (("en", 1280), ("ru", 320)):
            page = self.open_form(layout, "anonymous", anonymous=True)
            expect(page).to_have_url(self.options.base_url + "/login?next=" + PATH)
            self.save(page, "anonymous")
            self.assertEqual(self.http.writes + self.http.unexpected + self.page_errors, [])
            self.doCleanups()

    def test_api_failure_preserves_form_and_retry(self):
        for layout in (("en", 1280), ("ru", 320)):
            for outcome, message in ERRORS.items():
                with self.subTest(layout=layout, outcome=outcome):
                    page = self.open_form(layout, "error-" + outcome)
                    route = self.start_post(page, "error-" + outcome)
                    if outcome == "network":
                        self.http.abort(route)
                    else:
                        self.http.fulfill(
                            route,
                            status=int(outcome) if outcome.isdigit() else 500,
                            json={"detail": message} if outcome == "detail" else {},
                        )
                    alert = page.locator("main").get_by_role("alert")
                    expect(alert).to_be_visible()
                    self.records["error_bounds"] = alert.evaluate(data.ACTION)
                    self.save(page, "error-" + outcome)
                    expect(alert).to_contain_text(self.label("Failed to submit the task"))
                    expect(alert).to_contain_text(self.label(message))
                    bounds = self.records["error_bounds"]
                    self.assertGreaterEqual(
                        bounds["top"], max(0, bounds["headerBottom"]) - 1, bounds
                    )
                    self.assertLessEqual(bounds["bottom"], bounds["viewportHeight"] + 1, bounds)
                    values = [PAYLOAD[k] for k in ("title", "prompt", "category")]
                    values.extend(PAYLOAD["rubric"][0].values())
                    for control, value in zip(self.fields(page), values, strict=True):
                        expect(control).to_have_value("  " + value + "  ")
                    expect(
                        page.get_by_role("radio", name=self.label("Hard"), exact=True)
                    ).to_have_attribute("aria-checked", "true")
                    self.audit(
                        page,
                        "error-" + outcome,
                        page.get_by_role("button", name=self.label("Submit task"), exact=True),
                    )
                    self.submit(page, "retry-" + outcome)
                    expect(
                        page.get_by_role("button", name=self.label("Submitting…"), exact=True)
                    ).to_be_disabled()
                    self.assertEqual(len(self.http.writes), 2)
                    self.doCleanups()

    def result_case(self, layout, status, reason):
        tag = f"result-{status}-{reason}"
        page = self.open_form(layout, tag)
        route = self.start_post(page, tag)
        self.http.fulfill(
            route, status=201, json={"id": entry.ROWS[0]["id"], "status": status, "reason": reason}
        )
        heading = "Task rejected" if status == "rejected" else "Task accepted into quarantine"
        if status == "pending_validation":
            heading = "Under review"
        self.save(page, tag)
        expect(page.get_by_text(self.label(heading), exact=True)).to_be_visible()
        self.audit(page, tag, page.locator("main button"))
        message = (
            ("Title is empty" if reason == "title_empty" else reason or "No reason given.")
            if status == "rejected"
            else (
                "The task can already be used in unrated battles and is awaiting moderator review "
                "before it joins the rated pool."
            )
        )
        if status == "pending_validation":
            message = PENDING
        expect(page.get_by_text(self.label(message), exact=True)).to_be_visible()
        another = page.get_by_role("button", name=self.label("Submit another"), exact=True)
        self.keyboard_action(page, another)
        page.keyboard.press("Enter")
        self.save(page, tag + "-reset")
        for control in self.fields(page):
            expect(control).to_have_value("")
        expect(page.locator("main textarea")).to_have_count(2)
        expect(page.locator('main [aria-invalid="true"]')).to_have_count(0)
        expect(page.get_by_role("radio", checked=True)).to_have_count(0)
        expect(
            page.get_by_role("button", name=self.label("Submit task"), exact=True)
        ).to_be_enabled()
        route = self.start_post(page, tag + "-again")
        self.http.fulfill(
            route, json={"id": entry.ROWS[0]["id"], "status": status, "reason": reason}
        )
        mine = page.get_by_role("button", name=self.label("My submissions"), exact=True)
        self.keyboard_action(page, mine)
        page.keyboard.press("Enter")
        expect(page).to_have_url(self.options.base_url + "/battles/tasks")
        self.save(page, tag + "-destination")
        self.assertEqual(self.http.unexpected + self.page_errors, [])
        self.doCleanups()

    def test_results_and_reset_navigation(self):
        for layout in entry.LAYOUTS:
            for status in ("quarantine", "rejected"):
                with self.subTest(layout=layout, status=status):
                    self.result_case(
                        layout, status, "title_empty" if status == "rejected" else None
                    )
        for layout in (("en", 1280), ("ru", 320)):
            with self.subTest(layout=layout, status="pending_validation"):
                self.result_case(layout, "pending_validation", None)
            for reason in ("synthetic_unknown_reason", None):
                with self.subTest(layout=layout, reason=reason):
                    self.result_case(layout, "rejected", reason)


if __name__ == "__main__":
    TaskSubmitTests.options, unittest_args = browser.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
