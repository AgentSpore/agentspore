"""Measure public project cards and preserve voting through synthetic HTTP.

python test_project_cards.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
Fixtures follow the project-list and public vote serializers; no live account is used.
"""

import json
import unittest
from urllib.parse import parse_qs, urlparse

import test_detail_width as detail
from playwright.sync_api import expect

PROJECTS = [
    {
        "id": "00000000-0000-4000-8000-000000000101",
        "title": (
            "Synthetic live project — длинное название проекта для проверки доступности карточки"
        ),
        "description": (
            "Synthetic live description. Проверка длинного описания на русском и"
            " английском. Project details stay available through the title link;"
            " this paragraph is intentionally a two-line preview. https://exampl"
            "e.invalid/materials/synthetic-long-identifier-0123456789abcdef01234"
            "56789abcdef0123456789abcdef"
        ),
        "category": "productivity",
        "status": "deployed",
        "votes_up": 12,
        "votes_down": 3,
        "score": 9,
        "deploy_url": "https://example.invalid/live-demo",
        "repo_url": "https://github.com/Example/synthetic-live-repository-with-long-name",
        "tech_stack": [
            "TypeScript",
            "Python",
            "PostgreSQL",
            "Synthetic technology label with long content",
            "Redis",
        ],
        "hackathon_id": None,
        "creator_agent_id": "00000000-0000-4000-8000-000000000111",
        "agent_name": "Synthetic creator — длинное имя автора для проверки переноса",
        "agent_handle": "synthetic-live",
        "github_stars": 1234,
        "created_at": "2026-10-19T09:00:00Z",
    },
    {
        "id": "00000000-0000-4000-8000-000000000102",
        "title": ("Synthetic building project — длинное название ещё не опубликованного проекта"),
        "description": (
            "Synthetic non-live description. Длинный текст второго проекта для п"
            "роверки переноса и доступности независимых кнопок. Building means s"
            "tatus, not a health result."
        ),
        "category": "social",
        "status": "building",
        "votes_up": 27,
        "votes_down": 5,
        "score": 22,
        "deploy_url": None,
        "repo_url": "https://github.com/Example/synthetic-building-repository",
        "tech_stack": [
            "TypeScript",
            "SQLite",
        ],
        "hackathon_id": None,
        "creator_agent_id": "00000000-0000-4000-8000-000000000112",
        "agent_name": "Synthetic creator — длинное имя автора для проверки переноса",
        "agent_handle": "synthetic-building",
        "github_stars": 42,
        "created_at": "2026-09-01T09:00:00Z",
    },
]
VOTE_SUCCESS = {
    "project_id": "00000000-0000-4000-8000-000000000101",
    "votes_up": 13,
    "votes_down": 3,
    "score": 10,
}
SERVER_ERROR = {"detail": "Synthetic projects unavailable"}
MIN_TEXT_PX = 12

ACTION = """el => {
    const r = el.getBoundingClientRect(), card = el.closest('article, main');
    const p = card.getBoundingClientRect(), style = getComputedStyle(el);
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return {text: el.innerText, name: el.getAttribute('aria-label') || el.innerText,
        x: r.x, right: r.right, width: r.width, height: r.height,
        top: r.top + scrollY, bottom: r.bottom + scrollY,
        viewportTop: r.top, viewportBottom: r.bottom, viewportHeight: innerHeight,
        headerBottom: document.querySelector('header')?.getBoundingClientRect().bottom || 0,
        parentX: p.x, parentRight: p.right,
        hit: el === hit || el.contains(hit), focused: el === document.activeElement,
        focusVisible: el.matches(':focus-visible'), outline: style.outlineStyle,
        outlineWidth: parseFloat(style.outlineWidth)};
}"""
TEXT = """card => {
    const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT), result = [];
    while (walker.nextNode()) {
        const node = walker.currentNode, el = node.parentElement, text = node.textContent.trim();
        if (text && !el.closest('svg') && el.checkVisibility({opacityProperty: true}))
            result.push({text, size: parseFloat(getComputedStyle(el).fontSize)});
    }
    return result;
}"""


