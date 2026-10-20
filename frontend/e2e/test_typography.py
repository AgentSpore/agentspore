"""Check U3 body/utility font parity and EN/RU layouts in an explicit local build.

python test_typography.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
"""

import json
import unittest
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
            self.unexpected.append("external request")
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
                            self.assertEqual(self.unexpected, [])
                        finally:
                            page.context.close()


if __name__ == "__main__":
    TypographyTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
