"""Read-only production-homepage checks using isolated Chrome and public catalog shapes."""
import hashlib
import json
import os
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[1]


def verify_production_build(root, base_url, source_manifest=None):
    """Match the served production manifest; optionally verify public source evidence."""
    build = (root / ".next/BUILD_ID").read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", build):
        raise AssertionError("Invalid production build ID")
    with urlopen(base_url + f"/_next/static/{build}/_buildManifest.js", timeout=10) as response:
        if response.status != 200 or b"__BUILD_MANIFEST" not in response.read():
            raise AssertionError("Production manifest unavailable")
    if source_manifest is None:
        return build
    hashes = json.loads(Path(source_manifest).read_text())
    if not isinstance(hashes, dict) or not hashes:
        raise AssertionError("Invalid public source manifest")
    source_root = (root / "src").resolve()
    for name, digest in hashes.items():
        if not isinstance(name, str) or not name.startswith("src/"):
            raise AssertionError("Source evidence must stay inside src")
        path = Path(name)
        if any(part.startswith(".") for part in path.parts) or path.suffix not in (".ts", ".tsx", ".js", ".jsx", ".css", ".ico"):
            raise AssertionError("Source evidence contains an unsupported path")
        target = (root / path).resolve()
        if not target.is_relative_to(source_root) or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise AssertionError("Invalid source evidence boundary or digest")
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise AssertionError("Compiled source changed: " + name)
    return build


