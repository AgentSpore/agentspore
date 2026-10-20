"""Keep full public detail prose within the viewport using synthetic HTTP data.

python test_detail_width.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
Fixtures follow AgentProfile, BlogService.list_agent_posts, project_repo.get_project_by_id
and the existing public ownership/contributor response contracts. The 77-character
identifier is a synthetic stress example, not observed customer content.
"""

import json
import unittest
from urllib.parse import urlparse

import auth_form_fixtures as browser_options
from playwright.sync_api import expect, sync_playwright

STRESS_IDENTIFIER = "synthetic_long_identifier_for_horizontal_code_scrolling_012345678901234567890"
RESOURCES = {
    "agent": {
        "id": "00000000-0000-4000-8000-000000000001",
        "name": "Synthetic Agent — длинное имя для проверки переноса",
        "handle": "synthetic-agent",
        "agent_type": "external",
        "model_provider": "synthetic",
        "model_name": "synthetic-model",
        "specialization": "programmer",
        "skills": ["Synthetic skill", "Проверка переноса длинного навыка"],
        "karma": 1234,
        "projects_created": 12,
        "code_commits": 345,
        "reviews_done": 67,
        "last_heartbeat": "2026-10-19T09:00:00Z",
        "is_active": True,
        "created_at": "2026-10-19T08:00:00Z",
        "dna_risk": 7,
        "dna_speed": 6,
        "dna_verbosity": 5,
        "dna_creativity": 8,
        "bio": "Synthetic biography. Длинное описание агента для проверки типографики и "
        "переноса текста.",
        "fork_count": 3,
        "is_hosted": False,
    },
    "project": {
        "id": "00000000-0000-4000-8000-000000000002",
        "title": "Synthetic Project — проверка длинного заголовка",
        "description": "Synthetic text for typography. Проверка русского текста, переносов "
        "и длинной ссылки.\n"
        "\n"
        "**Readable emphasis**, [fixture "
        "link](https://example.invalid/fixture), `synthetic_code`.\n"
        "\n"
        "```text\n"
        "synthetic_long_identifier_for_horizontal_code_scrolling_012345678901234567890\n"
        "```",
        "category": "other",
        "status": "deployed",
        "votes_up": 12,
        "votes_down": 2,
        "score": 10,
        "deploy_url": "https://example.invalid/demo",
        "repo_url": "https://example.invalid/repository",
        "tech_stack": ["TypeScript", "Python", "Synthetic long technology label"],
        "hackathon_id": None,
        "creator_agent_id": "00000000-0000-4000-8000-000000000001",
        "agent_name": "Synthetic Agent — длинное имя для проверки переноса",
        "agent_handle": "synthetic-agent",
        "github_stars": 21,
        "created_at": "2026-10-19T08:00:00Z",
    },
    "activity": [
        {
            "id": "00000000-0000-4000-8000-000000000013",
            "agent_id": "00000000-0000-4000-8000-000000000001",
            "agent_name": "Synthetic Agent — длинное имя для проверки переноса",
            "specialization": "programmer",
            "action_type": "code_commit",
            "description": "Synthetic activity. Длинное описание изменения для проверки.",
            "project_id": "00000000-0000-4000-8000-000000000002",
            "metadata": {},
            "ts": "2026-10-19T09:00:00Z",
        }
    ],
    "github_activity": {
        "activities": [
            {
                "id": "00000000-0000-4000-8000-000000000014",
                "action_type": "code_commit",
                "description": "Synthetic commit description.",
                "project_id": "00000000-0000-4000-8000-000000000002",
                "project_title": "Synthetic Project — проверка длинного заголовка",
                "project_repo_url": "https://example.invalid/repository",
                "github_url": "https://example.invalid/commit",
                "commit_sha": "0123456789abcdef0123456789abcdef01234567",
                "branch": "synthetic-branch-with-long-name",
                "issue_number": None,
                "issue_title": None,
                "pr_number": None,
                "pr_url": None,
                "issues_created": None,
                "commit_message": "Synthetic commit message. Проверка длинного текста.",
                "fix_description": None,
                "dispute_reason": None,
                "created_at": "2026-10-19T09:00:00Z",
            }
        ],
        "count": 1,
    },
    "badges": [
        {
            "badge_id": "synthetic-badge",
            "name": "Synthetic badge — тестовый знак",
            "description": "Synthetic badge description. Проверка подсказки.",
            "icon": "✦",
            "category": "contribution",
            "rarity": "rare",
            "awarded_at": "2026-10-19T09:00:00Z",
        }
    ],
    "agent_blog": {
        "posts": [
            {
                "id": "00000000-0000-4000-8000-000000000004",
                "agent_id": "00000000-0000-4000-8000-000000000001",
                "agent_name": "Synthetic Agent — длинное имя для проверки переноса",
                "agent_handle": "synthetic-agent",
                "title": "Synthetic Post — проверка длинного заголовка публикации",
                "content": "# Synthetic heading\n"
                "\n"
                "Synthetic text for typography. Проверка русского текста, "
                "переносов и длинной ссылки.\n"
                "\n"
                "**Readable emphasis**, [fixture "
                "link](https://example.invalid/fixture), "
                "`synthetic_code`.\n"
                "\n"
                "```text\n"
                "synthetic_long_identifier_for_horizontal_code_scrolling_012345678901234567890\n"
                "```",
                "reactions": {"like": 12, "fire": 0, "insightful": 2, "funny": 0},
                "created_at": "2026-10-19T08:00:00Z",
                "updated_at": "2026-10-19T09:00:00Z",
            }
        ],
        "total": 1,
        "limit": 10,
        "offset": 0,
    },
    "ownership": {
        "project_id": "00000000-0000-4000-8000-000000000002",
        "project_title": "Synthetic Project — проверка длинного заголовка",
        "contributors": [
            {
                "agent_id": "00000000-0000-4000-8000-000000000001",
                "agent_name": "Synthetic Agent — длинное имя для проверки переноса",
                "owner_user_id": "00000000-0000-4000-8000-000000000005",
                "owner_name": "Synthetic Owner — тестовый участник",
                "wallet_address": None,
                "contribution_points": 120,
                "share_pct": 100.0,
            }
        ],
    },
    "contributors": {
        "contributors": [
            {
                "id": "00000000-0000-4000-8000-000000000007",
                "role": "contributor",
                "contribution_points": 120,
                "joined_at": "2026-10-19T09:00:00Z",
                "user_id": "00000000-0000-4000-8000-000000000005",
                "user_name": "Synthetic Owner — тестовый участник",
                "user_email": "synthetic-owner@example.invalid",
                "wallet_address": None,
            }
        ],
        "total": 1,
    },
    "model_usage_empty": {
        "agent_id": "00000000-0000-4000-8000-000000000001",
        "total_calls": 0,
        "unique_models": 0,
        "by_task": [],
        "by_model": [],
    },
}

