"""Real browser questionnaire, native PDF and outward privacy acceptance."""

import functools
import http.server
import itertools
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.parse import unquote, urlsplit

import fitz
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("contacts", "home", "documents", "accounts", "care")
VALUES = {key: (f"D3-PRIVATE-{key}", f"D3-NOTE-{key}") for key in SECTIONS}
PUBLIC = {"/": "index.html", "/index.html": "index.html", "/src/app.js": "src/app.js",
          "/src/styles.css": "src/styles.css", "/content/en.json": "content/en.json",
          "/content/ru.json": "content/ru.json", "/content/countries.json": "content/countries.json"}
GITHUB = "https://github.com/AgentSpore/agentspore/issues/new"


class PublicFiles(http.server.SimpleHTTPRequestHandler):
    """Serve an explicit asset list, never a directory listing or arbitrary file."""

    def send_head(self):
        """Reject paths and symlinks before the standard static-file handler."""
        name = PUBLIC.get(urlsplit(self.path).path)
        if name is None:
            self.send_error(404)
            return None
        path = ROOT / name
        if not path.is_file() or any(part.is_symlink() for part in (path, *path.parents)):
            self.send_error(404)
            return None
        return super().send_head()

    def log_message(self, *_args):
        """Suppress request logs, including possible privacy-regression input."""


class QuestionnaireBrowser(unittest.TestCase):
    """Own native browser resources; no test methods to duplicate in subclasses."""

    @classmethod
    def setUpClass(cls):
        """Reuse the D1 fixture lifecycle with a strictly bounded public server."""
        cls.output = Path(sys.argv[1])
        cls.output.mkdir(parents=True, exist_ok=True)
        handler = functools.partial(PublicFiles, directory=str(ROOT))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.addClassCleanup(cls.server.server_close)
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join, 2)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/"
        cls.runtime = sync_playwright().start()
        cls.addClassCleanup(cls.runtime.stop)
        cls.browser = cls.runtime.chromium.launch(headless=True, executable_path=sys.argv[2], args=["--accept-lang=en-US"])
        cls.addClassCleanup(cls.browser.close)

    def open_page(self, context, metric_status=204):
        """Mock only outward metrics/GitHub and keep request diagnostics in RAM."""
        observed = {"events": [], "traffic": [], "console": [], "github": []}
        def receive(route):
            request = route.request
            observed["traffic"].append([request.url, request.headers, request.post_data])
            if request.url == self.base + "_agentspore/events":
                observed["events"].append(request.post_data_json)
                if metric_status == "abort":
                    route.abort("failed")
                else:
                    route.fulfill(status=metric_status)
            elif request.url.startswith(self.base):
                route.continue_()
            elif request.url.split("?", 1)[0] == GITHUB:
                observed["github"].append(request.url)
                route.fulfill(status=200, content_type="text/html", body="<title>GitHub boundary</title>")
            else:
                route.abort("blockedbyclient")
        context.route("**/*", receive)
        page = context.new_page()
        page.on("console", lambda message: observed["console"].append(message.text))
        page.set_default_timeout(2000)
        page.goto(self.base, wait_until="networkidle")
        expect(page.locator("#language")).to_be_enabled()
        return page, observed

    def select_section(self, page, section):
        """Navigate through the same buttons used by the person filling the form."""
        page.locator(f'#section-nav [data-section="{section}"]').click()
        expect(page.locator("#section-title")).to_be_visible()

    def fill_sections(self, page, values):
        """Fill real editable controls, not app state or a mirrored data model."""
        for section, (primary, notes) in values.items():
            self.select_section(page, section)
            page.locator("#section-primary").fill(primary)
            page.locator("#section-notes").fill(notes)

    def assert_sections(self, page, values):
        """Read all ten values back after transitions."""
        for section, (primary, notes) in values.items():
            self.select_section(page, section)
            expect(page.locator("#section-primary")).to_have_value(primary)
            expect(page.locator("#section-notes")).to_have_value(notes)


