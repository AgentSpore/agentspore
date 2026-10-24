"""Exercise only an isolated loopback Otkrytka fixture; never export its secrets."""
import argparse
import base64
import ipaddress
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from playwright import sync_api as pw

TITLE = "Synthetic browser fixture 171"
WISH = "Synthetic guest wish for browser fixture 171"
ASSET_HOSTS = {"cdn.tailwindcss.com", "fonts.googleapis.com", "fonts.gstatic.com"}


def loopback_origin(value: str) -> str:
    """Reject external origins and URL components that could carry credentials."""
    try:
        target = urlsplit(value)
        hostname = target.hostname or ""
        local = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
        valid = target.scheme in {"http", "https"} and local and target.port != 0
        valid = valid and target.username is None and target.password is None
        valid = valid and target.path in {"", "/"} and not target.query and not target.fragment
        valid = valid and not any(character.isspace() for character in value)
        valid = valid and "?" not in value and "#" not in value
    except ValueError:
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError(
            "Use a loopback HTTP(S) origin without credentials or extra URL components")
    authority = "[" + hostname + "]" if ":" in hostname else hostname
    if target.port is not None and target.port != {"http": 80, "https": 443}[target.scheme]:
        authority += ":" + str(target.port)
    return target.scheme + "://" + authority


