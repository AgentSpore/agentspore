"""Keep failed demo-agent loads distinct from successful empty responses."""

import json
import re
import unittest
from pathlib import Path

import test_battle_entry_states as entry
from playwright.sync_api import expect

SOURCE = (Path(__file__).resolve().parents[1] / "src/lib/i18n/shared.ts").read_text()
SHARED = json.loads(
    SOURCE.split("export const sharedMessages = ", 1)[1].split(" as const;", 1)[0]
)
EMPTY = (
    "You have no connected agents of your own. Demo battles are only available "
    'to non-hosted (self-run) agents — set one up in the "My agents" section.'
)


class DemoAgentLoadTests(unittest.TestCase):
    """Compose existing entry fixtures without inheriting their test cases."""

    @classmethod
    def setUpClass(cls):
        entry.browser.BattleAccessibilityTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        entry.browser.BattleAccessibilityTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.entry = entry.BattleEntryTests(methodName="runTest")
        self.entry.browser, self.entry.options = self.browser, self.options
        self.addCleanup(self.entry.doCleanups)

    def controls(self, page):
        fixture = self.entry
        labels = SHARED[fixture.locale]
        return (
            page.get_by_role("button", name=labels["retry"], exact=True),
            page.get_by_role(
                "button", name=fixture.label("Start demo battle"), exact=True
            ),
        )

    def fail_read(self, page, failure, index=0):
        fixture = self.entry
        page.wait_for_function(
            "async n => (await window.__syntheticEntryCounts())[0] === n", arg=index + 1
        )
        route = fixture.http.reads[index]
        if failure == "network":
            fixture.http.abort(route)
        elif failure == "json":
            fixture.http.fulfill(
                route, status=200, content_type="application/json", body="{"
            )
        else:
            fixture.http.fulfill(
                route, status=503, json={"detail": "Synthetic unavailable"}
            )

    def assert_error(self, page):
        fixture = self.entry
        retry, submit = self.controls(page)
        expect(page.locator("main [role=alert]")).to_have_text(
            re.compile(re.escape(SHARED[fixture.locale]["couldNotLoadData"]))
        )
        expect(retry).to_be_enabled()
        expect(submit).to_be_disabled()
        expect(page.get_by_text(fixture.label(EMPTY), exact=True)).to_have_count(0)
        expect(page.locator("main select")).to_have_count(0)
        self.assertEqual(fixture.http.writes, [])

    def start_retry(self, page):
        fixture = self.entry
        retry, submit = self.controls(page)
        count = len(fixture.http.reads)
        retry.focus()
        with page.expect_request(
            lambda r: r.url.endswith("/api/v1/users/me/external-agents")
        ):
            page.keyboard.press("Enter")
        expect(
            page.get_by_text(fixture.label("Loading your agents…"), exact=True)
        ).to_be_visible()
        expect(page.locator("main [role=alert]")).to_have_count(0)
        expect(retry).to_have_count(0)
        expect(submit).to_be_disabled()
        page.keyboard.press("Enter")
        submit.evaluate("el => el.click()")
        page.evaluate("() => new Promise(requestAnimationFrame)")
        self.assertEqual(len(fixture.http.reads), count + 1)
        self.assertEqual(fixture.http.writes, [])
        return fixture.http.reads[-1]

    def recovery_case(self, failure, outcome, layout):
        fixture = self.entry
        page = fixture.open_entry("/battles/demo", layout)
        tag = f"agent-load-{failure}-{outcome}"
        try:
            self.fail_read(page, failure)
            self.assert_error(page)
            fixture.audit(page, tag + "-error")
            route = self.start_retry(page)
            fixture.audit(page, tag + "-pending")
            payload = fixture.agents() if outcome == "populated" else []
            fixture.http.fulfill(route, status=200, json=payload)
            retry, submit = self.controls(page)
            expect(retry).to_have_count(0)
            expect(page.locator("main [role=alert]")).to_have_count(0)
            if outcome == "empty":
                expect(
                    page.get_by_text(fixture.label(EMPTY), exact=True)
                ).to_be_visible()
                expect(submit).to_be_disabled()
            else:
                fixture.select_demo(page, submit, "selected")
                expect(submit).to_be_enabled()
            fixture.audit(page, tag + "-recovered")
        finally:
            fixture.save(page, tag + "-final")
            fixture.http.close(page.context)

    def test_errors_recover_to_confirmed_results(self):
        for layout in entry.LAYOUTS:
            for failure in ("http", "network", "json"):
                for outcome in ("empty", "populated"):
                    with self.subTest(layout=layout, failure=failure, outcome=outcome):
                        self.recovery_case(failure, outcome, layout)

    def test_retry_can_fail_again_then_recover(self):
        for layout in entry.LAYOUTS:
            with self.subTest(layout=layout):
                fixture = self.entry
                page = fixture.open_entry("/battles/demo", layout)
                try:
                    self.fail_read(page, "http")
                    self.assert_error(page)
                    self.start_retry(page)
                    self.fail_read(page, "network", 1)
                    self.assert_error(page)
                    fixture.audit(page, "agent-load-repeated-error")
                    route = self.start_retry(page)
                    fixture.http.fulfill(route, status=200, json=fixture.agents())
                    expect(page.locator("main select")).to_be_visible()
                    self.assertEqual(len(fixture.http.reads), 3)
                    fixture.audit(page, "agent-load-repeated-recovered")
                finally:
                    fixture.save(page, "agent-load-repeated-final")
                    fixture.http.close(page.context)

    def test_pending_retry_is_cancelled_when_leaving(self):
        fixture = self.entry
        page = fixture.open_entry("/battles/demo", ("en", 390))
        try:
            self.fail_read(page, "http")
            self.assert_error(page)
            route = self.start_retry(page)
            link = page.get_by_role(
                "link", name=fixture.label("Create a regular challenge")
            )
            with page.expect_event(
                "requestfailed", predicate=lambda r: r == route.request
            ):
                link.click()
                expect(page).to_have_url(self.options.base_url + "/battles/new")
            fixture.http.fulfill(route, status=200, json=[])
            page.wait_for_function(
                "async () => (await window.__syntheticEntryCounts())[0] === 3"
            )
            page.go_back()
            page.wait_for_function(
                "async () => (await window.__syntheticEntryCounts())[0] === 4"
            )
            fixture.http.fulfill(
                fixture.http.reads[-1], status=200, json=fixture.agents()
            )
            expect(page.locator("main select")).to_be_visible()
            expect(page.get_by_text(fixture.label(EMPTY), exact=True)).to_have_count(0)
            fixture.audit(page, "agent-load-remounted")
        finally:
            fixture.save(page, "agent-load-unmount-final")
            fixture.http.close(page.context)


if __name__ == "__main__":
    DemoAgentLoadTests.options, unittest_args = entry.browser.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
