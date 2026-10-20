"""Exercise existing challenge submission through strict synthetic HTTP boundaries."""

import json
import unittest
from urllib.parse import parse_qs, urlparse

import test_battle_entry_states as entry
import test_battles_accessibility as browser
from auth_form_fixtures import AuthHTTP
from playwright.sync_api import expect
from test_battles_data_accessibility import ACTION, TEXT

A, B, BATTLE = (f"00000000-0000-4000-8000-{n:012d}" for n in (711, 813, 814))
OPPONENT = "SyntheticOpponent" + "LongName" * 12
REPRESENTATIVE = [("en", 1280), ("ru", 320)]


class ChallengeHTTP(entry.EntryHTTP):
    """Hold challenge writes and destination reads; reject unknown API contracts."""

    def accepts_page(self, request, target):
        query = parse_qs(target.query, keep_blank_values=True)
        if target.path in {"/battles/new", f"/battles/{BATTLE}"}:
            allowed = {"_rsc"}
        elif target.path == "/login" and query.get("next") == ["/battles/new"]:
            allowed = {"next", "_rsc"}
        else:
            return AuthHTTP.accepts_page(self, request, target)
        return (
            request.method == "GET"
            and set(query) <= allowed
            and all(len(values) == 1 and bool(values[0]) for values in query.values())
            and ("_rsc" not in query or request.headers.get("rsc") == "1")
        )

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        agent = entry.BattleEntryTests.agents(self)[0]
        gets = {
            "/api/v1/users/me/external-agents": [agent],
            "/api/v1/battles/contenders": [],
            "/api/v1/battles/tasks": json.loads(
                '{"minimum_pool_size":20,"cooldown_days":30,"pools":'
                '[{"category":"backend","difficulty":"hard","fresh_count":24,"challenge_available":true}]}'
            ),
            f"/api/v1/agents/{A}": {**agent, "available_for_battles": True},
            "/api/v1/agents/leaderboard?limit=100": [
                {**agent, "id": B, "name": OPPONENT, "handle": "synthetic-opponent"}
            ],
        }
        exact = target.path + ("?" + target.query if target.query else "")
        if request.method == "GET" and exact in gets:
            self.reads.append(route)
            return route.fulfill(json=gets[exact])
        if request.method == "GET" and exact == f"/api/v1/battles/{BATTLE}":
            self.detail.append(route)
            self.pending.append(route)
        elif request.method == "POST" and exact == "/api/v1/battles":
            self.writes.append(route)
            self.pending.append(route)
        else:
            AuthHTTP.route(self, route)


