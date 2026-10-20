import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

import project_card_contrast_paint as paint
import test_project_cards as cards
from auth_form_fixtures import BrowserFixture
from playwright.sync_api import Browser, expect

STATUSES = (
    "deployed",
    "submitted",
    "proposed",
    "building",
    "review",
    "archived",
    "active",
    "fallback",
)


class ProjectCardContrastTests(BrowserFixture, unittest.TestCase):
    """Measure calibrated contrast without rediscovering inherited fixture tests."""

    browser: Browser
    options: Namespace

    def route_request(self, route):
        target = urlparse(route.request.url)
        if (
            target.netloc == urlparse(self.options.base_url).netloc
            and route.request.method == "GET"
            and target.path == "/api/v1/projects"
        ):
            query = parse_qs(target.query)
            self.requested.append(target.path + "?" + target.query)
            if set(query) - {"limit", "offset", "category", "status"}:
                self.unexpected.append("Unknown project query")
                route.fulfill(status=503, json=cards.SERVER_ERROR)
                return
            rows = self.filtered_projects(query)
            if "status" not in query:
                rows = [p for p in rows if p["status"] != "archived"]
            offset, limit = int(query.get("offset", ["0"])[0]), int(query.get("limit", ["100"])[0])
            rows = sorted(rows, key=lambda p: str(p["created_at"]), reverse=True)[offset : offset + limit]
            route.fulfill(json=rows)
        else:
            self.fixture.route_request(route)

    def filtered_projects(self, query):
        selected = {key: query[key][0] for key in ("category", "status") if key in query}
        return [p for p in self.projects if all(p[key] == value for key, value in selected.items())]

    def open_page(self, locale, width):
        self.projects = [dict(p) for p in cards.PROJECTS]
        for index, status in enumerate(s for s in STATUSES if s not in ("deployed", "building")):
            fixture = dict(cards.PROJECTS[0], status=status)
            fixture["id"] = f"00000000-0000-4000-8000-{index + 201:012d}"
            self.projects.append(fixture)
        self.projects[-1].update(description="", repo_url=None, deploy_url=None)
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            device_scale_factor=1,
            locale=locale,
            service_workers="block",
        )
        self.addCleanup(context.close)
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.mode, self.unexpected, self.requested = "populated", [], []
        self.votes, self.pending_votes = [], []

        class CardFixture(cards.ProjectCardTests):
            options: Namespace

        self.fixture = CardFixture()
        self.fixture.options = self.options
        for name in ("mode", "unexpected", "requested", "votes", "pending_votes"):
            setattr(self.fixture, name, getattr(self, name))
        context.route("**/*", self.route_request)
        page = context.new_page()
        page.set_default_timeout(7000)
        self.assertEqual(getattr(page.goto(self.options.base_url + "/projects"), "status", None), 200)
        expect(page.locator("main article").first).to_be_visible()
        expect(page.locator("html")).to_have_attribute("lang", locale)
        page.add_script_tag(content=paint.SCRIPT)
        return page

    def controls(self, page):
        page.evaluate("""() => {
            const el = document.createElement('article'); el.id='contrast-controls';
            el.style='position:fixed;left:20px;top:100px;width:260px;padding:10px;background:black;color:white;z-index:9999';
            el.innerHTML=`<span style="display:block;color:lab(100% 0 0)">Known white</span>
                <span style="color:rgba(255,255,255,.5);display:block">Known alpha</span>
                <span style="color:#111;display:block">Known failure</span>`;
            document.body.append(el);
        }""")
        root = page.locator("#contrast-controls")
        try:
            self.assertEqual(
                page.evaluate("cardContrast.rasterControls()"),
                {"allowed": 0, "rejected": [True] * 6, "outside": True},
            )
            nodes = self.paint.capture(page, root, "calibration:solid-alpha")
            self.assertEqual([n["status"] for n in nodes], ["pass", "pass", "fail"])
            self.assertEqual(nodes[0]["background"], [0, 0, 0])
            self.assertAlmostEqual(nodes[0]["nominal"], 21, places=8)
            expected_alpha = ((0.5 + 0.055) / 1.055) ** 2.4
            self.assertEqual(nodes[1]["rgba"][3], 0.5)
            self.assertAlmostEqual(nodes[1]["nominal"], (expected_alpha + 0.05) / 0.05, places=8)
            self.assertEqual(
                root.evaluate("""root=>{
                const owner=document.createElement('span');
                owner.style='position:absolute;left:10px;top:10px;width:.40625px;height:1px;z-index:1';
                const cover=owner.cloneNode();cover.style.width='1px';cover.style.zIndex=2;root.append(owner);
                try {const box=cardContrast.box(owner.getBoundingClientRect()),point=box.slice(0,2).map(Math.floor);
                const fractional=cardContrast.visibleGlyph(owner,point,[box]);
                const centerOutside=!owner.contains(document.elementFromPoint(point[0]+.5,point[1]+.5));
                root.append(cover);return {fractional,centerOutside,
                    foreignRejected:!cardContrast.visibleGlyph(owner,point,[box])};
                } finally {owner.remove();cover.remove();}
            }"""),
                {"fractional": True, "centerOutside": True, "foreignRejected": True},
            )
            self.clipping_controls(page, root)
            omitted = next(p["id"] for p in self.projects if p["status"] == "submitted")
            with self.assertRaises(AssertionError):
                self.assert_matrix_coverage(
                    [state for state in self.required_states() if not state.startswith(f"{omitted}:")]
                )
        finally:
            root.evaluate("el => el.remove()")

    def clipping_controls(self, page, root):
        root.evaluate(
            """el => el.innerHTML = `<span style="display:block;color:#ddd;
            background:linear-gradient(90deg,#000 40%,#eee 40%)">Glyphs WWWWWWWWW</span>
            <div style="width:43px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">
            <span>Clipped partial glyphs WWWWW</span>
            <span style="position:relative;left:100px">Hidden</span></div>
            <div style="width:50px;height:18px;overflow:hidden;display:-webkit-box;
            -webkit-line-clamp:1;-webkit-box-orient:vertical">Clamp words across lines</div>`"""
        )
        clipped = self.paint.capture(page, root, "calibration:gradient-clip")
        self.assertEqual(clipped[0]["status"], "fail")
        self.assertEqual(clipped[1]["status"], "pass")
        self.assertEqual(clipped[2]["status"], "excluded")
        self.assertGreater(clipped[1]["glyphs"], 0)
        self.assertGreater(clipped[3]["excluded"], 0)
        root.evaluate(
            """(el, text) => {
            const p = document.createElement('p'); p.textContent = text;
            p.style = 'width:230px;font:12px/19.5px monospace;overflow:hidden;'
                + 'display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical';
            el.append(p);
        }""",
            cards.PROJECTS[0]["description"],
        )
        marker = self.paint.capture(page, root, "calibration:generated-ellipsis")[-1]
        self.assertTrue(marker["generated"])
        self.assertEqual(marker["status"], "pass")
        root.locator("span").first.evaluate("el => el.style.textShadow='1px 1px white'")
        self.assertEqual(self.paint.capture(page, root, "calibration:unsupported")[0]["status"], "unknown")

    def position_card(self, page, card):
        self.paint.settle(page)
        card.evaluate("el => el.scrollIntoView({block:'center', behavior:'instant'})")
        bounds = card.bounding_box()
        header = page.locator("header").evaluate("el => el.getBoundingClientRect().bottom")
        self.assertGreaterEqual(bounds["y"], header)
        self.assertLessEqual(bounds["y"] + bounds["height"], page.viewport_size["height"])

    def native_focus(self, page, control):
        for _ in range(80):
            page.keyboard.press("Tab")
            if control.evaluate("el => el === document.activeElement"):
                break
        self.assertTrue(control.evaluate("el => el === document.activeElement && el.matches(':focus-visible')"))

    def test_focused_preflight(self):
        page = self.open_page("en", 1280)
        self.paint = paint.ProjectCardPaint(self)
        try:
            self.controls(page)
            card = page.locator("main article").first
            for kind, selector in (("title", "h3 a"), ("upvote", "button")):
                page.mouse.move(0, 0)
                self.native_focus(page, card.locator(selector).first)
                self.position_card(page, card)
                nodes = self.paint.capture(page, card, "preflight:focus-" + kind)
                self.assertEqual(
                    [
                        (n["text"], n["status"], n["reasons"])
                        for n in nodes
                        if n["status"] not in ("pass", "excluded", "disabled")
                    ],
                    [],
                )
            self.mutation(page, card)
            self.assertEqual(self.unexpected, [])
        finally:
            self.paint.save_metrics("preflight-en", 1280)
            page.context.close()

    def expected_controls(self, project):
        return {
            key: name
            for key, name in (
                ("/projects/" + project["id"], "title"),
                ("↑", "upvote"),
                ("↓", "downvote"),
                (project["repo_url"], "repository"),
                (project["deploy_url"] if project["status"] != "archived" else None, "demo"),
            )
            if key
        }

    def required_states(self):
        self.assertEqual({p["status"] for p in self.projects}, set(STATUSES))
        self.assertEqual(len({p["id"] for p in self.projects}), len(STATUSES))
        required = set()
        for project in self.projects:
            states = ["default", "hover-card"] + [
                f"{mode}-{control}"
                for mode in ("hover", "focus")
                for control in self.expected_controls(project).values()
            ]
            required.update(f"{project['id']}:{state}" for state in states)
        return required

    def assert_matrix_coverage(self, observed):
        expected = self.required_states()
        self.assertEqual(set(observed), expected, "Missing or unexpected matrix identity")
        self.assertEqual(len(observed), len(expected), "Duplicate matrix identity")

    def audit_card(self, page, card, prefix):
        self.position_card(page, card)
        page.mouse.move(0, 0)
        page.evaluate("document.activeElement?.blur()")
        self.paint.capture(page, card, prefix + ":default")
        card.hover(position={"x": 3, "y": 3})
        self.paint.capture(page, card, prefix + ":hover-card")
        project = next(p for p in self.projects if p["id"] == prefix)
        expected = self.expected_controls(project)
        for control in card.locator("h3 a, button, a[target]").all():
            identity = control.get_attribute("href") or control.inner_text().strip()[0]
            self.assertIn(identity, expected)
            kind = expected[identity]
            control.hover()
            self.paint.capture(page, card, prefix + ":hover-" + kind)
            page.mouse.move(0, 0)
            self.native_focus(page, control)
            self.position_card(page, card)
            self.paint.capture(page, card, prefix + ":focus-" + kind)

    def mutation(self, page, card):
        self.position_card(page, card)
        page.mouse.move(0, 0)
        label = card.locator("p").first
        original = self.paint.capture(page, card, "mutation:before")
        old = label.get_attribute("style")
        try:
            label.evaluate("el => el.style.color='rgb(16,16,16)'")
            nodes = self.paint.capture(page, card, "mutation:low")
            self.assertTrue(any(n["status"] == "fail" and n["text"] == label.text_content() for n in nodes))
        finally:
            label.evaluate(
                "(el, old) => old === null ? el.removeAttribute('style') : el.setAttribute('style', old)",
                old,
            )
        restored = self.paint.capture(page, card, "mutation:restored")
        self.assertEqual(original, restored)

    def disabled_vote(self, page):
        card = page.locator(f'main article:has(a[href="/projects/{cards.PROJECTS[0]["id"]}"])')
        button = card.get_by_role("button").first
        button.click()
        expect(button).to_be_disabled()
        nodes = self.paint.capture(page, card, "disabled:held")
        self.assertTrue(any(n["status"] == "disabled" for n in nodes))
        self.assertEqual(len(self.votes), 1)
        self.pending_votes.pop().fulfill(status=503, json=cards.SERVER_ERROR)
        expect(button).to_be_enabled()
        self.paint.capture(page, card, "enabled:restored")

    def assert_product_results(self):
        product = [
            n
            for record in self.paint.results
            if record["state"].split(":")[0] not in ("calibration", "mutation")
            for n in record["nodes"]
        ]
        self.assertEqual(
            [
                (n["text"], n["status"], n["reasons"])
                for n in product
                if n["status"] not in ("pass", "excluded", "disabled")
            ],
            [],
        )

    def test_calibrated_card_contrast_in_six_layouts(self):
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    page = self.open_page(locale, width)
                    self.paint = paint.ProjectCardPaint(self)
                    try:
                        self.controls(page)
                        for card in page.locator("main article").all():
                            project_id = card.locator("h3 a").get_attribute("href").split("/")[-1]
                            self.audit_card(page, card, project_id)
                        self.mutation(page, page.locator("main article").first)
                        self.disabled_vote(page)
                        status_group = page.get_by_role(
                            "group",
                            name="Project status" if locale == "en" else "Статус проекта",
                            exact=True,
                        )
                        status_group.get_by_role(
                            "button", name="archived" if locale == "en" else "В архиве", exact=True
                        ).click()
                        expect(page.locator("main article")).to_have_count(1)
                        archived = next(p["id"] for p in self.projects if p["status"] == "archived")
                        self.audit_card(page, page.locator("main article"), archived)
                        expect(page.locator("main article a[href*=live-demo]")).to_have_count(0)
                        self.assertIn("status=archived", self.requested[-1])
                        self.assert_product_results()
                        self.assert_matrix_coverage(
                            [
                                record["state"]
                                for record in self.paint.results
                                if record["state"].split(":")[0]
                                not in ("calibration", "mutation", "disabled", "enabled")
                            ]
                        )
                        self.assertEqual(self.unexpected, [])
                    finally:
                        self.paint.save_metrics(locale, width)
                        page.context.close()


if __name__ == "__main__":
    ProjectCardContrastTests.options, unittest_args = cards.detail.browser_options.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