class OtkrytkaBrowser:
    """Keep fixture credentials in live contexts and gate every artifact after setup."""

    def __init__(self, base_url: str, output: Path):
        self.base_url, self.output = base_url, output
        self.errors: Counter[str] = Counter()
        self.events: Counter[str] = Counter()
        self.requests: Counter[str] = Counter()
        self.checks: dict = {}
        self.contexts = []
        self.socket_trace: list[dict] = []

    def route(self, route: pw.Route) -> None:
        target, request = urlsplit(route.request.url), route.request
        local = (target.scheme == urlsplit(self.base_url).scheme
                 and target.netloc == urlsplit(self.base_url).netloc)
        asset = (request.method == "GET" and target.scheme == "https"
                 and target.hostname in ASSET_HOSTS)
        if local or asset:
            route.continue_()
        else:
            self.errors["blocked_request"] += 1
            route.abort()

    def response(self, actor: str, response: pw.Response) -> None:
        request, path = response.request, urlsplit(response.url).path
        if response.status >= 400:
            self.errors["http"] += 1
        if request.resource_type == "document" and request.method == "GET":
            self.requests[actor + ":documents"] += 1
            if actor == "bot":
                agent = request.header_value("user-agent") or ""
                self.checks["default_headless_user_agent"] = "headless" in agent.lower()
        if (actor == "organizer" and request.method == "GET"
                and path.startswith("/api/v1/boards/")):
            header = request.header_value("x-organizer-token")
            self.requests["organizer_header_reads"] += int(bool(header))
        if path.startswith("/uploads/") and response.status == 200:
            self.requests["uploaded_image_reads"] += 1

    def frame(self, actor: str, record: dict, payload: str | bytes) -> None:
        try:
            message = json.loads(payload)
        except (ValueError, UnicodeError):
            self.errors["websocket_payload"] += 1
            return
        if message in ({"type": "hello"}, {"type": "changed"}):
            record["hello"] = record["hello"] or message["type"] == "hello"
            self.events[actor + ":" + message["type"]] += 1
        else:
            self.errors["websocket_payload"] += 1

    def websocket(self, actor: str, socket: pw.WebSocket) -> None:
        if urlsplit(socket.url).netloc != urlsplit(self.base_url).netloc:
            self.errors["external_websocket"] += 1
        self.events[actor + ":opened"] += 1
        record = {"actor": actor, "generation": self.events[actor + ":opened"], "hello": False,
                  "created_phase": self.checks.get("stage", "setup"), "closed": False}
        self.socket_trace.append(record)
        socket.on("framereceived", partial(self.frame, actor, record))
        socket.on("socketerror", partial(self.socket_error, record))
        socket.on("close", lambda _socket=None: record.update(closed=True,
                  closed_phase=self.checks.get("stage", "setup")))

    def socket_error(self, record: dict, message: str) -> None:
        categories = {"WebSocket is closed before the connection is established.": "cancelled",
                      "net::ERR_ABORTED": "cancelled"}
        record.update(error_category=categories.get(message, "transport_failure"),
                      error_phase=self.checks.get("stage", "setup"))
        self.errors["websocket"] += 1

    def annotate_navigation_errors(self) -> None:
        for record in self.socket_trace:
            actor = record["actor"]
            navigation = self.checks.get("navigation_" + actor, {})
            before = navigation.get("before_generation", 0)
            recovered = navigation.get("before_connected") and navigation.get("after_connected")
            recovered = recovered and navigation.get("before_hello", 0) > 0
            recovered = recovered and navigation.get("after_hello", 0) > navigation["before_hello"]
            expected = recovered and record.get("error_category") == "cancelled"
            expected = expected and record.get("error_phase") in {"reload_"+actor, "capture_"+actor}
            expected = expected and record["generation"] <= before
            expected = expected and navigation.get("after_generation", 0) > before
            record["navigation_cancellation_candidate"] = bool(expected)

    def page(self, browser: pw.Browser, actor: str, options: dict) -> pw.Page:
        context = browser.new_context(service_workers="block", **options)
        self.contexts.append((actor, context))
        context.route("**/*", self.route)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on("pageerror", lambda _: self.errors.update(["pageerror"]))
        page.on("console", lambda message:
                self.errors.update(["console"]) if message.type == "error" else None)
        page.on("requestfailed", lambda _: self.errors.update(["requestfailed"]))
        page.on("response", partial(self.response, actor))
        page.on("websocket", partial(self.websocket, actor))
        return page

    def wait_frame(self, page: pw.Page, key: str, before: int, timeout: int = 15000) -> None:
        """Poll Python frame counters while allowing Playwright to dispatch events."""
        deadline = monotonic() + timeout / 1000
        while self.events[key] <= before:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise pw.TimeoutError("websocket_frame_deadline")
            page.wait_for_timeout(min(50, remaining * 1000))

    def create_board(self, page: pw.Page) -> str:
        self.checks["stage"] = "create_board"
        response = page.goto(self.base_url, wait_until="domcontentloaded")
        if response is None or response.status != 200:
            raise AssertionError("initial_document_status")
        page.locator("#create-title").fill(TITLE)
        reveal = datetime.now(UTC) + timedelta(days=1)
        page.locator("#create-reveal").fill(reveal.strftime("%Y-%m-%dT%H:%M"))
        with page.expect_response(lambda result: result.request.method == "POST"
                                  and urlsplit(result.url).path == "/api/v1/boards") as created:
            page.locator("#create-btn").click()
        self.checks["create_status"] = created.value.status
        if created.value.status != 200:
            raise AssertionError("source_pinned_create_status")
        shared = urlsplit(page.locator('[data-field="share"]').input_value())
        if shared.netloc != urlsplit(self.base_url).netloc or not shared.path.startswith("/c/"):
            raise AssertionError("guest_link_origin")
        page.locator('[data-f="ack"]').check()
        page.locator('[data-action="go"]').click()
        pw.expect(page.locator('[data-field="manage"]')).to_have_count(0)
        pw.expect(page.locator("#add-wish-btn")).to_be_visible()
        self.checks["private_modal_closed"] = True
        return shared.path

    def attach_image(self, page: pw.Page) -> None:
        image = page.evaluate("""() => {
          const canvas = document.createElement('canvas'); canvas.width = canvas.height = 64;
          const paint = canvas.getContext('2d'); paint.fillStyle = 'rgb(200,120,90)';
          paint.fillRect(0,0,64,64); return canvas.toDataURL('image/jpeg');
        }""")
        page.locator('[data-f="file"]').set_input_files({"name": "synthetic.jpg",
            "mimeType": "image/jpeg", "buffer": base64.b64decode(image.split(",", 1)[1])})
        pw.expect(page.locator('[data-f="preview"] img')).to_be_visible()

    def add_wish(self, guest: pw.Page, guest_path: str) -> None:
        self.checks["stage"] = "guest_wish"
        guest.goto(self.base_url + guest_path, wait_until="domcontentloaded")
        guest.locator("#add-wish-btn").click()
        guest.locator('[data-f="name"]').fill("Synthetic guest")
        guest.locator('[data-f="text"]').fill(WISH)
        self.attach_image(guest)
        with guest.expect_response(lambda result: result.request.method == "POST"
                                   and urlsplit(result.url).path.endswith("/cards")) as added, \
             guest.expect_response(lambda result: result.request.method == "POST"
                                   and urlsplit(result.url).path.endswith("/upload")) as uploaded:
            guest.locator('[data-f="send"]').click()
        self.checks["wish_status"] = added.value.status
        self.checks["upload_status"] = uploaded.value.status
        if added.value.status != 200 or uploaded.value.status != 200:
            raise AssertionError("source_pinned_wish_status")
        pw.expect(guest.get_by_text(WISH, exact=True)).to_have_count(0)

    def prove_realtime(self, organizer: pw.Page, guest: pw.Page, guest_path: str) -> None:
        self.wait_frame(organizer, "organizer:hello", 0)
        organizer.wait_for_function(
            "typeof ws !== 'undefined' && ws.socket?.readyState === WebSocket.OPEN")
        documents = self.requests["organizer:documents"]
        self.add_wish(guest, guest_path)
        self.wait_frame(organizer, "organizer:changed", 0)
        pw.expect(organizer.get_by_text(WISH, exact=True)).to_be_visible()
        organizer.wait_for_function(
            "document.querySelector('img[src^=\"/uploads/\"]')?.naturalWidth > 0")
        if (self.requests["organizer:documents"] != documents
                or not self.requests["organizer_header_reads"]):
            raise AssertionError("realtime_or_organizer_header")
        self.checks["wish_visible_without_reload"] = True
        self.checks["organizer_previews_unrevealed_wish"] = True

    def reload_and_capture(self, organizer: pw.Page, guest: pw.Page) -> None:
        connected = "typeof ws !== 'undefined' && ws.socket?.readyState === WebSocket.OPEN"
        for actor, page, width in (("organizer", organizer, 1440), ("guest", guest, 390)):
            self.checks["stage"] = "reload_" + actor
            navigation = {"before_hello": self.events[actor + ":hello"],
                          "before_connected": page.evaluate(connected),
                          "before_generation": self.events[actor + ":opened"]}
            self.checks["navigation_" + actor] = navigation
            with page.expect_response(lambda response:
                    response.request.method == "GET"
                    and urlsplit(response.url).path.startswith("/api/v1/boards/")) as reread:
                page.reload(wait_until="domcontentloaded")
            board = reread.value.json()
            if board.get("revealed") is not False or board.get("card_count") != 1:
                raise AssertionError("scheduled_fixture_read")
            if actor == "organizer":
                pw.expect(page.get_by_text(WISH, exact=True)).to_be_visible()
            elif board.get("cards") != []:
                raise AssertionError("guest_preview_leak")
            else:
                pw.expect(page.get_by_text(WISH, exact=True)).to_have_count(0)
            pw.expect(page.locator('[data-field="manage"]')).to_have_count(0)
            if self.requests[actor + ":documents"] < 2:
                raise AssertionError("repeat_document_missing")
            if page.evaluate("document.documentElement.scrollWidth > innerWidth"):
                raise AssertionError("horizontal_overflow")
            self.wait_frame(page, actor + ":hello", navigation["before_hello"])
            page.wait_for_function(connected)
            navigation.update(after_hello=self.events[actor + ":hello"],
                              after_connected=page.evaluate(connected),
                              after_generation=self.events[actor + ":opened"])
            self.checks["stage"] = "capture_" + actor
            page.screenshot(path=self.output / (actor + "-" + str(width) + ".png"), full_page=True)
        self.checks["repeat_document_gets"] = True

    def record_public(self, browser: pw.Browser, organizer: pw.Page, settings: dict) -> None:
        options = dict(settings, storage_state=organizer.context.storage_state(),
                       record_video_dir=str(self.output / "video"),
                       record_video_size={"width": 1440, "height": 1000})
        try:
            page = self.page(browser, "recording", options)
        except pw.Error as error:
            if "ffmpeg" not in str(error).lower():
                raise
            self.checks["video_status"] = "playwright_recorder_unavailable"
            return
        page.goto(organizer.url, wait_until="domcontentloaded")
        pw.expect(page.get_by_text(WISH, exact=True)).to_be_visible()
        pw.expect(page.locator('[data-field="manage"]')).to_have_count(0)
        # Recording dwell is intentional; assertions above establish readiness.
        page.wait_for_timeout(3000)
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_text(WISH, exact=True).scroll_into_view_if_needed()
        page.wait_for_timeout(3000)
        video = page.video
        self.checks["stage"] = "recording_teardown"
        page.context.close()
        self.contexts.remove(("recording", page.context))
        if video is None:
            raise AssertionError("missing_video")
        video.save_as(self.output / "otkrytka-fixture.webm")
        self.checks["video_status"] = "recorded_owned_fixture_only"

    def run(self, browser: pw.Browser, video: bool) -> None:
        agent = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/"
                 + browser.version + " Safari/537.36")
        settings = {"viewport": {"width": 1440, "height": 1000}, "user_agent": agent}
        organizer = self.page(browser, "organizer", settings)
        guest_path = self.create_board(organizer)
        guest = self.page(browser, "guest", dict(settings, viewport={"width": 390, "height": 844}))
        self.prove_realtime(organizer, guest, guest_path)
        self.reload_and_capture(organizer, guest)
        self.checks["stage"] = "default_headless_visit"
        bot = self.page(browser, "bot", {"viewport": {"width": 390, "height": 844}})
        bot.goto(self.base_url, wait_until="domcontentloaded")
        pw.expect(bot.locator("#create-title")).to_be_visible()
        if not self.checks.get("default_headless_user_agent"):
            raise AssertionError("default_headless_marker_missing")
        self.checks["video_status"] = "not_requested"
        if video:
            self.record_public(browser, organizer, settings)
            if self.checks["video_status"] != "recorded_owned_fixture_only":
                raise AssertionError("requested_video_not_recorded")
        self.annotate_navigation_errors()
        self.checks["stage"] = "complete"
        if self.errors:
            raise AssertionError("unexpected_browser_errors")

    def close(self) -> None:
        for actor, context in self.contexts:
            self.checks["stage"] = "closing_" + actor
            try:
                context.close()
            except pw.Error:
                self.errors["cleanup"] += 1