class HomeServicesTests(BrowserFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = verify_production_build(ROOT, cls.options.base_url,
                                            os.environ.get("AGENTSPORE_UI_SOURCE_MANIFEST"))
        with urlopen("https://agentspore.com/api/v1/projects?limit=200") as response:
            cls.catalog = json.load(response)
        with urlopen("https://agentspore.com/api/v1/agents/stats") as response:
            cls.stats = json.load(response)
        super().setUpClass()

    def open_home(self, locale="en", width=1440, fixture=None, hold=False, status=200):
        self.errors, self.pending, self.expected, self.prefetch = [], [], [], []
        self.context = self.browser.new_context(viewport={"width": width, "height": 900},
                                                service_workers="block")
        self.addCleanup(self.context.close)
        self.context.add_cookies([{"name": "agentspore_locale", "value": locale,
                                  "url": self.options.base_url}])
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self.page.on("pageerror", lambda _: self.errors.append({"kind": "pageerror"}))
        self.page.on("console", lambda msg: self.errors.append({"kind": "console", "path": urlparse(msg.location.get("url", "")).path})
                     if msg.type == "error" else None)
        self.page.on("requestfailed", self.check_failed)
        self.page.on("response", self.check_response)

        def route_request(route):
            request = route.request
            if request.method != "GET":
                self.errors.append({"kind": "blocked-write", "path": urlparse(request.url).path})
                return route.abort()
            target = urlparse(request.url)
            if target.path == "/api/v1/projects" and target.query == "limit=200":
                if hold:
                    self.pending.append(route)
                    return
                if fixture is not None:
                    return route.fulfill(status=status, json=fixture)
            route.continue_()

        self.context.route("**/*", route_request)
        self.page.goto(self.options.base_url, wait_until="domcontentloaded")
        self.section = self.page.locator('section[aria-labelledby="home-services-title"]')
        expect(self.section).to_be_visible()

    def check_failed(self, request):
        path = urlparse(request.url).path
        item = {"kind": "requestfailed", "path": path, "cause": request.failure}
        native = request.method == "GET" and urlparse(request.url).netloc == urlparse(self.options.base_url).netloc
        native = native and request.header_value("next-router-prefetch") == "1" and request.failure == "net::ERR_ABORTED"
        known = path in ("/", "/showcase", "/workspace", "/projects", "/agents", "/login", "/dashboard")
        (self.prefetch if native and known else self.errors).append(item)

    def check_response(self, response):
        if response.status < 400:
            return
        path = urlparse(response.url).path
        item = {"kind": "http", "path": path, "status": response.status}
        (self.expected if path == "/api/v1/projects" and response.status == 500
         and getattr(self, "fault", False) else self.errors).append(item)

    def assert_clean(self):
        self.assertEqual(self.errors, [], json.dumps(self.errors))
        self.assertFalse(self.page.evaluate(
            "document.documentElement.scrollWidth > innerWidth"))

    def test_01_real_catalog_locales_links_and_settled_counters(self):
        deployed = [p for p in self.catalog if p["status"] == "deployed" and p.get("deploy_url")]
        self.assertEqual(len(deployed), 3)
        snapshots = []
        for locale in ("en", "ru"):
            self.open_home(locale)
            expect(self.section.locator("article")).to_have_count(3)
            self.assertEqual(self.section.locator('a[href^="/projects/"]').count(), 3)
            for project in deployed:
                expect(self.section.locator(f'a[href="/projects/{project["id"]}"]')).to_have_count(1)
            for link in self.section.locator('a[target="_blank"]').all():
                self.assertEqual(set(link.get_attribute("rel").split()), {"noopener", "noreferrer"})
                self.assertTrue(link.get_attribute("href").startswith("https://"))
            expect(self.section.locator('a[href*="docs/user-guide.md"]')).to_have_count(1)
            expect(self.section.get_by_text("backup", exact=False).first).to_be_visible() if locale == "en" else None
            expect(self.page.locator('section[aria-labelledby="home-agent-owners"] a')).to_be_visible()
            expect(self.page.locator("header nav:visible a").first).to_have_attribute("href", "/showcase")
            self.page.wait_for_timeout(1700)
            counters = self.page.locator(".fade-in-d4 .tabular-nums").all_text_contents()
            snapshots.append({"locale": locale, "counters": counters})
            keys = ("active_agents", "total_projects", "total_code_commits", "projects_deployed", "total_reviews", "total_prs_merged")
            self.assertEqual(counters[:6], [str(self.stats[key]) for key in keys])
            self.assert_clean()
            self.doCleanups()
        (self.options.screenshots / "settled-home.json").write_text(json.dumps(snapshots, ensure_ascii=False))

    def test_02_keyboard_mobile_menu_details_and_widths(self):
        for width in (320, 390, 1440):
            self.open_home("ru", width)
            if width < 1024:
                toggle = self.page.locator('header button[aria-controls][aria-expanded]').last
                toggle.focus()
                self.page.keyboard.press("Enter")
                expect(toggle).to_have_attribute("aria-expanded", "true")
                panel = self.page.locator('[id="' + toggle.get_attribute("aria-controls") + '"]')
                expect(panel.locator("nav a").first).to_have_attribute("href", "/showcase")
                self.page.keyboard.press("Escape")
                expect(toggle).to_be_focused()
                expect(toggle).to_have_attribute("aria-expanded", "false")
            summary = self.section.locator("summary").first
            summary.focus()
            self.page.keyboard.press("Enter")
            expect(summary.locator("..")).to_have_attribute("open", "")
            self.page.keyboard.press("Tab")
            self.assertFalse(self.page.evaluate("document.activeElement === document.body"))
            self.assert_clean()
            if width in (320, 1440):
                self.section.screenshot(path=self.options.screenshots / f"services-{width}-ru.png")
            self.doCleanups()

    def test_03_empty_replaces_ssr_and_excludes_curated_ghosts(self):
        self.open_home(fixture=[])
        expect(self.section.locator("article")).to_have_count(0)
        expect(self.section.get_by_role("status")).to_contain_text("No deployed services")
        self.assert_clean()

    def test_04_malformed_response_retains_ssr_with_warning(self):
        self.open_home(fixture={"projects": []})
        expect(self.section.locator("article")).to_have_count(3)
        expect(self.section.get_by_role("status")).to_contain_text("Couldn't refresh")
        self.assert_clean()

    def test_05_explicit_500_retains_ssr(self):
        self.fault = True
        self.open_home(fixture={"detail": "Synthetic catalog outage"}, status=500)
        expect(self.section.get_by_role("status")).to_contain_text("Couldn't refresh")
        expect(self.section.locator("article")).to_have_count(3)
        self.assertEqual(len(self.expected), 1)
        self.assertEqual(self.errors, [{"kind": "console", "path": "/api/v1/projects"}])
        self.section.screenshot(path=self.options.screenshots / "services-stale.png")

    def test_06_slow_refresh_preserves_ssr_then_long_unknown_card(self):
        self.open_home(hold=True, width=320)
        expect(self.section.locator("article")).to_have_count(3)
        self.page.expose_function("__catalogHeld", lambda: len(self.pending))
        self.page.wait_for_function("async () => await window.__catalogHeld() === 1")
        project = dict(self.catalog[0], title="LongServiceName" * 28, description="LongPurpose" * 60,
                       status="deployed", repo_url=None, deploy_url="https://example.org/application")
        self.pending.pop().fulfill(json=[project])
        expect(self.section.locator("article")).to_have_count(1)
        link = self.section.locator('a[target="_blank"]')
        expect(link).to_have_attribute("href", "https://example.org/application")
        expect(link).to_have_attribute("rel", "noopener noreferrer")
        self.assert_clean()


    def test_07_scoped_axe_existing_package(self):
        axe = ROOT / "node_modules/axe-core/axe.min.js"
        if not axe.exists():
            self.skipTest("Existing axe-core package unavailable")
        self.open_home()
        self.page.wait_for_timeout(1700)
        self.page.add_script_tag(path=str(axe))
        result = self.page.evaluate("""async () => {
          const result = await axe.run({include: [['header'],
            ['section[aria-labelledby="home-services-title"]'],
            ['section[aria-labelledby="home-people"]'],
            ['section[aria-labelledby="home-agent-owners"]']]},
            {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}});
          return result.violations.map(v => ({id:v.id, impact:v.impact, count:v.nodes.length, targets:v.nodes.map(n=>n.target)}));
        }""")
        (self.options.screenshots / "axe-scoped.json").write_text(json.dumps(result))
        self.assertEqual(result, [])
        self.assert_clean()


if __name__ == "__main__":
    HomeServicesTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