class QuestionnaireTest(QuestionnaireBrowser):
    """Exercise state retention, actual PDF output and privacy through browser UI."""

    def test_country_language_and_care_preserve_all_fields_until_clear_or_reload(self):
        """Settings must not drop hidden sections or mix selected/displayed language."""
        for country, language in itertools.product(("US", "GB"), ("en", "ru")):
            with self.subTest(country=country, language=language), self.browser.new_context() as context:
                page, _ = self.open_page(context)
                self.fill_sections(page, VALUES)
                page.locator("#country").select_option(country)
                page.locator("#language").select_option(language)
                expect(page.locator("html")).to_have_attribute("lang", language)
                page.locator("#mode").select_option("care")
                for section in ("documents", "accounts"):
                    expect(page.locator(f'#section-nav [data-section="{section}"]')).to_be_hidden()
                page.locator("#mode").select_option("family")
                self.assert_sections(page, VALUES)
                for width in (390, 1280):
                    page.set_viewport_size({"width": width, "height": 900})
                    self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                    page.screenshot(path=str(self.output / f"{country}-{language}-{width}.png"), full_page=True)
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator("#clear-folder").click()
                self.assert_sections(page, {key: ("", "") for key in SECTIONS})
                page.locator("#section-primary").fill("D3-RELOAD-CLEAR")
                page.reload(wait_until="networkidle")
                self.assert_sections(page, {key: ("", "") for key in SECTIONS})

    def test_native_pdf_matches_family_and_care_without_controls_or_script_execution(self):
        """Real Chromium PDF must keep long text, Cyrillic and mode-specific sections."""
        values = dict(VALUES)
        values["contacts"] = ("D3-PRIVATE-contacts " + "longtext " * 80 + "D3-END",
                              '<img src=x onerror="window.__d3Injected=1"> D3-XSS-LITERAL Проверка')
        for country, language in itertools.product(("US", "GB"), ("en", "ru")):
            with self.subTest(country=country, language=language), self.browser.new_context() as context:
                page, _ = self.open_page(context)
                page.locator("#country").select_option(country)
                page.locator("#language").select_option(language)
                expect(page.locator("html")).to_have_attribute("lang", language)
                self.fill_sections(page, values)
                self.assertFalse(page.evaluate("Boolean(window.__d3Injected)"))
                for mode in ("family", "care"):
                    page.locator("#mode").select_option(mode)
                    page.locator("#print-folder").click()
                    page.emulate_media(media="print")
                    path = self.output / f"{country}-{language}-{mode}.pdf"
                    pdf_bytes = page.pdf(path=str(path), format="A4", print_background=True)
                    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
                        text = "".join(sheet.get_text() for sheet in document)
                        for section in SECTIONS:
                            included = mode == "family" or section in ("contacts", "home", "care")
                            self.assertEqual(f"D3-PRIVATE-{section}" in text, included)
                        for token in ("D3-END", "D3-XSS-LITERAL", "<img", "Проверка"):
                            self.assertIn(token, text)
                        for sheet in document:
                            corner = fitz.Rect(sheet.rect.width - 3, sheet.rect.height - 3,
                                               sheet.rect.width - 1, sheet.rect.height - 1)
                            samples = sheet.get_pixmap(clip=corner, alpha=False).samples
                            self.assertTrue(samples and all(value == 255 for value in samples))
                    expect(page.locator("#print-preview")).to_be_visible()
                    for selector in ("#language", "#section-primary", "#section-nav", "#print-folder"):
                        expect(page.locator(selector)).to_be_hidden()
                    page.emulate_media(media="screen")

    def test_metrics_failures_do_not_block_form_print_or_private_country_request(self):
        """Only fixed metric dimensions and a deliberate generic GitHub link may leave."""
        for metric_status in (503, "abort"):
            with self.subTest(metric_status=metric_status), self.browser.new_context() as context:
                page, observed = self.open_page(context, metric_status)
                self.fill_sections(page, VALUES)
                page.locator("#mark-complete").click()
                page.locator("#print-folder").click()
                self.assertTrue(page.pdf(format="A4").startswith(b"%PDF"))
                self.assertFalse(observed["github"])
                with context.expect_page() as popup:
                    page.locator("#request-country").click()
                popup.value.wait_for_load_state("networkidle")
                self.assertEqual(len(observed["github"]), 1)
                self.assertEqual(observed["github"][0].split("?", 1)[0], GITHUB)
                self.assertTrue(observed["events"])
                events = observed["events"]
                self.assertEqual(events[0], {"event": "folder_open", "section": "overview", "lang": "en"})
                self.assertIn({"event": "section_complete", "section": "care", "lang": "en"}, events)
                self.assertIn({"event": "print_request", "section": "overview", "lang": "en"}, events)
                for event in events:
                    self.assertEqual(set(event), {"event", "section", "lang"})
                    self.assertIn(event["lang"], ("en", "ru"))
                    if event["event"] in ("section_open", "section_complete"):
                        self.assertIn(event["section"], SECTIONS)
                    else:
                        self.assertIn(event["event"], ("folder_open", "print_request", "backup_download"))
                        self.assertEqual(event["section"], "overview")
                for url, _, body in observed["traffic"]:
                    parsed = urlsplit(url)
                    if url.startswith(self.base) and parsed.path != "/_agentspore/events":
                        self.assertIn(parsed.path, PUBLIC)
                        self.assertIsNone(body)
                    elif not url.startswith(self.base):
                        self.assertEqual(url.split("?", 1)[0], GITHUB)
                outward = unquote(json.dumps(observed, ensure_ascii=False))
                self.assertFalse(any(value in outward for pair in VALUES.values() for value in pair))
                self.assertEqual(page.evaluate("location.search + location.hash"), "")
                self.assertEqual(page.evaluate("localStorage.length + sessionStorage.length"), 0)
                self.assertEqual(context.cookies(), [])
                self.assertEqual(page.evaluate("async () => (await indexedDB.databases()).length"), 0)
                self.assertEqual(page.evaluate("async () => (await navigator.serviceWorker.getRegistrations()).length"), 0)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