def main() -> int:
    """Parse the local fixture target and export only sanitized verification metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, type=loopback_origin)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--executable",
                        default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    parser.add_argument("--video", action="store_true",
                        help="Record owned fixture views after setup if ffmpeg is available")
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    probe = OtkrytkaBrowser(options.base_url, options.output)
    error_class = None
    try:
        with pw.sync_playwright() as playwright:
            with playwright.chromium.launch(executable_path=options.executable) as browser:
                try:
                    probe.run(browser, options.video)
                finally:
                    probe.close()
    except (pw.Error, AssertionError, ValueError, OSError, TypeError, RuntimeError) as error:
        error_class = type(error).__name__
    passed = error_class is None and not probe.errors
    result = {"passed": passed, "error_class": error_class, "checks": probe.checks,
              "error_counts": dict(probe.errors), "websocket_frames": dict(probe.events),
              "websocket_diagnostics": probe.socket_trace,
              "browser_request_counts": dict(probe.requests),
              "synthetic_normal_ua_local_only": True,
              "server_metrics": "Not asserted here; require independent receiver/PG receipts"}
    (options.output / "browser-proof.json").write_text(json.dumps(result, indent=2) + "\n")
    sys.stdout.write(json.dumps({"passed": passed, "error_class": error_class,
                                "unexpected_errors": sum(probe.errors.values())}) + "\n")
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
