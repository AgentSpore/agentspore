"""Exercise only the missing battle pagination window through synthetic HTTP."""

import json
import re
import unittest
from datetime import UTC, datetime
from urllib.parse import urlparse

import auth_form_fixtures as auth
import test_battles_accessibility as browser
import test_battles_data_accessibility as data
from playwright.sync_api import expect

ROWS = [dict(data.ROWS[1], id=f"00000000-0000-4000-8000-{index:012d}") for index in range(801, 951)]
MORE = re.compile(r"^(Show more battles|Показать ещё бои|Loading…|Загрузка…)$")
CARDS = 'main a[href^="/battles/00000000-"]'


class PaginationHTTP(auth.AuthHTTP):
    """Hold exact list reads; permit only existing public support and known pages."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.lists = []

    def accepts_page(self, request, target):
        known = {"/battles"} | {"/battles/" + row["id"] for row in ROWS}
        known |= {"/battles/demo", "/battles/new", "/battles/tasks/new", "/docs/glossary"}
        if target.path in known:
            query = data.parse_qs(target.query, keep_blank_values=True)
            return request.method == "GET" and (
                not query
                or (
                    set(query) == {"_rsc"}
                    and len(query["_rsc"]) == 1
                    and bool(query["_rsc"][0])
                    and request.headers.get("rsc") == "1"
                )
            )
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        local_get = (
            target.scheme == "http" and target.netloc == self.origin and request.method == "GET"
        )
        if local_get and target.path == "/api/v1/battles":
            try:
                data.BattlesDataTests.list_rows(self, target.query)
                if set(self.lists[-1]) != {"limit", "offset"}:
                    raise ValueError("Unexpected pagination filter")
            except ValueError:
                self.reject(route)
                return
            self.pending.append(route)
        elif local_get and not target.query and target.path in data.SUPPORT:
            route.fulfill(json=data.SUPPORT[target.path])
        else:
            super().route(route)

    def reply(self, count, failure=None):
        route = self.pending.pop(0)
        if failure == "network":
            route.abort("failed")
        elif failure == "http":
            route.fulfill(status=503, json={"detail": "Synthetic pagination unavailable"})
        else:
            query = data.parse_qs(urlparse(route.request.url).query)
            offset, limit = int(query["offset"][0]), int(query["limit"][0])
            rows = ROWS[:count][offset : offset + limit]
            self.marker = f"Synthetic pagination response {len(self.lists)}"
            rows[-1] = dict(rows[-1], task_title_snapshot=self.marker)
            route.fulfill(json=rows)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class BattlePaginationTests(unittest.TestCase):
    """Check BP.1–5 without repeating accepted card geometry or filter matrices."""

    @classmethod
    def setUpClass(cls):
        data.detail.DetailWidthTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        data.detail.DetailWidthTests.tearDownClass.__func__(cls)

    def open_list(self, locale, width):
        self.locale, self.width, self.records = locale, width, {}
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, service_workers="block"
        )
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.http = PaginationHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(7000)
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.expose_function("__paginationPending", lambda: len(self.http.pending))
        page.clock.install(time=datetime(2026, 10, 20, tzinfo=UTC))
        page.clock.pause_at(datetime(2026, 10, 20, 0, 0, 1, tzinfo=UTC))
        page.goto(self.options.base_url + "/battles")
        self.wait_request(page, 25)
        self.http.reply(150)
        expect(page.locator(CARDS)).to_have_count(25)
        return page

    def more(self, page):
        return page.locator("main").get_by_role("button", name=MORE)

    def wait_request(self, page, limit, offset=0):
        page.wait_for_function("async () => await window.__paginationPending() > 0")
        self.assertEqual(self.http.lists[-1], {"limit": str(limit), "offset": str(offset)})

    def save(self, page, tag):
        button = self.more(page)
        self.records.update(
            requests=list(self.http.lists),
            unexpected=self.http.unexpected,
            page_errors=self.errors,
            rows=page.locator(CARDS).count(),
            button=button.evaluate(data.ACTION) if button.count() else None,
            pagination_text=button.locator("..").evaluate(data.TEXT) if button.count() else [],
            document_width=page.evaluate("document.documentElement.scrollWidth"),
            contrast="Not measured; RR.4",
        )
        target = self.options.screenshots / f"pagination-{tag}-{self.locale}-{self.width}"
        target.with_suffix(".json").write_text(
            json.dumps(self.records, ensure_ascii=False, indent=2)
        )
        page.screenshot(path=str(target.with_suffix(".png")), animations="disabled")
        self.assertEqual(self.http.unexpected + self.errors, [])
        self.assertLessEqual(self.records["document_width"], self.width)
        self.assertEqual([r for r in self.records["pagination_text"] if r["size"] < 12], [])

    def assert_rows(self, page, count):
        hrefs = page.locator(CARDS).evaluate_all("els => els.map(el => el.getAttribute('href'))")
        self.assertEqual(set(hrefs), {"/battles/" + row["id"] for row in ROWS[:count]})
        self.assertEqual(len(hrefs), count)
        self.assertTrue(all(card.is_visible() for card in page.locator(CARDS).all()))

    def activate_more(self, page):
        button = self.more(page)
        for _ in range(page.locator("a[href], button, input, select, summary").count() + 5):
            page.keyboard.press("Tab")
            if button.evaluate("el => el === document.activeElement"):
                break
        self.records["native_focus"] = button.evaluate(data.ACTION)
        self.save(page, "before-activation-" + str(len(self.http.lists)))
        record = self.records["native_focus"]
        data.BattlesDataTests.assert_action(self, record, self.width)
        self.assertTrue(record["focused"] and record["focusVisible"], record)
        self.assertNotEqual(record["boxShadow"], "none", record)
        page.keyboard.press("Enter")

    def pending(self, page, previous):
        self.save(page, "pending-" + str(previous))
        with self.subTest(pending=previous, locale=self.locale):
            self.assert_rows(page, previous)
            expect(self.more(page)).to_be_disabled()
            expect(self.more(page)).to_have_attribute("aria-busy", "true")
            self.assertEqual(
                self.more(page).inner_text().strip(),
                "Loading…" if self.locale == "en" else "Загрузка…",
            )
            page.keyboard.press("Enter")
            self.assertEqual(len(self.http.pending), 1)

    def poll(self, page, limit):
        page.clock.fast_forward(15001)
        self.wait_request(page, limit)

    def test_pending_failure_recovery_and_exhaustion(self):
        for locale, width, failure in (("en", 1280, "http"), ("ru", 320, "network")):
            with self.subTest(locale=locale, failure=failure):
                page = self.open_list(locale, width)
                self.activate_more(page)
                self.wait_request(page, 50)
                self.pending(page, 25)
                self.http.reply(150, failure)
                error = page.get_by_text(data.LABELS[locale]["error"], exact=True)
                expect(error).to_be_visible()
                self.records["error_text"] = error.locator("..").evaluate(data.TEXT)
                self.save(page, failure + "-error")
                with self.subTest(error_feedback_visible=locale):
                    expect(error).to_be_in_viewport()
                with self.subTest(preserved_after_error=locale):
                    self.assert_rows(page, 25)
                    expect(self.more(page)).to_be_enabled()
                    expect(self.more(page)).to_have_attribute("aria-busy", "false")
                    self.assertEqual([r for r in self.records["error_text"] if r["size"] < 12], [])
                self.poll(page, 50)
                self.http.reply(150)
                expect(page.locator(CARDS)).to_have_count(50)
                self.save(page, "recovered-50")
                self.assert_rows(page, 50)
                self.assertEqual(
                    self.more(page).locator("..").locator("[aria-live]").inner_text().strip(),
                    ("Showing" if locale == "en" else "Показано") + " 50",
                )
                self.poll(page, 50)
                self.http.reply(150)
                expect(page.get_by_text(self.http.marker, exact=True)).to_be_visible()
                expect(error).to_have_count(0)
                self.activate_more(page)
                self.wait_request(page, 75)
                self.http.reply(62)
                expect(page.locator(CARDS)).to_have_count(62)
                self.save(page, "short-62")
                self.assert_rows(page, 62)
                expect(self.more(page)).to_have_count(0)
                self.poll(page, 75)
                self.http.reply(62)
                expect(page.get_by_text(self.http.marker, exact=True)).to_be_visible()
                self.save(page, "exhausted-poll")
                self.assert_rows(page, 62)
                page.context.close()

    def test_window125_uses_offset100(self):
        page = self.open_list("en", 1280)
        for count in (50, 75, 100, 125):
            self.activate_more(page)
            self.wait_request(page, min(count, 100))
            self.http.reply(150)
            if count == 125:
                self.wait_request(page, 25, 100)
                self.http.reply(150)
            expect(page.locator(CARDS)).to_have_count(count)
            self.save(page, "window-" + str(count))
            self.assert_rows(page, count)
        self.poll(page, 100)
        self.http.reply(150)
        self.wait_request(page, 25, 100)
        self.http.reply(150)
        expect(page.get_by_text(self.http.marker, exact=True)).to_be_visible()
        self.save(page, "window125-poll")
        self.assert_rows(page, 125)


if __name__ == "__main__":
    BattlePaginationTests.options, unittest_args = browser.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
