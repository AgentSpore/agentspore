"""Browser acceptance for training snapshots and a private, consent-based card."""

import functools
import http.server
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.parse import unquote
from uuid import UUID

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PHRASE = "D1 synthetic violet lighthouse"
FIELDS = {"event", "attempt_id", "curriculum_version", "lang", "answers", "completed"}
CORRECT = [True] * 8 + [False] * 2


class QuietFiles(http.server.SimpleHTTPRequestHandler):
    """Serve only the standalone project without request logging."""

    def log_message(self, *_args):
        """Suppress HTTP request logs."""


class TrainingTest(unittest.TestCase):
    """Exercise actual browser controls and substitute only the HTTP boundary."""
    @classmethod
    def setUpClass(cls):
        """Own a loopback static server and the already-installed browser."""
        handler = functools.partial(QuietFiles, directory=str(ROOT))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.addClassCleanup(cls.server.server_close)
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join, 2)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/"
        cls.runtime = sync_playwright().start()
        cls.addClassCleanup(cls.runtime.stop)
        cls.browser = cls.runtime.chromium.launch(headless=True, executable_path=sys.argv[2])
        cls.addClassCleanup(cls.browser.close)

    def open_page(self, context, status=204):
        """Record outward traffic, reject external requests and stub metric replies."""
        events, requests = [], []
        def receive(route):
            request = route.request
            requests.append(json.dumps([request.url, request.headers, request.post_data]))
            if request.url == self.base + "_agentspore/events":
                events.append(request.post_data_json)
                route.fulfill(status=status)
            elif request.url.startswith(self.base):
                route.continue_()
            else:
                route.abort("blockedbyclient")
        context.route("**/*", receive)
        context.add_init_script("window.printCalls = 0; window.print = () => { window.printCalls += 1; };")
        page = context.new_page()
        page.set_default_timeout(1500)
        page.goto(self.base, wait_until="networkidle")
        return page, events, requests

    def test_ten_rounds_feedback_snapshots_and_restart(self):
        """Rapid answers cannot advance; wrong answers count false and restart is fresh."""
        with self.browser.new_context() as context:
            page, events, _ = self.open_page(context)
            with page.expect_request("**/_agentspore/events") as sent:
                page.locator("#start-training").click()
            sent.value.response()
            manifest = json.loads((ROOT / "content/curriculum.json").read_text())
            self.assertEqual(manifest, {"version": 1, "rounds": [
                {"id": index, "correct_pause": index <= 8} for index in range(1, 11)]})
            for index in range(1, 11):
                expect(page.locator("#training")).to_have_attribute("data-round-id", str(index))
                expect(page.locator("#round-feedback")).to_be_hidden()
                with page.expect_request("**/_agentspore/events") as sent:
                    page.locator("#pause-verify").dblclick()
                sent.value.response()
                expect(page.locator("#round-feedback")).to_be_visible()
                self.assertTrue(page.locator("#round-feedback").inner_text().strip())
                expect(page.locator("#pause-verify")).to_be_disabled()
                expect(page.locator("#ordinary-request")).to_be_disabled()
                expect(page.locator("#training")).to_have_attribute("data-round-id", str(index))
                self.assertEqual(len(events), index + 1)
                page.locator("#next-round").click()
            expect(page.locator("#training-result")).to_contain_text("8/10")
            attempt = events[0]["attempt_id"]
            self.assertEqual(UUID(attempt).version, 4)
            for count, row in enumerate(events):
                self.assertEqual(set(row), FIELDS)
                self.assertEqual(row, dict(event="training_progress", attempt_id=attempt,
                    curriculum_version=1, lang="en", answers=CORRECT[:count], completed=count == 10))
                self.assertEqual([type(value) for value in row["answers"] + [row["completed"]]], [bool] * (count + 1))
            with page.expect_request("**/_agentspore/events") as sent:
                page.locator("#restart-training").click()
            sent.value.response()
            self.assertNotEqual(events[-1]["attempt_id"], attempt)
            self.assertEqual(UUID(events[-1]["attempt_id"]).version, 4)
            self.assertEqual(events[-1]["answers"], [])
            self.assertIs(events[-1]["completed"], False)
            expect(page.locator("#training")).to_have_attribute("data-round-id", "1")

    def test_phrase_privacy_print_consent_and_metric_failure(self):
        """HTTP503 cannot block training or print; private phrase never leaves RAM."""
        with self.browser.new_context() as context:
            page, events, requests = self.open_page(context, 503)
            links = [page.locator(selector).get_attribute("href") for selector in ("#share-whatsapp", "#share-telegram")]
            expect(page.locator("#include-phrase")).not_to_be_checked()
            page.locator("#family-phrase").fill(PHRASE)
            page.locator("#start-training").click()
            page.locator("#print-card").click()
            self.assertEqual(page.evaluate("window.printCalls"), 1)
            for consent in (False, True, False):
                page.emulate_media(media="screen")
                page.locator("#include-phrase").set_checked(consent)
                page.emulate_media(media="print")
                self.assertEqual(PHRASE in page.locator("body").inner_text(), consent)
                for selector in ("#family-phrase", "#include-phrase", "#print-card", "#training"):
                    expect(page.locator(selector)).to_be_hidden()
            page.emulate_media(media="screen")
            for _ in range(10):
                page.locator("#pause-verify").click()
                page.locator("#next-round").click()
            expect(page.locator("#training-result")).to_contain_text("8/10")
            self.assertEqual(links, [page.locator(selector).get_attribute("href") for selector in ("#share-whatsapp", "#share-telegram")])
            self.assertNotIn(PHRASE, unquote(json.dumps([requests, links, events])))
            self.assertNotIn(events[0]["attempt_id"], unquote(json.dumps(links)))
            self.assertEqual(page.evaluate("localStorage.length + sessionStorage.length"), 0)
            self.assertEqual(context.cookies(), [])
            self.assertEqual(page.evaluate("location.search + location.hash"), "")
            page.reload(wait_until="networkidle")
            expect(page.locator("#family-phrase")).to_have_value("")
            expect(page.locator("#include-phrase")).not_to_be_checked()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
