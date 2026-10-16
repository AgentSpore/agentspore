"""Exercise the downloaded PNG and browser privacy boundary with synthetic data."""

import base64
import functools
import http.server
import json
import struct
import sys
import threading
import unittest
from pathlib import Path
from urllib.parse import quote, unquote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1])
BROWSER = sys.argv[2]
PHONE = "+12025550123"
MESSAGE = "D2-SYNTHETIC-Жёлтый 猫 e\u0301 مرحبا"
PRESETS = {"iphone-x": (1125, 2436), "iphone-xr": (828, 1792), "pixel-8": (1080, 2400)}


class QuietFiles(http.server.SimpleHTTPRequestHandler):
    """Serve only this service directory without request logging."""

    def log_message(self, format, *args):
        """Suppress HTTP access logs, which are unnecessary for this test."""


class WallpaperTest(unittest.TestCase):
    """Verify PNG output independently of rendering implementation details."""

    @classmethod
    def setUpClass(cls):
        """Own an ephemeral loopback server and one headless browser."""
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
        """Release only resources created by this suite."""
        cls.browser.close()
        cls.runtime.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_three_downloads_are_private_offline_pngs(self):
        """Check actual PNG bytes, decoded pixels and every outgoing request."""
        for preset, dimensions in PRESETS.items():
            with self.subTest(preset=preset), self.browser.new_context(accept_downloads=True) as context:
                page = context.new_page()
                page.set_default_timeout(1500)
                outgoing = []
                page.on("request", lambda request: outgoing.append(json.dumps({
                    "url": request.url, "headers": request.all_headers(),
                    "body": request.post_data or "",
                }, ensure_ascii=False)))
                page.goto(self.base, wait_until="networkidle")
                page.get_by_label("Phone model", exact=True).select_option(preset)
                page.get_by_label("Contact number", exact=True).fill(PHONE)
                page.get_by_label("Message", exact=True).fill(MESSAGE)
                page.add_script_tag(path=str(ROOT / "vendor/jsQR.js"))
                context.set_offline(True)
                with page.expect_download() as download:
                    page.get_by_role("button", name="Download PNG", exact=True).click()
                target = OUT / f"{preset}.png"
                download.value.save_as(target)
                raw = target.read_bytes()
                self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", raw[16:24]), dimensions)
                payload = page.evaluate("""async encoded => {
                    const image = new Image();
                    image.src = 'data:image/png;base64,' + encoded;
                    await image.decode();
                    const canvas = document.createElement('canvas');
                    canvas.width = image.width; canvas.height = image.height;
                    const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
                    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
                    return jsQR(pixels.data, pixels.width, pixels.height)?.data;
                }""", base64.b64encode(raw).decode())
                self.assertEqual(payload, "tel:" + PHONE)
                for request in outgoing:
                    for private in (PHONE, PHONE[1:], MESSAGE, quote(MESSAGE)):
                        self.assertNotIn(private, unquote(request))
                self.assertEqual(page.evaluate("location.search + location.hash"), "")
                self.assertEqual(page.evaluate("localStorage.length"), 0)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
