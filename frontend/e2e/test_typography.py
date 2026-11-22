"""Check U3 body/utility font parity and EN/RU layouts in an explicit local build.

python test_typography.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
"""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import auth_form_fixtures as browser_fixtures
from playwright.sync_api import expect, sync_playwright

PROBES = """() => {
    const holder = document.createElement('div');
    holder.id = 'typography-probes';
    for (const [script, text] of [['latin', 'Hamburgefonts'], ['cyrillic', 'Съешьещёбулок']]) {
        for (const [kind, className] of [['body', ''], ['sans', 'font-sans'],
                                        ['mono', 'font-mono']]) {
            const span = document.createElement('span');
            span.id = `typography-${kind}-${script}`;
            span.className = className;
            span.textContent = text;
            holder.append(span, document.createElement('br'));
        }
    }
    document.body.append(holder);
}"""
ROOT = Path(__file__).resolve().parents[1]
FUNNEL_PATH = "/api/v1/funnel/visit"
PUBLIC_PAGES = ("/", "/projects", "/showcase", "/agents", "/blog", "/login")
PUBLIC_API = {
    "/api/v1/agents/stats": {
        "total_agents": 3, "active_agents": 3, "total_projects": 5, "total_code_commits": 360,
        "total_reviews": 12, "projects_deployed": 3, "total_repos_created": 5, "total_prs_merged": 8,
    },
    "/api/v1/blog/posts": {"posts": []},
    "/api/v1/projects": [],
    "/api/v1/activity": [{
        "id": "e1", "agent_id": "a1", "agent_name": "Synthetic", "action_type": "code_commit",
        "description": "Synthetic commit", "project_id": None, "metadata": {},
        "ts": "2026-11-22T08:00:00Z",
    }],
    "/api/v1/oauth/providers": {"github": False, "google": False},
}


def live_agents():
    """One agent with a fresh heartbeat so the home marquee renders."""
    return [{
        "id": "a1", "name": "Synthetic", "handle": "synthetic", "agent_type": "hosted",
        "model_provider": "x", "model_name": "model", "specialization": "programmer",
        "skills": [], "karma": 12, "projects_created": 1, "code_commits": 3, "reviews_done": 2,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(), "is_active": True,
        "created_at": "2026-11-01T08:00:00Z", "bio": None, "fork_count": 0,
    }]


UI_AUDIT = """() => {
    const visible = el => {
        const b = el.getBoundingClientRect(), s = getComputedStyle(el);
        return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const labelSize = el => Math.min(...[...el.querySelectorAll('*'), el].flatMap(node =>
        [...node.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim())
            .map(() => parseFloat(getComputedStyle(node).fontSize))));
    const labelFamilies = el => [...el.querySelectorAll('*'), el].filter(node =>
        !node.closest('[data-mono]') && [...node.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
        .map(node => getComputedStyle(node).fontFamily).join('|');
    const offenders = [];
    const selector = 'header a, header button, main a, main button, label, [role=tab], summary';
    for (const el of document.querySelectorAll(selector)) {
        const text = el.textContent.trim();
        if (!visible(el) || !text || text.length > 40 || el.closest('[data-mono]')) continue;
        if (el.querySelector('p, h1, h2, h3, h4, code') || el.closest('p')) continue;
        const family = labelFamilies(el), size = labelSize(el);
        if (/mono/i.test(family) || size < 14) {
            offenders.push({text, family: family.slice(0, 60), size, tag: el.tagName});
        }
    }
    return offenders;
}"""
GEOMETRY = """el => {
    const b = el.getBoundingClientRect();
    const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
    return {x: b.x, right: b.right, width: b.width, height: b.height,
        hit: hit === el || el.contains(hit),
        textFits: el.scrollWidth <= el.clientWidth + 1};
}"""


