"""Real browser event privacy, native PNG export and telemetry failure boundaries."""

import json
import struct
import sys
import unittest

import test_wallpaper as wallpaper


class EventsTest(unittest.TestCase):
    """Reuse the existing owned server/browser fixture without inheriting its tests."""

    setUpClass = classmethod(wallpaper.WallpaperTest.setUpClass.__func__)
    tearDownClass = classmethod(wallpaper.WallpaperTest.tearDownClass.__func__)

    def open_page(self, context, failure=204):
        """Observe only outward telemetry and substitute its HTTP result."""
        events = []

        def receive(route):
            events.append(route.request.post_data_json)
            if failure == "abort":
                route.abort("failed")
            else:
                route.fulfill(status=failure)

        context.route("**/_agentspore/events", receive)
        context.add_init_script("""(() => {
            const native = window.fetch.bind(window); window.eventOptions = []; window.downloadTriggered = false;
            const click = HTMLAnchorElement.prototype.click;
            HTMLAnchorElement.prototype.click = function() { click.call(this); window.downloadTriggered = Boolean(this.download); };
            window.fetch = (input, options) => {
                const request = new Request(input, options);
                if (new URL(request.url).pathname === '/_agentspore/events')
                    window.eventOptions.push([request.credentials, window.downloadTriggered]);
                return native(input, options);
            };
        })();""")
        page = context.new_page()
        page.set_default_timeout(1500)
        page.goto(self.base, wait_until="networkidle")
        self.assertEqual(events, [{"event": "form_open", "model": "iphone-x", "lang": "en"}])
        return page, events

    def test_open_events_ignore_private_input_and_mode_changes(self):
        """The initial open precedes typing; only model/language changes add opens."""
        with self.browser.new_context() as context:
            page, events = self.open_page(context)
            page.locator("#contact").fill(wallpaper.PHONE)
            page.locator("#message").fill(wallpaper.MESSAGE)
            page.locator("#mode").select_option("child")
            self.assertEqual(len(events), 1)
            with page.expect_request("**/_agentspore/events") as changed:
                page.locator("#model").select_option("pixel-8")
            changed.value.response()
            with page.expect_request("**/_agentspore/events") as changed:
                page.locator("#language").select_option("ru")
            changed.value.response()
            self.assertEqual(
                events,
                [
                    {"event": "form_open", "model": model, "lang": lang}
                    for model, lang in [("iphone-x", "en"), ("pixel-8", "en"), ("pixel-8", "ru")]
                ],
            )
            self.assertEqual(page.evaluate("window.eventOptions"), [["same-origin", False]] * 3)
            self.assertNotIn(wallpaper.PHONE, json.dumps(events))
            self.assertNotIn(wallpaper.MESSAGE, json.dumps(events, ensure_ascii=False))
            self.assertEqual(page.evaluate("localStorage.length + sessionStorage.length"), 0)
            self.assertEqual(page.evaluate("location.search + location.hash"), "")
            self.assertEqual(context.cookies(), [])

    def test_download_event_snapshots_dimensions_and_language_before_blob_callback(self):
        """Delay only the actual encoder callback while the user changes selections."""
        with self.browser.new_context(accept_downloads=True) as context:
            page, events = self.open_page(context)
            page.locator("#contact").fill(wallpaper.PHONE)
            page.evaluate("""() => {
                const canvas = document.querySelector('#wallpaper');
                const encode = canvas.toBlob.bind(canvas);
                canvas.toBlob = callback => encode(blob => {
                    window.finishExport = () => callback(blob); }, 'image/png');
            }""")
            page.locator("#download").click()
            page.wait_for_function("typeof window.finishExport === 'function'")
            self.assertFalse(any(row["event"] == "png_download" for row in events))
            page.locator("#model").select_option("pixel-8")
            page.locator("#language").select_option("ru")
            with (
                page.expect_download() as saved,
                page.expect_request(
                    lambda request: request.url.endswith("/_agentspore/events") and request.post_data_json.get("event") == "png_download"
                ) as sent,
            ):
                page.evaluate("window.finishExport()")
            sent.value.response()
            target = wallpaper.OUT / "events-snapshot.png"
            saved.value.save_as(target)
            self.assertEqual(saved.value.suggested_filename, "foundphone-iphone-x.png")
            self.assertEqual(struct.unpack(">II", target.read_bytes()[16:24]), (1125, 2436))
            self.assertEqual(
                sent.value.post_data_json,
                {"event": "png_download", "model": "iphone-x", "lang": "en"},
            )
            self.assertEqual(sum(row["event"] == "png_download" for row in events), 1)
            self.assertEqual(page.evaluate("window.eventOptions.at(-1)"), ["same-origin", True])

    def test_failed_blob_emits_nothing_and_metric_failure_cannot_block_native_png(self):
        """A null encoder result is not a download; HTTP503/abort remain best effort."""
        for failure in (503, "abort"):
            with (
                self.subTest(failure=failure),
                self.browser.new_context(accept_downloads=True) as context,
            ):
                page, events = self.open_page(context, failure)
                page.locator("#contact").fill(wallpaper.PHONE)
                page.evaluate("""() => {
                    const canvas = document.querySelector('#wallpaper');
                    window.realExport = canvas.toBlob; canvas.toBlob = cb => cb(null);
                }""")
                page.locator("#download").click()
                self.assertIn("could not be saved", page.locator("#error").inner_text())
                self.assertEqual(len(events), 1)
                page.evaluate("() => { document.querySelector('#wallpaper').toBlob = window.realExport; }")
                with (
                    page.expect_download() as saved,
                    page.expect_request("**/_agentspore/events") as sent,
                ):
                    page.locator("#download").click()
                sent.value.response()
                target = wallpaper.OUT / f"events-failure-{failure}.png"
                saved.value.save_as(target)
                self.assertEqual(target.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(events[-1], {"event": "png_download", "model": "iphone-x", "lang": "en"})
                self.assertEqual(page.locator("#error").inner_text(), "")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
