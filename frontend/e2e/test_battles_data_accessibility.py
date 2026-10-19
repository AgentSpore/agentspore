"""Measure populated battle cards and first-load recovery through local synthetic HTTP.

Use the existing explicit loopback/browser/screenshots CLI. Contrast is not certified.
"""

import json
import unittest
from urllib.parse import parse_qs, urlparse

import test_detail_width as detail
from playwright.sync_api import expect

A, B, C, CA, CB = (f"00000000-0000-4000-8000-{n:012d}" for n in (301, 302, 303, 401, 402))
IDS = [f"00000000-0000-4000-8000-{n:012d}" for n in range(201, 206)]
ROWS = json.loads(r"""
[ { "task_id": "00000000-0000-4000-8000-000000000501", "status": "running",
"agent_a_id": "00000000-0000-4000-8000-000000000301",
"agent_b_id": "00000000-0000-4000-8000-000000000302", "contender_a_id": null,
"contender_b_id": null, "winner": null, "is_demo": true, "challenged_at": "2026-10-19T09:00:00Z",
"started_at": "2026-10-19T09:01:00Z", "ended_at": null, "task_category_filter": "backend",
"task_difficulty_filter": "hard",
"task_title_snapshot": "Synthetic live task — длинное название задания для проверки карточки",
"task_content_withheld": false, "rated_eligible": false, "is_rated": null,
"rated_ineligibility_reason": "demo", "judging_stop_reason": null,
"id": "00000000-0000-4000-8000-000000000201" }, {
"task_id": "00000000-0000-4000-8000-000000000502", "status": "completed",
"agent_a_id": "00000000-0000-4000-8000-000000000301",
"agent_b_id": "00000000-0000-4000-8000-000000000303", "contender_a_id": null,
"contender_b_id": null, "winner": "b", "is_demo": false, "challenged_at": "2026-10-18T09:00:00Z",
"started_at": "2026-10-18T09:01:00Z", "ended_at": "2026-10-18T09:10:00Z",
"task_category_filter": "backend", "task_difficulty_filter": "hard",
"task_title_snapshot": "Synthetic rated task — длинное название завершённого задания",
"task_content_withheld": false, "rated_eligible": true, "is_rated": true,
"rated_ineligibility_reason": null, "judging_stop_reason": null,
"id": "00000000-0000-4000-8000-000000000202" }, { "task_id": null, "status": "challenge_pending",
"agent_a_id": "00000000-0000-4000-8000-000000000301", "agent_b_id": null, "contender_a_id": null,
"contender_b_id": null, "winner": null, "is_demo": false, "challenged_at": "2026-10-19T10:00:00Z",
"started_at": null, "ended_at": null, "task_category_filter": "backend",
"task_difficulty_filter": "hard", "task_title_snapshot": null, "task_content_withheld": true,
"rated_eligible": null, "is_rated": null, "rated_ineligibility_reason": null,
"judging_stop_reason": null, "id": "00000000-0000-4000-8000-000000000203" }, { "task_id": null,
"status": "aborted", "agent_a_id": null, "agent_b_id": null,
"contender_a_id": "00000000-0000-4000-8000-000000000401",
"contender_b_id": "00000000-0000-4000-8000-000000000402", "winner": null, "is_demo": false,
"challenged_at": "2026-10-17T09:00:00Z", "started_at": null, "ended_at": "2026-10-17T09:01:00Z",
"task_category_filter": "backend", "task_difficulty_filter": "hard", "task_title_snapshot": null,
"task_content_withheld": true, "rated_eligible": false, "is_rated": null,
"rated_ineligibility_reason": "auto", "judging_stop_reason": null,
"id": "00000000-0000-4000-8000-000000000204" }, {
"task_id": "00000000-0000-4000-8000-000000000502", "status": "completed",
"agent_a_id": "00000000-0000-4000-8000-000000000301",
"agent_b_id": "00000000-0000-4000-8000-000000000303", "contender_a_id": null,
"contender_b_id": null, "winner": null, "is_demo": false, "challenged_at": "2026-10-18T08:00:00Z",
"started_at": "2026-10-18T08:01:00Z", "ended_at": "2026-10-18T08:10:00Z",
"task_category_filter": "backend", "task_difficulty_filter": "hard",
"task_title_snapshot": "Synthetic rated task — длинное название завершённого задания",
"task_content_withheld": false, "rated_eligible": true, "is_rated": false,
"rated_ineligibility_reason": null, "judging_stop_reason": "owner_budget_exhausted",
"id": "00000000-0000-4000-8000-000000000205" } ]
""")
UNDECIDED = ROWS.pop()
NAMES = {
    A: "Synthetic Challenger — длинное имя агента для проверки переноса",
    B: "Synthetic Sparring Agent — демонстрационный соперник",
    C: "Synthetic Rated Opponent — длинное имя победившего агента",
}
PROFILE = json.loads(r"""
{ "agent_type": "external", "model_provider": "synthetic", "model_name": "synthetic-model",
"specialization": "programmer", "skills": [], "karma": 0, "projects_created": 0,
"code_commits": 0, "reviews_done": 0, "last_heartbeat": null, "is_active": true,
"created_at": "2026-10-01T00:00:00Z", "dna_risk": 5, "dna_speed": 5, "dna_verbosity": 5,
"dna_creativity": 5, "bio": null, "fork_count": 0, "is_hosted": false,
"available_for_battles": true }
""")
CONTENDERS = json.loads(r"""
[ { "id": "00000000-0000-4000-8000-000000000401",
"display_name": "Synthetic retired contender — длинное имя · Direct", "provider": "synthetic",
"model_id": "synthetic-retired", "approach_key": "direct", "enabled": false }, {
"id": "00000000-0000-4000-8000-000000000402",
"display_name": "Synthetic active contender — длинное имя · Step by step",
"provider": "synthetic", "model_id": "synthetic-active", "approach_key": "stepwise",
"enabled": true } ]
""")
SUPPORT = {
    f"/api/v1/agents/{key}": dict(PROFILE, id=key, name=name, handle="synthetic-" + key[-3:])
    for key, name in NAMES.items()
}
SUPPORT.update(
    {
        "/api/v1/battles/contenders": CONTENDERS,
        "/api/v1/battles/leaderboard": {"contenders": [], "approaches": []},
    }
)
LABELS = json.loads(r"""
{ "en": { "completed": "Completed", "include": "Include battles without a verdict",
"error": "Failed to refresh the arena", "details": "Technical details", "open": "open challenge",
"hidden": "· hidden", "demo": "Demo · unrated", "rated": "Rated · Elo updated",
"aborted": "Battle aborted" }, "ru": { "completed": "Завершены",
"include": "Показать бои без решения", "error": "Не удалось обновить арену",
"details": "Технические сведения", "open": "открытый вызов", "hidden": "· скрыта",
"demo": "Демо · без рейтинга", "rated": "Рейтинговый · Elo обновлён", "aborted": "Бой прерван" } }
""")
ACTION = """el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return {href: el.getAttribute('href'), text: el.innerText, x: r.x, right: r.right,
        top: r.top, bottom: r.bottom, width: r.width, height: r.height,
        viewportHeight: innerHeight, headerBottom: document.querySelector('header')
            ?.getBoundingClientRect().bottom || 0,
        hit: el === hit || el.contains(hit), focused: el === document.activeElement,
        focusVisible: el.matches(':focus-visible'), boxShadow: s.boxShadow};
}"""
TEXT = """root => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), records = [];
    while (walker.nextNode()) {
        const node = walker.currentNode, el = node.parentElement, text = node.textContent.trim();
        if (!text || el.closest('svg, [aria-hidden="true"]') || !el.checkVisibility()) continue;
        const closed = el.closest('details:not([open])');
        if (closed && !closed.querySelector('summary').contains(el)) continue;
        records.push({text, size: parseFloat(getComputedStyle(el).fontSize),
            kind: ['?', '⚙'].includes(text) ? 'exposed state marker' : 'substantive text'});
    }
    return records;
}"""