class ProjectCardTests(unittest.TestCase):
    """Check card controls without inheriting or rediscovering prior browser cases."""

    @classmethod
    def setUpClass(cls):
        detail.DetailWidthTests.setUpClass.__func__(cls)

    def route_request(self, route):
        """Serve exact list contracts, hold synthetic votes and reject unknown traffic."""
        request, target = route.request, urlparse(route.request.url)
        local = target.netloc == urlparse(self.options.base_url).netloc
        if local and request.method == "GET" and target.path == "/api/v1/projects":
            self.requested.append(target.path + "?" + target.query)
            query = parse_qs(target.query)
            if set(query) - {"limit", "offset", "category", "status"}:
                self.unexpected.append("Unknown project query")
                route.fulfill(status=503, json=SERVER_ERROR)
                return
            rows = [
                p
                for p in PROJECTS
                if all(p[key] == query[key][0] for key in ("category", "status") if key in query)
            ]
            offset = int(query.get("offset", ["0"])[0])
            limit = int(query.get("limit", ["100"])[0])
            rows = sorted(rows, key=lambda p: p["created_at"], reverse=True)[
                offset : offset + limit
            ]
            payload = SERVER_ERROR if self.mode == "error" else [] if self.mode == "empty" else rows
            route.fulfill(status=503 if self.mode == "error" else 200, json=payload)
        elif (
            local
            and request.method == "POST"
            and target.path == f"/api/v1/projects/{PROJECTS[0]['id']}/vote"
        ):
            self.votes.append(request.post_data_json)
            self.pending_votes.append(route)
        else:
            detail.DetailWidthTests.route_request(self, route)

    def open_page(self, locale, width, mode="populated"):
        """Open isolated anonymous state and wait for finite font/animation layout."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.mode, self.unexpected, self.requested = mode, [], []
        self.votes, self.pending_votes = [], []
        context.route("**/*", self.route_request)
        page = context.new_page()
        page.set_default_timeout(7000)
        self.assertEqual(page.goto(self.options.base_url + "/projects").status, 200)
        expect(page.locator("main h1")).to_be_visible()
        expect(page.locator("html")).to_have_attribute("lang", locale)
        selector = {
            "populated": "article",
            "error": '[role="alert"]',
            "empty": 'a[href="/docs/connect"]',
        }[mode]
        expect(page.locator("main").locator(selector).first).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page

    def save(self, page, tag, records):
        """Retain metrics and actual UI before any size or text assertion fails."""
        records["page"] = page.evaluate(
            "() => ({viewport: innerWidth, documentWidth: document.documentElement.scrollWidth})"
        )
        records["requests"], records["unexpected"] = list(self.requested), list(self.unexpected)
        (self.options.screenshots / f"{tag}.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2)
        )
        page.screenshot(path=str(self.options.screenshots / f"{tag}.png"), full_page=True)

    def measure_actions(self, controls):
        """Measure each independent target after scrolling its own hit area into view."""
        records = []
        for control in controls.all():
            control.scroll_into_view_if_needed()
            records.append(control.evaluate(ACTION))
        return records

    def keyboard_actions(self, page, controls, tag):
        """Reach controls by Tab and capture the actual visible focus indication."""
        records = []
        for control in controls.all():
            for _ in range(80):
                page.keyboard.press("Tab")
                if control.evaluate("el => el === document.activeElement"):
                    break
            records.append(control.evaluate(ACTION))
        page.screenshot(path=str(self.options.screenshots / f"{tag}-focus.png"))
        return records

    def assert_actions(self, records):
        """Require complete independent hit areas inside their card and viewport."""
        for record in records:
            with self.subTest(action=record["name"]):
                self.assertGreaterEqual(record["width"], 44, record)
                self.assertGreaterEqual(record["height"], 44, record)
                self.assertGreaterEqual(record["x"], max(0, record["parentX"]) - 1, record)
                self.assertLessEqual(record["right"], record["parentRight"] + 1, record)
                self.assertGreaterEqual(
                    record["viewportTop"], max(0, record["headerBottom"]) - 1, record
                )
                self.assertLessEqual(record["viewportBottom"], record["viewportHeight"] + 1, record)
                self.assertTrue(record["hit"], record)

    def test_populated_cards_targets_text_and_keyboard_in_six_layouts(self):
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    page = self.open_page(locale, width)
                    try:
                        tag = f"cards-{locale}-{width}"
                        cards = page.locator("main article")
                        controls = cards.locator("h3 a, button, a[target]")
                        records = {
                            "actions": self.measure_actions(controls),
                            "text": cards.evaluate_all("cards => cards.flatMap(" + TEXT + ")"),
                        }
                        records["keyboard"] = self.keyboard_actions(page, controls, tag)
                        self.save(page, tag, records)
                        with self.subTest(property="controls"):
                            self.assertEqual(cards.count(), 2)
                            self.assertEqual(len(records["actions"]), 9)
                            self.assert_actions(records["actions"] + records["keyboard"])
                        with self.subTest(property="text"):
                            self.assertTrue(records["text"])
                            self.assertEqual(
                                [t for t in records["text"] if t["size"] < MIN_TEXT_PX], []
                            )
                        for focus in records["keyboard"]:
                            with self.subTest(focus=focus["name"]):
                                self.assertTrue(focus["focused"] and focus["focusVisible"], focus)
                                self.assertNotEqual(focus["outline"], "none", focus)
                                self.assertGreater(focus["outlineWidth"], 0, focus)
                        self.assertLessEqual(records["page"]["documentWidth"], width)
                        self.assertEqual(self.unexpected, [])
                    finally:
                        page.context.close()

    def test_public_vote_pending_lock_success_and_error_recovery(self):
        page = self.open_page("en", 390)
        try:
            selector = f'a[href="/projects/{PROJECTS[0]["id"]}"]'
            card = page.locator("article").filter(has=page.locator(selector))
            up, down = card.locator("button").nth(0), card.locator("button").nth(1)
            up.click()
            expect(up).to_be_disabled()
            expect(down).to_be_disabled()
            box = up.bounding_box()
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            self.assertEqual(self.votes, [{"vote": 1}])
            self.pending_votes.pop(0).fulfill(json=VOTE_SUCCESS)
            expect(up).to_have_text("↑13")
            expect(down).to_have_text("↓3")
            expect(up).to_be_enabled()
            down.click()
            expect(down).to_be_disabled()
            self.pending_votes.pop(0).fulfill(status=503, json=SERVER_ERROR)
            expect(up).to_be_enabled()
            expect(down).to_be_enabled()
            expect(up).to_have_text("↑13")
            expect(down).to_have_text("↓3")
            self.save(page, "vote-recovered", {"votes": self.votes})
            self.assertEqual(self.votes, [{"vote": 1}, {"vote": -1}])
            self.assertEqual(self.unexpected, [])
        finally:
            page.context.close()

    def test_empty_docs_and_first_error_retry_remain_bounded(self):
        for locale in ("en", "ru"):
            for mode in ("empty", "error"):
                with self.subTest(locale=locale, mode=mode):
                    page = self.open_page(locale, 320, mode)
                    try:
                        controls = page.locator(
                            "main a[href^='/docs/'], main [role='alert'] button"
                        )
                        records = {"actions": self.measure_actions(controls)}
                        self.save(page, f"{mode}-{locale}", records)
                        self.assertEqual(page.locator("article").count(), 0)
                        self.assertEqual(len(records["actions"]), 2 if mode == "empty" else 1)
                        self.assert_actions(records["actions"])
                        self.assertLessEqual(records["page"]["documentWidth"], 320)
                        if mode == "error":
                            self.mode = "populated"
                            controls.click()
                            expect(page.locator("article")).to_have_count(2)
                            self.assertEqual(len(self.requested), 2)
                        self.assertEqual(self.unexpected, [])
                    finally:
                        page.context.close()


if __name__ == "__main__":
    ProjectCardTests.options, unittest_args = detail.browser_options.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
