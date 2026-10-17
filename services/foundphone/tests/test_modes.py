"""Exercise wallpaper modes, accessible controls and browser failure boundaries."""

import functools
import hashlib
import http.server
import threading
import unittest

from playwright.sync_api import sync_playwright

from test_wallpaper import BROWSER, OUT, PHONE, ROOT, QuietFiles


class ModesTest(unittest.TestCase):
    """Test mode output and the visible interface without importing application logic."""

    @classmethod
    def setUpClass(cls):
        """Own one loopback server and headless browser for this slice."""
        OUT.mkdir(parents=True, exist_ok=True)
        handler = functools.partial(QuietFiles, directory=str(ROOT))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(headless=True, executable_path=BROWSER)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        """Close only resources owned by this suite."""
        cls.browser.close()
        cls.runtime.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_modes_change_contact_labels_and_actual_png(self):
        """All modes remain usable offline and produce distinct wallpaper content."""
        hashes = set()
        with self.browser.new_context(accept_downloads=True) as context:
            page = context.new_page()
            page.set_default_timeout(1500)
            page.goto(self.base, wait_until="networkidle")
            context.set_offline(True)
            labels = {"phone": "Contact number", "child": "Guardian contact number", "pet": "Owner contact number"}
            for mode, label in labels.items():
                with self.subTest(mode=mode):
                    page.get_by_label("Wallpaper for", exact=True).select_option(mode)
                    page.get_by_label(label, exact=True).fill(PHONE)
                    with page.expect_download() as download:
                        page.get_by_role("button", name="Download PNG", exact=True).click()
                    path = OUT / f"mode-{mode}.png"
                    download.value.save_as(path)
                    raw = path.read_bytes()
                    self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
                    hashes.add(hashlib.sha256(raw).hexdigest())
            self.assertEqual(len(hashes), 3)

    def test_en_ru_mobile_desktop_keyboard_and_screenshots(self):
        """Visible labels, keyboard focus and layout work at both required widths."""
        for language, contact_label, download_label in [
            ("en", "Contact number", "Download PNG"),
            ("ru", "Контактный номер", "Скачать PNG"),
        ]:
            for width in (390, 1280):
                with self.subTest(language=language, width=width), self.browser.new_context(
                    viewport={"width": width, "height": 900}
                ) as context:
                    page = context.new_page()
                    page.set_default_timeout(1500)
                    page.goto(self.base, wait_until="networkidle")
                    page.get_by_role("combobox", name="Language", exact=True).select_option(language)
                    self.assertEqual(page.locator("html").get_attribute("lang"), language)
                    contact = page.get_by_label(contact_label, exact=True)
                    contact.fill(PHONE)
                    contact.focus()
                    page.keyboard.press("Tab")
                    self.assertTrue(page.locator("#message").evaluate("el => el === document.activeElement"))
                    self.assertNotEqual(page.locator("#message").evaluate("el => getComputedStyle(el).outlineStyle"), "none")
                    page.keyboard.press("Tab")
                    self.assertTrue(page.get_by_role("button", name=download_label, exact=True).evaluate("el => el === document.activeElement"))
                    self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                    for mode in ("child", "pet"):
                        page.locator("#mode").select_option(mode)
                        labels = {
                            "en": {"child": "Guardian contact number", "pet": "Owner contact number"},
                            "ru": {"child": "Контактный номер опекуна", "pet": "Контактный номер хозяина"},
                        }
                        self.assertTrue(page.get_by_label(labels[language][mode], exact=True).is_visible())
                        self.assertTrue(page.get_by_role("button", name=download_label, exact=True).is_enabled())
                    page.locator("#mode").select_option("phone")
                    page.screenshot(path=str(OUT / f"ui-{language}-{width}.png"), full_page=True)

    def test_invalid_numbers_and_long_unicode_disable_download(self):
        """Invalid input exposes a useful error and prevents exporting stale content."""
        with self.browser.new_context() as context:
            page = context.new_page()
            page.set_default_timeout(1500)
            page.goto(self.base, wait_until="networkidle")
            contact = page.get_by_label("Contact number", exact=True)
            button = page.get_by_role("button", name="Download PNG", exact=True)
            for invalid in ("123", "++12025550123", "+01234567890", "../phone"):
                with self.subTest(number=invalid):
                    contact.fill(invalid)
                    self.assertEqual(contact.get_attribute("aria-invalid"), "true")
                    self.assertTrue(button.is_disabled())
                    self.assertIn("country code", page.get_by_role("status").inner_text())
            contact.fill(PHONE)
            self.assertTrue(button.is_enabled())
            message = page.get_by_label("Message", exact=True)
            message.fill("e\u0301" * 121)
            self.assertEqual(message.get_attribute("aria-invalid"), "true")
            self.assertTrue(button.is_disabled())
            self.assertIn("Shorten", page.get_by_role("status").inner_text())
            message.fill("猫 مرحبا e\u0301")
            self.assertTrue(button.is_enabled())
            self.assertEqual(page.get_by_role("status").inner_text(), "")

    def test_null_png_export_has_visible_error_and_can_retry(self):
        """Inject failure only at the browser encoding boundary, then retry real export."""
        with self.browser.new_context(accept_downloads=True) as context:
            page = context.new_page()
            page.set_default_timeout(1500)
            page.goto(self.base, wait_until="networkidle")
            page.get_by_label("Contact number", exact=True).fill(PHONE)
            page.evaluate("""() => {
                const canvas = document.querySelector('#wallpaper');
                window.realToBlob = canvas.toBlob;
                canvas.toBlob = callback => callback(null);
            }""")
            page.get_by_role("button", name="Download PNG", exact=True).click()
            self.assertIn("could not be saved", page.get_by_role("status").inner_text())
            page.evaluate("() => { document.querySelector('#wallpaper').toBlob = window.realToBlob; }")
            with page.expect_download() as download:
                page.get_by_role("button", name="Download PNG", exact=True).click()
            path = OUT / "export-retry.png"
            download.value.save_as(path)
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(page.get_by_role("status").inner_text(), "")


    def test_single_grapheme_wider_than_canvas_prevents_export(self):
        """A measured oversized grapheme must be rejected rather than clipped."""
        with self.browser.new_context() as context:
            page = context.new_page()
            page.set_default_timeout(1500)
            page.goto(self.base, wait_until="networkidle")
            page.get_by_label("Contact number", exact=True).fill(PHONE)
            message = ("😀\u200d" * 60) + "😀"
            field = page.get_by_label("Message", exact=True)
            field.fill(message)
            measured = page.evaluate("""value => {
                const width = document.querySelector('#wallpaper').width;
                const ctx = document.createElement('canvas').getContext('2d');
                ctx.font = `${width * 0.041}px system-ui, sans-serif`;
                return {width, textWidth: ctx.measureText(value).width,
                    graphemes: [...new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(value)].length};
            }""", message)
            self.assertEqual(measured["graphemes"], 1)
            self.assertGreater(measured["textWidth"], measured["width"])
            self.assertEqual(field.get_attribute("aria-invalid"), "true")
            self.assertTrue(page.get_by_role("button", name="Download PNG", exact=True).is_disabled())
            self.assertIn("Shorten", page.get_by_role("status").inner_text())


if __name__ == "__main__":
    unittest.main(argv=["test_modes.py"], verbosity=2)