class TypographyTests(unittest.TestCase):
    """Catch the U3 mismatch between inherited Arial and the loaded Geist utility."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(executable_path=cls.options.executable)
        cls.addClassCleanup(cls.browser.close)
        cls.options.screenshots.mkdir(parents=True, exist_ok=True)

    def route_request(self, route):
        """Serve known public reads and fail closed for any unknown request."""
        parsed = urlparse(route.request.url)
        if parsed.netloc != urlparse(self.options.base_url).netloc:
            self.unexpected.append({"method": route.request.method, "url": route.request.url})
            route.abort()
        elif route.request.method == "GET" and parsed.path == "/api/v1/projects":
            # The public list endpoint returns an array; this scenario is explicitly empty.
            route.fulfill(json=[])
        elif route.request.method == "GET" and parsed.path == "/api/v1/oauth/providers":
            route.fulfill(json={"github": False, "google": False})
        elif not parsed.path.startswith("/api/"):
            route.continue_()
        else:
            self.http.route(route)

    def open_page(self, path, locale, width):
        """Open an isolated synthetic owner or anonymous context without live auth."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.http = browser_fixtures.AuthHTTP(self.options.base_url)
        self.unexpected = self.http.unexpected
        context.route("**/*", self.route_request)
        page = context.new_page()
        page.set_default_timeout(7000)
        response = page.goto(self.options.base_url + path)
        self.assertEqual(response.status, 200)
        expect(page.locator("main h1")).to_be_visible()
        expect(page.locator("html")).to_have_attribute("lang", locale)
        return page

    def rendered_fonts(self, page, selector):
        """Read actual rendered glyph fonts through Chromium's layout engine."""
        cdp = page.context.new_cdp_session(page)
        try:
            cdp.send("DOM.enable")
            cdp.send("CSS.enable")
            root = cdp.send("DOM.getDocument")["root"]["nodeId"]
            node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})
            self.assertNotEqual(node["nodeId"], 0, selector)
            return cdp.send("CSS.getPlatformFontsForNode", node)["fonts"]
        finally:
            cdp.detach()

    def sample_fonts(self, page):
        """Record separate Latin/Cyrillic glyphs and real page text/form styling."""
        page.evaluate(PROBES)
        page.evaluate("document.fonts.ready")
        fonts = {}
        for script in ("latin", "cyrillic"):
            for kind in ("body", "sans", "mono"):
                key = f"{kind}-{script}"
                fonts[key] = self.rendered_fonts(page, f"#typography-{key}")
        fonts["heading"] = self.rendered_fonts(page, "main h1")
        fonts["ui"] = page.locator("main h1, main p, main input, main form").evaluate_all(
            "els => els.map(el => ({tag: el.tagName, family: getComputedStyle(el).fontFamily}))"
        )
        page.locator("#typography-probes").evaluate("el => el.remove()")
        return fonts

    def assert_fonts(self, fonts):
        """Require loaded Geist glyphs for both scripts and preserve separate mono."""
        for script in ("latin", "cyrillic"):
            body = fonts[f"body-{script}"]
            sans = fonts[f"sans-{script}"]
            mono = fonts[f"mono-{script}"]
            with self.subTest(script=script, property="rendered-font"):
                self.assertTrue(body and sans and mono, fonts)
                self.assertEqual(
                    {font["postScriptName"] for font in body},
                    {font["postScriptName"] for font in sans},
                    f"Body and font-sans render different {script} fonts: {body} / {sans}",
                )
                for font in body:
                    self.assertTrue(font["isCustomFont"], font)
                    self.assertGreater(font["glyphCount"], 0)
                    self.assertIn("Geist", font["familyName"])
                    self.assertNotIn("Mono", font["familyName"])
                self.assertTrue(all("Mono" in font["familyName"] for font in mono), mono)
        self.assertTrue(fonts["heading"], fonts)
        self.assertTrue(all("Geist" in font["familyName"] for font in fonts["heading"]))

    def assert_geometry(self, page, tag):
        """Check each visible main action's bounds and hit area after font layout."""
        controls = page.locator("main button, main a, main input, main select")
        records = []
        for control in controls.all():
            if not control.is_visible():
                continue
            control.scroll_into_view_if_needed()
            record = control.evaluate(GEOMETRY)
            records.append(record)
            with self.subTest(control=control.get_attribute("aria-label") or control.inner_text()):
                self.assertGreater(record["width"], 0)
                self.assertGreater(record["height"], 0)
                self.assertGreaterEqual(record["x"], -1)
                self.assertLessEqual(record["right"], page.viewport_size["width"] + 1)
                self.assertTrue(record["hit"], record)
                self.assertTrue(record["textFits"], record)
        self.assertTrue(records)
        self.assertLessEqual(
            page.evaluate("document.documentElement.scrollWidth"), page.viewport_size["width"]
        )
        (self.options.screenshots / f"{tag}-geometry.json").write_text(json.dumps(records))

    def test_body_and_sans_share_loaded_fonts_without_clipping_actions(self):
        for path in ("/login", "/docs/connect", "/projects"):
            for locale in ("en", "ru"):
                for width in (320, 390, 1280):
                    with self.subTest(path=path, locale=locale, width=width):
                        page = self.open_page(path, locale, width)
                        try:
                            page.evaluate("document.fonts.ready")
                            page.evaluate(
                                "Promise.all(document.getAnimations().filter(a => "
                                "a.effect?.getComputedTiming().iterations !== Infinity)"
                                ".map(a => a.finished.catch(() => {})))"
                            )
                            tag = f"{path.strip('/').replace('/', '-')}-{locale}-{width}"
                            fonts = self.sample_fonts(page)
                            (self.options.screenshots / f"{tag}-fonts.json").write_text(
                                json.dumps(fonts, ensure_ascii=False, indent=2)
                            )
                            page.screenshot(path=str(self.options.screenshots / f"{tag}.png"))
                            with self.subTest(property="fonts"):
                                self.assert_fonts(fonts)
                            with self.subTest(property="geometry"):
                                self.assert_geometry(page, tag)
                            self.assertEqual(self.surprises(self.unexpected), [])
                        finally:
                            page.context.close()

    @staticmethod
    def surprises(requests):
        """Everything except the visit beacon: POST to exactly /api/v1/funnel/visit."""
        return [
            r for r in requests
            if not (r["method"] == "POST" and r["url"].split("?")[0] == FUNNEL_PATH)
        ]

    def open_public(self, path, locale, width):
        """Open a public page with held synthetic reads; the visit beacon and prefetches pass."""
        origin = urlparse(self.options.base_url).netloc
        self.rejected = []

        def handle(route):
            request, target = route.request, urlparse(route.request.url)
            if target.netloc == origin and not target.path.startswith("/api/"):
                route.continue_()
            elif request.method == "POST" and target.path == FUNNEL_PATH:
                route.fulfill(status=204, headers={"access-control-allow-origin": "*"})
            elif request.method == "GET" and target.path.endswith("/leaderboard"):
                route.fulfill(json=live_agents(), headers={"access-control-allow-origin": "*"})
            elif request.method == "GET" and target.path in PUBLIC_API:
                route.fulfill(json=PUBLIC_API[target.path], headers={"access-control-allow-origin": "*"})
            else:
                self.rejected.append({"method": request.method, "url": request.url})
                route.abort()

        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.route("**/*", handle)
        page = context.new_page()
        page.set_default_timeout(8000)
        page.goto(self.options.base_url + path)
        expect(page.locator("main h1").first).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.wait_for_load_state("networkidle")
        return page

    def check_each_public_page(self, widths, check):
        for path in PUBLIC_PAGES:
            for locale in ("en", "ru"):
                for width in widths:
                    with self.subTest(path=path, locale=locale, width=width):
                        page = self.open_public(path, locale, width)
                        check(page, width)
                        self.assertEqual(self.surprises(self.rejected), [])

    def test_interactive_labels_are_sans_and_at_least_14px(self):
        self.check_each_public_page(
            (1280, 390), lambda page, width: self.assertEqual(page.evaluate(UI_AUDIT), [])
        )

    def test_identifiers_and_inputs_keep_their_mono_face_and_size(self):
        probes = (
            ("input", "font-mono text-sm", "14px"),
            ("input", "font-mono text-xs", "12px"),
            ("a", "font-mono text-xs", "12px"),
            ("a data-mono", "font-mono text-xs", "12px"),
        )
        for locale in ("en", "ru"):
            page = self.open_public("/login", locale, 1280)
            for index, (tag, classes, size) in enumerate(probes):
                with self.subTest(locale=locale, tag=tag, classes=classes):
                    got = page.evaluate(
                        """([tag, classes, index]) => {
                            const [name, attr] = tag.split(' ');
                            const el = document.createElement(name); if (attr) el.setAttribute(attr, '');
                            el.className = classes; el.textContent = 'Zz0O1lI'; el.id = 'probe-' + index;
                            if (name === 'a') el.href = '/x';
                            document.querySelector('main').append(el);
                            const s = getComputedStyle(el);
                            return {family: s.fontFamily, size: s.fontSize};
                        }""",
                        [tag, classes, index],
                    )
                    self.assertRegex(got["family"], "(?i)mono")
                    self.assertEqual(got["size"], size)

    def test_filter_empty_text_is_centred_under_its_icon(self):
        for locale in ("en", "ru"):
            with self.subTest(locale=locale):
                page = self.open_public("/agents", locale, 1280)
                page.locator("main input[type=text]").fill("zzzz-no-such-agent")
                box = page.locator("main .text-center:has(> p)")
                expect(box).to_be_visible()
                centres = box.evaluate(
                    """el => {
                        const mid = n => { const b = n.getBoundingClientRect(); return b.x + b.width / 2; };
                        return {icon: mid(el.querySelector('.text-4xl')), text: mid(el.querySelector('p'))};
                    }"""
                )
                self.assertLessEqual(abs(centres["text"] - centres["icon"]), 4, centres)

    def test_no_horizontal_overflow_at_phone_widths(self):
        self.check_each_public_page(
            (390, 320),
            lambda page, width: self.assertLessEqual(
                page.evaluate("document.documentElement.scrollWidth"), width
            ),
        )

    def test_home_has_no_axe_target_size_violations(self):
        axe = ROOT / "node_modules/axe-core/axe.min.js"
        for locale in ("en", "ru"):
            for width in (1280, 390):
                with self.subTest(locale=locale, width=width):
                    page = self.open_public("/", locale, width)
                    page.add_script_tag(path=str(axe))
                    buttons = page.locator("[data-strip] button")
                    expect(buttons).to_have_count(2)  # strips mount after the client fetches resolve
                    for index in range(2):
                        # Scrolled into view the round corners count as obscured pixels.
                        buttons.nth(index).evaluate(
                            "el => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 80)"
                        )
                        page.wait_for_timeout(500)
                        result = page.evaluate(
                            "axe.run(document, {runOnly: ['target-size']})"
                            ".then(r => r.violations.map(v => v.nodes.map(n => n.target)))"
                        )
                        self.assertEqual(result, [], json.dumps(result)[:600])


if __name__ == "__main__":
    TypographyTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
