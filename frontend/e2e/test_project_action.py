"""Check the project page primary action, one-line status badge and hidden zero metrics.

python test_project_action.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
Synthetic project reads only; the fixtures reuse the project-detail suites.
"""

import json
import unittest
from argparse import Namespace
from pathlib import Path

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect
from test_chat_ownership import PROJECT_ID
from test_project_detail_metadata import BASE, DEMO, PROJECT, REPO, MetadataHTTP

TITLE = "Synthetic action project"
LIVE = dict(PROJECT, title=TITLE, status="building", votes_up=0, votes_down=0)
SOURCE_ONLY = dict(LIVE, deploy_url=None)
BARE = dict(LIVE, deploy_url=None, repo_url=None)
LABELS = {
    "en": {
        "live": "Open service",
        "source": "View source",
        "votes": "Votes",
        "people": "Contributors",
    },
    "ru": {
        "live": "Открыть сервис",
        "source": "Смотреть исходный код",
        "votes": "Голоса",
        "people": "Участники",
    },
}
VIEWPORTS = ((1440, 900), (390, 844))
AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]


class ActionHTTP(MetadataHTTP):
    project = LIVE

    def read(self, route, target):
        if target.path == BASE and route.request.method == "GET" and not target.query:
            self.calls.append(target.path)
            return route.fulfill(json=self.project)
        return super().read(route, target)


class ProjectActionTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, size, project):
        width, height = size
        http = type("Http", (ActionHTTP,), {"project": project})(self.options.base_url)
        context = self.browser.new_context(
            viewport={"width": width, "height": height},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        self.addCleanup(http.close, context)
        context.add_cookies(
            [
                {
                    "name": "agentspore_locale",
                    "value": locale,
                    "url": self.options.base_url,
                }
            ]
        )
        context.route("**/*", http.route)
        page = context.new_page()
        page.set_default_timeout(7000)
        page.goto(
            self.options.base_url + f"/projects/{PROJECT_ID}",
            wait_until="domcontentloaded",
        )
        expect(
            page.get_by_role("heading", level=1, name=TITLE, exact=True)
        ).to_be_visible()
        page.evaluate(
            "async()=>{await document.fonts.ready;await new Promise(r=>setTimeout(r,500));}"
        )
        return page

    def shot(self, page, name):
        page.screenshot(path=str(self.options.screenshots / f"{name}.png"))

    def status_lines(self, page):
        return page.evaluate("""()=>{const el=[...document.querySelectorAll('main,div')].flatMap(
            d=>[...d.querySelectorAll('span.capitalize')]).find(s=>s.className.includes('capitalize'));
            return {rects:el.getClientRects().length,nowrap:getComputedStyle(el).whiteSpace}}""")

    def axe_violations(self, page, ruleset):
        page.add_script_tag(path=str(AXE))
        return page.evaluate(
            """async(args)=>{const r=await axe.run({include:[['#main-content'],['h1']]},
            {runOnly:{type:'tag',values:args.tags}});
            return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))}""",
            {"tags": ruleset},
        )

    def test_primary_action_fold_status_zero_metrics_axe(self):
        for locale, copy in LABELS.items():
            for size in VIEWPORTS:
                with self.subTest(locale=locale, size=size):
                    page = self.open_page(locale, size, LIVE)
                    link = page.get_by_role("link", name=copy["live"], exact=True)
                    expect(link).to_have_attribute("href", DEMO)
                    expect(link).to_have_attribute("rel", "noopener noreferrer")
                    box = link.bounding_box()
                    self.assertLessEqual(box["y"] + box["height"], size[1])
                    status = self.status_lines(page)
                    self.assertEqual(status, {"rects": 1, "nowrap": "nowrap"})
                    main = page.locator("#main-content")
                    expect(main.get_by_text(copy["people"], exact=True)).to_have_count(
                        0
                    )
                    expect(main.locator("button", has_text="↑")).to_have_text("↑")
                    expect(main.locator("button", has_text="↓")).to_have_text("↓")
                    self.shot(page, f"after-{locale}-{size[0]}-live")
                    violations = self.axe_violations(page, AXE_TAGS)
                    (
                        self.options.screenshots / f"axe-{locale}-{size[0]}.json"
                    ).write_text(json.dumps(violations))
                    self.assertEqual(violations, [])
                    self.doCleanups()

    def test_repository_fallback_and_empty_block(self):
        for locale, copy in LABELS.items():
            with self.subTest(locale=locale):
                page = self.open_page(locale, VIEWPORTS[1], SOURCE_ONLY)
                expect(
                    page.get_by_role("link", name=copy["source"], exact=True)
                ).to_have_attribute("href", REPO)
                expect(
                    page.get_by_role("link", name=copy["live"], exact=True)
                ).to_have_count(0)
                self.doCleanups()
                page = self.open_page(locale, VIEWPORTS[1], BARE)
                for key in ("live", "source"):
                    expect(
                        page.get_by_role("link", name=copy[key], exact=True)
                    ).to_have_count(0)
                self.doCleanups()


if __name__ == "__main__":
    ProjectActionTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
