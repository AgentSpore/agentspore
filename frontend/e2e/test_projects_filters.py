"""Projects list: search, status chips and sort in the first screen; period and category behind Filters.

python test_projects_filters.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
Synthetic HTTP only; the stand is built with an empty NEXT_PUBLIC_API_URL.
"""

import unittest
from argparse import Namespace
from pathlib import Path

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect
from test_project_list import COPY, PROJECTS, ProjectListHTTP

AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
FILTERS = {"en": "Filters", "ru": "Фильтры"}
IMPORTED = dict(PROJECTS[0], recovery_provenance={"source": "github"})
LIST = [IMPORTED] + PROJECTS[1:]


class ListHTTP(ProjectListHTTP):
    def payload(self, query):
        return [p for p in LIST if all(p[k] == query[k] for k in ("category", "status") if k in query)]


class ProjectsFiltersTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width, height):
        context = self.browser.new_context(viewport={"width": width, "height": height}, service_workers="block")
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = ListHTTP(self.options.base_url, False)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.clock.install(time="2026-10-20T12:00:00Z")
        self.page.goto(self.options.base_url + "/projects", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        expect(self.main.locator("article")).to_have_count(4)
        self.page.evaluate("document.fonts.ready")
        self.page.wait_for_timeout(900)

    def in_fold(self, locator, height):
        box = locator.bounding_box()
        self.assertIsNotNone(box)
        self.assertLessEqual(box["y"] + box["height"], height)

    def check(self, locale, label, width, height):
        copy = COPY[locale]
        self.page.screenshot(path=str(self.options.screenshots / f"projects-{locale}-{label}.png"))
        self.in_fold(self.main.get_by_role("textbox", name=copy[0], exact=True), height)
        status = self.main.get_by_role("group", name=copy[3], exact=True)
        self.in_fold(status, height)
        self.in_fold(status.get_by_role("button", name=copy[6], exact=True), height)
        sort = self.main.get_by_role("button", name=copy[10], exact=True)
        self.in_fold(sort, height)
        toggle = self.main.get_by_role("button", name=FILTERS[locale])
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(self.main.get_by_role("group", name=copy[2], exact=True)).to_be_hidden()
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        category = self.main.get_by_role("group", name=copy[2], exact=True)
        self.page.screenshot(path=str(self.options.screenshots / f"projects-{locale}-{label}-filters.png"))
        calls = len(self.http.calls)
        category.get_by_role("button", name=copy[4], exact=True).click()
        expect(self.main.locator("article")).to_have_count(2)
        self.assertEqual(self.http.calls[-1], {"limit": "100", "category": "productivity"})
        self.assertEqual(len(self.http.calls), calls + 1)
        self.main.get_by_role("button", name=copy[7], exact=True).click()
        expect(self.main.locator("article")).to_have_count(1)
        self.assertEqual(len(self.http.calls), calls + 1)

    def check_footnote_and_page(self, width):
        last = self.main.locator("article").last.bounding_box()
        note = self.main.locator("[data-testid=projects-footnote]").bounding_box()
        self.assertGreater(note["y"], last["y"] + last["height"] - 1)
        self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
        self.page.add_script_tag(path=str(AXE))
        violations = self.page.evaluate(
            "axe.run(document.querySelector('#main-content'), {runOnly: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']})"
            ".then(r => r.violations.map(v => v.id + ': ' + v.nodes.length))"
        )
        self.assertEqual(violations, [])

    def test_first_screen_filters_and_footnote(self):
        for locale in COPY:
            for label, (width, height) in VIEWPORTS.items():
                with self.subTest(locale=locale, viewport=label):
                    self.open_page(locale, width, height)
                    self.check(locale, label, width, height)
                    self.check_footnote_and_page(width)
                    # The visit beacon and header link prefetches are aborted by the fixture; anything else is a surprise.
                    surprises = [r for r in self.http.unexpected if not (r["method"] == "GET" and "_rsc=" in r["url"]) and not (r["method"] == "POST" and r["url"].split("?")[0] == "/api/v1/funnel/visit")]
                    self.assertEqual(surprises, [])
                    self.doCleanups()


if __name__ == "__main__":
    ProjectsFiltersTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining], verbosity=2)