class BattlesDataTests(unittest.TestCase):
    """Exercise real list rendering and polling without inheriting prior browser cases."""

    @classmethod
    def setUpClass(cls):
        detail.DetailWidthTests.setUpClass.__func__(cls)

    def list_rows(self, query):
        """Validate the actual caller query and mirror public serializer filtering."""
        params = parse_qs(query, keep_blank_values=True, strict_parsing=True)
        if set(params) - {"limit", "offset", "status", "include_undecided"}:
            raise ValueError("Unknown list parameter")
        if any(len(values) != 1 for values in params.values()):
            raise ValueError("Repeated list parameter")
        values = {key: value[0] for key, value in params.items()}
        limit, offset = int(values.get("limit", "50")), int(values.get("offset", "0"))
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid list window")
        if values.get("include_undecided", "false") not in ("true", "false"):
            raise ValueError("Invalid include flag")
        if "status" in values and values["status"] not in {
            "challenge_pending",
            "accepted",
            "reserved",
            "queued",
            "running",
            "judging",
            "completed",
            "declined",
            "expired",
            "aborted",
        }:
            raise ValueError("Invalid battle status")
        self.lists.append(values)
        rows = ROWS + ([UNDECIDED] if values.get("include_undecided") == "true" else [])
        rows = [row for row in rows if "status" not in values or row["status"] == values["status"]]
        return sorted(rows, key=lambda row: (row["challenged_at"], row["id"]), reverse=True)[
            offset : offset + limit
        ]

    def route_request(self, route):
        """Permit local assets and exact synthetic GET contracts; reject everything else."""
        request, target = route.request, urlparse(route.request.url)
        origin = urlparse(self.options.base_url)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            self.unexpected.append("external request")
            route.abort()
        elif request.method != "GET":
            self.unexpected.append(f"{request.method} {target.path}")
            route.abort()
        elif not target.path.startswith("/api/"):
            route.continue_()
        elif target.path == "/api/v1/battles":
            try:
                rows = self.list_rows(target.query)
            except ValueError as error:
                self.unexpected.append(str(error))
                route.fulfill(status=503, json={"detail": "Invalid synthetic query"})
                return
            route.fulfill(
                status=503 if self.mode == "error" else 200,
                json={"detail": "Synthetic battles unavailable"} if self.mode == "error" else rows,
            )
        elif target.path in SUPPORT and not target.query:
            self.support_requested.append(target.path)
            route.fulfill(json=SUPPORT[target.path])
        else:
            self.unexpected.append(f"GET {target.path}?{target.query}")
            route.fulfill(status=503, json={"detail": "No synthetic fixture"})

    def open_page(self, locale, width, mode="populated"):
        """Create fresh anonymous state; install the existing browser clock before navigation."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.mode, self.unexpected, self.lists, self.support_requested = mode, [], [], []
        context.route("**/*", self.route_request)
        page = context.new_page()
        page.set_default_timeout(7000)
        self.clock = getattr(page, "clock", None) if mode == "error" else None
        if self.clock is not None:
            self.clock.install()
        page.goto(self.options.base_url + "/battles")
        return page

    def settle_names(self, page):
        """Wait for real public profile/roster lookup rather than loading ellipses."""
        expect(page.locator("main h1")).to_be_visible()
        for name in list(NAMES.values()) + [row["display_name"] for row in CONTENDERS]:
            expect(page.get_by_text(name, exact=True).first).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity).map(a => a.finished))"
        )

    def card(self, page, identifier):
        """Select the independent card action by its actual destination."""
        return page.locator(f'main a[href="/battles/{identifier}"]')

    def tab_to(self, page, control):
        """Use native Tab and retain actual post-focus viewport/hit/ring geometry."""
        for _ in range(80):
            page.keyboard.press("Tab")
            if control.evaluate("el => el === document.activeElement"):
                break
        return control.evaluate(ACTION)

    def save(self, page, tag, records):
        """Retain evidence even if a later size/text assertion is RED."""
        records.update(
            page=page.evaluate(
                "() => ({width: innerWidth, documentWidth: document.documentElement.scrollWidth})"
            ),
            lists=list(self.lists),
            support=list(self.support_requested),
            unexpected=list(self.unexpected),
            contrast={"status": "unknown", "reason": "Paint/terminal opacity not measured"},
        )
        (self.options.screenshots / f"{tag}.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2)
        )
        page.screenshot(path=str(self.options.screenshots / f"{tag}.png"), full_page=True)

    def assert_action(self, record, width):
        """Require the complete independent target, not neighboring wrapper padding."""
        self.assertGreaterEqual(record["width"], 44, record)
        self.assertGreaterEqual(record["height"], 44, record)
        self.assertGreaterEqual(record["x"], -1, record)
        self.assertLessEqual(record["right"], width + 1, record)
        if record["height"] <= record["viewportHeight"] - max(0, record["headerBottom"]):
            self.assertGreaterEqual(record["top"], max(0, record["headerBottom"]) - 1, record)
            self.assertLessEqual(record["bottom"], record["viewportHeight"] + 1, record)
        self.assertTrue(record["hit"], record)

    def assert_data(self, page, locale):
        """Pin privacy/demo/rating/open/retired semantics using the actual cards."""
        labels = LABELS[locale]
        hrefs = page.locator('main a[href^="/battles/00000000-"]').evaluate_all(
            "links => links.map(el => el.getAttribute('href').split('/').pop())"
        )
        self.assertEqual(hrefs, [IDS[0], IDS[2], IDS[1], IDS[3]])
        expect(self.card(page, IDS[0])).to_contain_text(labels["demo"])
        expect(self.card(page, IDS[1])).to_contain_text(labels["rated"])
        expect(self.card(page, IDS[1])).to_contain_text(NAMES[C])
        expect(self.card(page, IDS[2])).to_contain_text(labels["open"])
        for identifier in (IDS[2], IDS[3]):
            expect(self.card(page, identifier)).to_contain_text(labels["hidden"])
            expect(self.card(page, identifier)).to_contain_text("backend")
            expect(self.card(page, identifier)).not_to_contain_text("Synthetic live task")
        expect(self.card(page, IDS[3])).to_contain_text(labels["aborted"])

    def audit_layout(self, locale, width):
        page = self.open_page(locale, width)
        records = {"actions": [], "keyboard": []}
        try:
            self.settle_names(page)
            controls = [self.card(page, identifier) for identifier in IDS[:4]]
            for control in controls:
                control.scroll_into_view_if_needed()
                records["actions"].append(control.evaluate(ACTION))
            records["text"] = page.locator("main").evaluate(
                "main => [...main.querySelectorAll('a[href]')].filter(el => "
                "el.getAttribute('href').match(/^\\/battles\\/[0-9a-f-]{36}$/))"
                ".flatMap(" + TEXT + ")"
            )
            records["keyboard"] = [self.tab_to(page, control) for control in controls]
            self.save(page, f"cards-{locale}-{width}", records)
            self.assert_data(page, locale)
            for record in records["actions"] + records["keyboard"]:
                with self.subTest(action=record["href"]):
                    self.assert_action(record, width)
            for record in records["keyboard"]:
                with self.subTest(focus=record["href"]):
                    self.assertTrue(record["focused"] and record["focusVisible"], record)
                    self.assertNotEqual(record["boxShadow"], "none", record)
            self.assertTrue(records["text"])
            self.assertEqual([r for r in records["text"] if r["size"] < 12], [])
            self.assertLessEqual(records["page"]["documentWidth"], width)
            self.assertEqual(self.unexpected, [])
        finally:
            self.save(page, f"cards-{locale}-{width}", records)
            page.context.close()

    def test_populated_cards_geometry_text_and_keyboard_in_six_layouts(self):
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    self.audit_layout(locale, width)

    def test_include_and_completed_filter_preserve_public_rows(self):
        for locale in ("en", "ru"):
            with self.subTest(locale=locale):
                page = self.open_page(locale, 320)
                records = {"counts": []}
                try:
                    self.settle_names(page)
                    cards = page.locator('main a[href^="/battles/00000000-"]')
                    expect(cards).to_have_count(4)
                    toggle = page.get_by_role("button", name=LABELS[locale]["include"], exact=True)
                    toggle.click()
                    expect(toggle).to_have_attribute("aria-pressed", "true")
                    expect(cards).to_have_count(5)
                    records["counts"].extend([4, 5])
                    expect(self.card(page, IDS[3])).to_be_visible()
                    completed = page.get_by_role(
                        "button", name=LABELS[locale]["completed"], exact=True
                    )
                    completed.click()
                    expect(completed).to_have_attribute("aria-pressed", "true")
                    expect(cards).to_have_count(2)
                    records["counts"].append(2)
                    self.assertEqual(self.lists[-1]["include_undecided"], "true")
                    toggle.click()
                    expect(toggle).to_have_attribute("aria-pressed", "false")
                    expect(cards).to_have_count(1)
                    records["counts"].append(1)
                    self.assertEqual(self.lists[-1]["status"], "completed")
                    self.assertNotIn("include_undecided", self.lists[-1])
                    self.assertTrue(
                        all(q["limit"] == "25" and q["offset"] == "0" for q in self.lists)
                    )
                    self.assertEqual(self.unexpected, [])
                finally:
                    self.save(page, f"filters-{locale}", records)
                    page.context.close()

    def test_first_error_details_and_automatic_poll_recovery(self):
        for locale in ("en", "ru"):
            with self.subTest(locale=locale):
                page = self.open_page(locale, 320, "error")
                records = {}
                try:
                    expect(page.get_by_text(LABELS[locale]["error"], exact=True)).to_be_visible()
                    expect(page.locator('main a[href^="/battles/00000000-"]')).to_have_count(0)
                    summary = page.get_by_text(LABELS[locale]["details"], exact=True)
                    expect(summary.locator("..")).not_to_have_attribute("open", "")
                    records["summary"] = self.tab_to(page, summary)
                    self.assertTrue(records["summary"]["focused"])
                    page.keyboard.press("Enter")
                    expect(summary.locator("..")).to_have_attribute("open", "")
                    expect(page.get_by_text("HTTP 503", exact=True)).to_be_visible()
                    records["text"] = summary.locator("../..").evaluate(TEXT)
                    self.save(page, f"error-open-{locale}", records)
                    self.mode = "populated"
                    self.assertEqual(page.evaluate("document.visibilityState"), "visible")
                    if self.clock is not None:
                        self.clock.fast_forward(15001)
                    expect(self.card(page, IDS[0])).to_be_visible(timeout=17000)
                    self.settle_names(page)
                    expect(page.locator('main a[href^="/battles/00000000-"]')).to_have_count(4)
                    expect(page.get_by_text(LABELS[locale]["error"], exact=True)).to_have_count(0)
                    self.assertGreaterEqual(len(self.lists), 2)
                    self.assertTrue(
                        all("status" not in q and "include_undecided" not in q for q in self.lists)
                    )
                    self.assert_action(records["summary"], 320)
                    self.assertEqual([r for r in records["text"] if r["size"] < 12], [])
                    self.assertEqual(self.unexpected, [])
                finally:
                    self.save(page, f"error-recovered-{locale}", records)
                    page.context.close()


if __name__ == "__main__":
    BattlesDataTests.options, unittest_args = detail.browser_options.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