AGENT_ID = RESOURCES["agent"]["id"]
PROJECT_ID = RESOURCES["project"]["id"]
RESPONSES = {
    f"/api/v1/agents/{AGENT_ID}": RESOURCES["agent"],
    "/api/v1/activity": RESOURCES["activity"],
    f"/api/v1/agents/{AGENT_ID}/model-usage": RESOURCES["model_usage_empty"],
    f"/api/v1/agents/{AGENT_ID}/github-activity": RESOURCES["github_activity"],
    f"/api/v1/agents/{AGENT_ID}/badges": RESOURCES["badges"],
    f"/api/v1/blog/agents/{AGENT_ID}/posts": RESOURCES["agent_blog"],
    f"/api/v1/projects/{PROJECT_ID}": RESOURCES["project"],
    f"/api/v1/projects/{PROJECT_ID}/ownership": RESOURCES["ownership"],
    f"/api/v1/projects/{PROJECT_ID}/contributors": RESOURCES["contributors"],
}
TEXT_GEOMETRY = """(el, token) => {
    const range = document.createRange(), text = el.firstChild;
    range.selectNodeContents(el);
    const lines = [...range.getClientRects()].filter(r => r.width > 0)
        .map(r => ({x: r.x, right: r.right, y: r.y, height: r.height}));
    const start = text.textContent.indexOf(token);
    range.setStart(text, start); range.setEnd(text, start + token.length);
    const rect = el.getBoundingClientRect(), css = getComputedStyle(el), ancestors = [];
    for (let node = el.parentElement; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (['hidden', 'clip', 'scroll', 'auto'].includes(style.overflowX))
            ancestors.push({tag: node.tagName, overflowX: style.overflowX});
    }
    return {documentWidth: document.documentElement.scrollWidth, viewport: innerWidth,
        bodyFont: getComputedStyle(document.body).fontFamily,
        bodyWidth: document.body.scrollWidth, paragraph: {x: rect.x, right: rect.right},
        clientWidth: el.clientWidth, scrollWidth: el.scrollWidth, lines,
        token: [...range.getClientRects()].map(r => ({x: r.x, right: r.right})), ancestors,
        whiteSpace: css.whiteSpace, overflowWrap: css.overflowWrap,
        textOverflow: css.textOverflow, lineClamp: css.webkitLineClamp};
}"""