class ChallengeSubmitTests(unittest.TestCase):
    """Measure native keyboard interaction, submission results and visible geometry."""

    setUpClass = classmethod(browser.BattleAccessibilityTests.setUpClass.__func__)
    tearDownClass = classmethod(browser.BattleAccessibilityTests.tearDownClass.__func__)
    label = entry.BattleEntryTests.label
    keyboard_action = entry.BattleEntryTests.keyboard_action

    def open_challenge(self, layout, anonymous=False):
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
        self.http, self.records, self.page_errors = (
            ChallengeHTTP(self.options.base_url),
            {},
            [],
        )
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.expose_function("__challengeWrites", lambda: len(self.http.writes))
        page.set_default_timeout(7000)
        page.goto(self.options.base_url + "/battles/new")
        page.evaluate("document.fonts.ready")
        page.locator("main").evaluate("el => el.getBoundingClientRect()")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page

    def save(self, page, tag):
        page.evaluate("document.fonts.ready")
        self.records.update(
            text=page.locator("main").evaluate(TEXT),
            document_width=page.evaluate("document.documentElement.scrollWidth"),
            unexpected=self.http.unexpected,
            page_errors=self.page_errors,
            writes=[r.request.post_data_json for r in self.http.writes],
        )
        self.capture_index = getattr(self, "capture_index", 0) + 1
        prefix = f"{self._testMethodName}-{self.capture_index:03d}"
        target = self.options.screenshots / f"challenge-{prefix}-{tag}-{self.locale}-{self.width}"
        target.with_suffix(".json").write_text(
            json.dumps(self.records, ensure_ascii=False, indent=2)
        )
        page.screenshot(path=str(target.with_suffix(".png")), full_page=False)

    def check_geometry(self):
        self.assertLessEqual(self.records["document_width"], self.width)
        self.assertEqual(self.http.unexpected + self.page_errors, [])
        for text in self.records["text"]:
            self.assertGreaterEqual(text["size"], 12, text)
        for record in self.records.get("actions", []):
            with self.subTest(control=record["text"]):
                self.assertGreaterEqual(record["width"], 44)
                self.assertGreaterEqual(record["height"], 44)
                self.assertGreaterEqual(record["x"], 0)
                self.assertLessEqual(record["right"], self.width)
                self.assertGreaterEqual(record["top"], max(0, record["headerBottom"]))
                self.assertLessEqual(record["bottom"], record["viewportHeight"])
                self.assertTrue(record["focused"] and record["focusVisible"] and record["hit"])
                self.assertTrue(record["outline"] or record["boxShadow"] != "none")

    def focus_control(self, page, control, tag):
        control.wait_for(state="visible")
        record = self.keyboard_action(page, control)
        self.records.setdefault("actions", []).append(record)
        self.save(page, tag)
        expect(control).to_be_focused()

    def select_challenge(self, page, targeted):
        select = page.get_by_label(self.label("Your agent"), exact=True)
        expect(select.locator(f'option[value="{A}"]')).to_have_count(1)
        self.focus_control(page, select, "agent-focus")
        select.select_option(A)
        expect(select).to_have_value(A)
        if targeted:
            search = page.get_by_placeholder(self.label("Search agents by name…"))
            self.focus_control(page, search, "search-focus")
            page.keyboard.type("SyntheticOpponent")
            result = page.get_by_role("button", name=OPPONENT + " @synthetic-opponent")
            self.focus_control(page, result, "result-focus")
            page.keyboard.press("Enter")
            remove = page.get_by_role("button", name=self.label("Remove"), exact=True)
            self.focus_control(page, remove, "remove-focus")
            page.keyboard.press("Escape")
            expect(remove).to_be_visible()
        name = (
            self.label("Challenge {name}").replace("{name}", OPPONENT)
            if targeted
            else self.label("Send an open challenge")
        )
        return page.get_by_role("button", name=name, exact=True)

    def start_submit(self, page, targeted):
        submit = self.select_challenge(page, targeted)
        self.focus_control(page, submit, "submit-focus")
        page.keyboard.press("Enter")
        page.wait_for_function("async () => await window.__challengeWrites() === 1")
        pending = page.get_by_role("button", name=self.label("Creating challenge…"), exact=True)
        self.save(page, "pending")
        expect(pending).to_be_disabled()
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")
        page.evaluate("() => new Promise(requestAnimationFrame)")
        self.assertEqual(len(self.http.writes), 1)
        payload = {"task_category": None, "task_difficulty": None, "agent_a_id": A}
        if targeted:
            payload["agent_b_id"] = B
        self.assertEqual(self.http.writes[0].request.post_data_json, payload)
        return submit

    def error_result(self, page, outcome, submit):
        route = self.http.writes[0]
        messages = {
            "409": self.label(
                "Not enough fresh tasks in this category — pick a different combination."
            ),
            "detail": "Synthetic admission denied",
            "fallback": "HTTP 500",
            "network": "Failed to fetch",
        }
        if outcome == "network":
            self.http.abort(route)
        else:
            self.http.fulfill(
                route,
                status=409 if outcome == "409" else 500,
                json={"detail": messages[outcome]} if outcome == "detail" else {},
            )
        alert = page.locator("main [role=alert]")
        expect(alert).to_be_visible()
        self.records["error_header"] = alert.locator("div").first.evaluate(ACTION)
        self.save(page, "error-" + outcome)
        header = self.records["error_header"]
        self.assertGreaterEqual(header["top"], max(0, header["headerBottom"]))
        self.assertLessEqual(header["bottom"], header["viewportHeight"])
        expect(alert).to_contain_text(self.label("Failed to create the challenge"))
        expect(alert).to_contain_text(messages[outcome])
        expect(submit).to_be_enabled()
        expect(page.get_by_label(self.label("Your agent"), exact=True)).to_have_value(A)
        expect(page.get_by_role("button", name=self.label("Remove"), exact=True)).to_be_visible()
        self.focus_control(page, submit, "retry-" + outcome)
        page.keyboard.press("Enter")
        page.wait_for_function("async () => await window.__challengeWrites() === 2")
        self.save(page, "retry-pending-" + outcome)
        self.assertEqual(
            self.http.writes[1].request.post_data_json,
            self.http.writes[0].request.post_data_json,
        )

    def test_open_and_targeted_submit_navigate_to_exact_battle(self):
        for layout in REPRESENTATIVE:
            for targeted in (False, True):
                with self.subTest(layout=layout, targeted=targeted):
                    page = self.open_challenge(layout)
                    try:
                        self.start_submit(page, targeted)
                        self.http.fulfill(self.http.writes[0], status=201, json={"id": BATTLE})
                        expect(page).to_have_url(self.options.base_url + f"/battles/{BATTLE}")
                        self.records["destination"] = f"/battles/{BATTLE}"
                        self.save(page, "destination-" + str(targeted))
                        self.assertEqual(self.http.unexpected + self.page_errors, [])
                    finally:
                        self.save(page, "success-final-" + str(targeted))
                        self.http.close(page.context)

    def test_api_errors_preserve_targeted_selection_and_allow_retry(self):
        for layout in REPRESENTATIVE:
            for outcome in ("409", "detail", "fallback", "network"):
                with self.subTest(layout=layout, outcome=outcome):
                    page = self.open_challenge(layout)
                    try:
                        submit = self.start_submit(page, True)
                        self.error_result(page, outcome, submit)
                        self.check_geometry()
                    finally:
                        self.save(page, "error-final-" + outcome)
                        self.http.close(page.context)

    def test_long_targeted_actions_fit_and_keep_keyboard_focus(self):
        for layout in entry.LAYOUTS:
            with self.subTest(layout=layout):
                page = self.open_challenge(layout)
                try:
                    submit = self.select_challenge(page, True)
                    self.focus_control(page, submit, "long-targeted")
                    self.records["preview_columns"] = page.get_by_text(
                        self.label("VS"), exact=True
                    ).evaluate("""el => {
                        const range = document.createRange(); range.selectNodeContents(el);
                        const text = range.getBoundingClientRect();
                        const left = el.previousElementSibling.getBoundingClientRect();
                        const right = el.nextElementSibling.getBoundingClientRect();
                        return {leftRight: left.right, separatorLeft: text.left,
                            separatorRight: text.right, rightLeft: right.left};
                    }""")
                    self.save(page, "preview-columns")
                    columns = self.records["preview_columns"]
                    self.assertLessEqual(columns["leftRight"], columns["separatorLeft"])
                    self.assertLessEqual(columns["separatorRight"], columns["rightLeft"])
                    self.check_geometry()
                finally:
                    self.save(page, "long-final")
                    self.http.close(page.context)

    def test_missing_agent_validation_never_posts(self):
        for layout in REPRESENTATIVE:
            with self.subTest(layout=layout):
                page = self.open_challenge(layout)
                try:
                    submit = page.get_by_role(
                        "button", name=self.label("Send an open challenge"), exact=True
                    )
                    expect(submit).to_be_enabled()
                    self.focus_control(page, submit, "validation-focus")
                    page.keyboard.press("Enter")
                    select = page.get_by_label(self.label("Your agent"), exact=True)
                    self.save(page, "validation")
                    expect(select).to_have_attribute("aria-invalid", "true")
                    error_id = select.get_attribute("aria-describedby")
                    expect(page.locator(f'[id="{error_id}"]')).to_have_text(
                        self.label("Select your agent")
                    )
                    self.assertEqual(self.http.writes, [])
                    self.check_geometry()
                finally:
                    self.save(page, "validation-final")
                    self.http.close(page.context)

    def test_anonymous_invitation_is_keyboard_accessible_without_submit(self):
        for layout in REPRESENTATIVE:
            with self.subTest(layout=layout):
                page = self.open_challenge(layout, anonymous=True)
                try:
                    link = page.locator('main a[href="/login?next=/battles/new"]')
                    expect(link).to_be_visible()
                    self.focus_control(page, link, "anonymous")
                    expect(page.locator("main button")).to_have_count(0)
                    self.assertEqual(self.http.writes + self.http.reads, [])
                    self.check_geometry()
                finally:
                    self.save(page, "anonymous-final")
                    self.http.close(page.context)


if __name__ == "__main__":
    ChallengeSubmitTests.options, unittest_args = browser.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
