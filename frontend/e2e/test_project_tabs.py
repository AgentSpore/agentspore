"""Project page tabs follow the WAI-ARIA tabs pattern; synthetic HTTP only, never a live service."""

import re
import unittest
from argparse import Namespace
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect

import funnel_beacon
from auth_form_fixtures import BrowserFixture, parse_options
from test_chat_ownership import PROJECT_ID, OwnershipHTTP
from test_project_detail_reads import CONTRIBUTOR, OWNERSHIP_DATA, PROJECT_DATA, TITLE

AXE = Path(__file__).resolve().parents[1] / "node_modules/axe-core/axe.min.js"
BASE = f"/api/v1/projects/{PROJECT_ID}"
READS = {
    BASE: PROJECT_DATA,
    BASE + "/ownership": OWNERSHIP_DATA,
    BASE + "/contributors": {"contributors": [CONTRIBUTOR], "total": 1},
    f"/api/v1/chat/project/{PROJECT_ID}/messages": [],
}
LABELS = {
    "en": ("Overview", "Discussion", "Contributors", "Ownership"),
    "ru": ("Обзор", "Обсуждение", "Участники", "Владение"),
}
SECTIONS = {"en": "Project sections", "ru": "Разделы проекта"}
IDS = ("overview", "chat", "contributors", "ownership")


class TabsHTTP(OwnershipHTTP):
    def __init__(self, base_url):
        super().__init__(base_url, "project")

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme == "http" and target.netloc == self.origin and request.method == "GET" and target.path in READS:
            return route.fulfill(json=READS[target.path])
        if funnel_beacon.answer(route, target):
            return
        if target.path.startswith("/api/") and target.netloc == self.origin:
            return self.reject(route)
        return super().route(route)


class ProjectTabsTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = TabsHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.goto(self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded")
        expect(self.page.get_by_role("heading", level=1, name=TITLE)).to_be_visible()
        self.tablist = self.page.get_by_role("tablist")
        self.tabs = [self.page.locator(f"#project-tab-{key}") for key in IDS]
        self.panel = self.page.get_by_role("tabpanel")

    def assert_selected(self, index):
        for i, tab in enumerate(self.tabs):
            expect(tab).to_have_attribute("aria-selected", "true" if i == index else "false")
            expect(tab).to_have_attribute("tabindex", "0" if i == index else "-1")
        expect(self.panel).to_have_count(1)
        expect(self.panel).to_have_attribute("id", f"project-panel-{IDS[index]}")
        expect(self.panel).to_have_attribute("aria-labelledby", f"project-tab-{IDS[index]}")
        expect(self.tabs[index]).to_have_attribute("aria-controls", f"project-panel-{IDS[index]}")

    def test_roles_names_and_roving_focus_by_keyboard(self):
        for locale, labels in LABELS.items():
            with self.subTest(locale=locale):
                self.open_page(locale)
                expect(self.tablist).to_have_attribute("aria-label", SECTIONS[locale])
                expect(self.page.get_by_role("tab")).to_have_count(4)
                self.assert_selected(0)
                for i, label in enumerate(labels):
                    expect(self.page.get_by_role("tab", name=re.compile("^" + label))).to_have_attribute(
                        "id", f"project-tab-{IDS[i]}"
                    )
                self.tabs[0].focus()
                for expected in (1, 2, 3, 0):
                    self.page.keyboard.press("ArrowRight")
                    self.assert_selected(expected)
                    expect(self.tabs[expected]).to_be_focused()
                self.page.keyboard.press("ArrowLeft")
                self.assert_selected(3)
                self.page.keyboard.press("Home")
                self.assert_selected(0)
                expect(self.tabs[0]).to_be_focused()
                self.page.keyboard.press("End")
                self.assert_selected(3)
                expect(self.tabs[3]).to_be_focused()
                self.page.keyboard.press("ArrowDown")
                self.assert_selected(3)
                self.assertEqual(self.http.unexpected, [])

    def test_tab_key_leaves_tablist_into_panel(self):
        self.open_page("en")
        self.tabs[0].focus()
        self.page.keyboard.press("Tab")
        expect(self.panel).to_be_focused()

    def test_tab_region_axe_clean(self):
        # Panel content (e.g. the scrollable .chat-panel, a low-contrast disabled control) predates the tab pattern; check the tab structure itself.
        self.open_page("en")
        self.page.add_script_tag(path=str(AXE))
        for index in range(4):
            self.tabs[index].click()
            self.assert_selected(index)
            self.page.wait_for_timeout(300)
            violations = self.page.evaluate(
                "axe.run({include: ['[role=tablist]', '[role=tabpanel]', '[id^=project-tab-]'], exclude: ['[role=tabpanel] > *']}, {runOnly: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']})"
                ".then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(', ')))"
            )
            self.assertEqual(violations, [], f"tab {IDS[index]}")
        self.assertEqual(self.http.unexpected, [])


if __name__ == "__main__":
    ProjectTabsTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