class DetailWidthTests(unittest.TestCase):
    """Exercise actual public detail fetches and rendered, unclipped text ranges."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(executable_path=cls.options.executable)
        cls.addClassCleanup(cls.browser.close)
        cls.options.screenshots.mkdir(parents=True, exist_ok=True)

    def route_request(self, route):
        """Fail closed for unknown API calls, writes and external requests."""
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != urlparse(self.options.base_url).netloc:
            self.unexpected.append("external request")
            route.abort()
        elif not target.path.startswith("/api/") and request.method == "GET":
            route.continue_()
        elif request.method == "GET" and target.path in RESPONSES:
            self.requested.append(target.path)
            route.fulfill(json=RESPONSES[target.path])
        else:
            self.unexpected.append(f"{request.method} {target.path}")
            route.fulfill(status=503, json={"detail": "No synthetic fixture for this endpoint"})

    def open_page(self, kind, locale, width):
        """Load anonymous populated fixtures and wait for final font/animation layout."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.unexpected, self.requested = [], []
        context.route("**/*", self.route_request)
        page = context.new_page()
        page.set_default_timeout(7000)
        identifier = AGENT_ID if kind == "agent" else PROJECT_ID
        response = page.goto(f"{self.options.base_url}/{kind}s/{identifier}")
        self.assertEqual(response.status, 200)
        expect(page.locator("main h1") if kind == "agent" else page.locator("h1")).to_be_visible()
        expect(page.locator("html")).to_have_attribute("lang", locale)
        content = (
            RESOURCES["agent_blog"]["posts"][0]["content"]
            if kind == "agent"
            else RESOURCES["project"]["description"]
        )
        paragraph = page.locator("main p").filter(has_text=STRESS_IDENTIFIER)
        expect(paragraph).to_have_count(1)
        self.assertEqual(paragraph.text_content(), content)
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page, paragraph

    def sample_actions(self, page, kind):
        """Check main action hit areas without invoking writes or external destinations."""
        selectors = (
            [f'a[href="/agents/{AGENT_ID}/chat"]', "main button"]
            if kind == "agent"
            else [
                f'a[href="{RESOURCES["project"]["deploy_url"]}"]',
                f'a[href="{RESOURCES["project"]["repo_url"]}"]',
                "main button",
            ]
        )
        records = []
        for selector in selectors:
            controls = page.locator(selector)
            self.assertGreater(controls.count(), 0)
            for control in controls.all():
                if not control.is_visible():
                    continue
                control.scroll_into_view_if_needed()
                record = control.evaluate("""el => {
                    const r = el.getBoundingClientRect();
                    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                    return {x: r.x, right: r.right, y: r.y, bottom: r.bottom,
                        hit: el === hit || el.contains(hit), href: el.getAttribute('href')};
                }""")
                records.append(record)
                self.assertGreaterEqual(record["x"], -1, record)
                self.assertLessEqual(record["right"], page.viewport_size["width"] + 1, record)
                self.assertGreaterEqual(record["y"], -1, record)
                self.assertLessEqual(record["bottom"], page.viewport_size["height"] + 1, record)
                self.assertTrue(record["hit"], record)
        return records

    def assert_text_geometry(self, metrics):
        """Reject document overflow, clipped prose and out-of-bounds text lines."""
        self.assertLessEqual(metrics["documentWidth"], metrics["viewport"] + 1)
        self.assertLessEqual(metrics["scrollWidth"], metrics["clientWidth"] + 1)
        self.assertNotEqual(metrics["textOverflow"], "ellipsis")
        self.assertIn(metrics["lineClamp"], ("none", "0"))
        self.assertTrue(metrics["lines"])
        for rect in metrics["lines"] + metrics["token"]:
            self.assertGreaterEqual(rect["x"], max(0, metrics["paragraph"]["x"]) - 1)
            self.assertLessEqual(
                rect["right"], min(metrics["viewport"], metrics["paragraph"]["right"]) + 1
            )

    def test_full_public_detail_text_and_actions_stay_within_viewport(self):
        for kind in ("agent", "project"):
            for locale in ("en", "ru"):
                for width in (320, 390, 1280):
                    with self.subTest(kind=kind, locale=locale, width=width):
                        page, paragraph = self.open_page(kind, locale, width)
                        try:
                            tag = f"{kind}-{locale}-{width}"
                            metrics = paragraph.evaluate(TEXT_GEOMETRY, STRESS_IDENTIFIER)
                            metrics["requests"] = self.requested
                            self.assertEqual(self.unexpected, [])
                            page.screenshot(
                                path=str(self.options.screenshots / f"{tag}.png"), full_page=True
                            )
                            (self.options.screenshots / f"{tag}.json").write_text(
                                json.dumps(metrics, indent=2)
                            )
                            with self.subTest(property="prose"):
                                self.assert_text_geometry(metrics)
                                self.assertEqual(
                                    metrics["whiteSpace"],
                                    "pre-wrap" if kind == "agent" else "normal",
                                )
                            actions = self.sample_actions(page, kind)
                            (self.options.screenshots / f"{tag}-actions.json").write_text(
                                json.dumps(actions, indent=2)
                            )
                            page.screenshot(
                                path=str(self.options.screenshots / f"{tag}-actions.png")
                            )
                            self.assertEqual(self.unexpected, [])
                        finally:
                            page.context.close()


if __name__ == "__main__":
    DetailWidthTests.options, unittest_args = browser_options.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
