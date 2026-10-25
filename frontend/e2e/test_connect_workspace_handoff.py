"""Verify the public connection guide hands an anonymous owner back to the workspace."""

import hashlib
import json
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import auth_form_fixtures as browser_fixtures
from playwright.sync_api import expect

COPY = {
    "en": {
        "next": "What next",
        "copy": "Copy prompt",
        "copied": "Prompt copied to clipboard",
        "agents": "Agents",
        "title": "Workspace",
        "auth": "Your workspace requires sign-in. Return here after signing in.",
        "signin": "Sign in to see your agents",
        "setup": "Connection instructions",
    },
    "ru": {
        "next": "Что дальше",
        "copy": "Скопировать запрос",
        "copied": "Запрос скопирован в буфер обмена",
        "agents": "Агенты",
        "title": "Рабочая область",
        "auth": "Для рабочей области нужен вход. После входа вы вернётесь сюда.",
        "signin": "Войти и посмотреть агентов",
        "setup": "Как подключить агента",
    },
}
CANCEL_COUNTERS = ("readerCancel", "bodyCancel", "releases")
READ_COUNTERS = ("bytes", "done", "errors", "reads", *CANCEL_COUNTERS)
PAGES = browser_fixtures.PREFETCH | {"/docs/connect", "/docs/glossary", "/privacy"}


def _identity(item):
    return tuple(
        item.get(key) for key in ("path", "key", "local", "method", "rsc", "prefetch")
    )


def _extends_read_history(previous, record):
    return previous is None or (
        _identity(previous) == _identity(record)
        and all(record[key] >= previous[key] for key in READ_COUNTERS)
    )


def _eof_observed(event, records, requests, verified):
    """Classify only uniquely correlated EOF observations; transport cause stays unknown."""
    identity = _identity(event)
    matches = [record for record in records if _identity(record) == identity]
    if (
        not verified
        or len(matches) != 1
        or sum(_identity(r) == identity for r in requests) != 1
    ):
        return False
    record = matches[0]
    return (
        event.get("error") == "net::ERR_ABORTED"
        and event.get("local") is True
        and event.get("method") == "GET"
        and event.get("rsc") is True
        and type(event.get("prefetch")) is bool
        and isinstance(event.get("key"), str)
        and bool(re.fullmatch(r"[0-9a-f]{64}", event["key"]))
        and record.get("status") == 200
        and record.get("mime") == "text/x-component"
        and record.get("bytes", 0) > 0
        and record.get("done", 0) > 0
        and record.get("errors") == 0
        and record.get("pending") == 0
        and all(
            type(record.get(key)) is int and record[key] == 0 for key in CANCEL_COUNTERS
        )
    )


class ReadClassificationTests(unittest.TestCase):
    @staticmethod
    def observed_fixture(prefetch=True):
        # Shape and byte count from response-eof-result.json: GET /, 211 bytes, done=1.
        event = {
            "path": "/",
            "key": "a" * 64,
            "local": True,
            "method": "GET",
            "rsc": True,
            "prefetch": prefetch,
            "error": "net::ERR_ABORTED",
        }
        record = dict(
            event,
            status=200,
            mime="text/x-component",
            bytes=211,
            done=1,
            errors=0,
            pending=0,
            readerCancel=0,
            bodyCancel=0,
            releases=0,
        )
        return event, record

    def test_observed_eof_requires_each_evidence_predicate(self):
        event, record = self.observed_fixture()
        for purpose in (True, False):
            sample, response = self.observed_fixture(purpose)
            self.assertTrue(_eof_observed(sample, [response], [sample], True))
        mutations = {
            "premature_abort": {"done": 0},
            "read_rejection": {"errors": 1},
            "empty": {"bytes": 0},
            "pending": {"pending": 1},
            "wrong_status": {"status": 500},
            "wrong_mime": {"mime": "text/html"},
            "reader_cancel": {"readerCancel": 1},
            "body_cancel": {"bodyCancel": 1},
            "release": {"releases": 1},
            "unknown_cancel": {"readerCancel": None},
            "unknown_body_cancel": {"bodyCancel": "0"},
            "unknown_release": {"releases": False},
        }
        for name, change in mutations.items():
            with self.subTest(name=name):
                self.assertFalse(
                    _eof_observed(event, [dict(record, **change)], [event], True)
                )
        for key, value in (
            ("method", "POST"),
            ("local", False),
            ("rsc", None),
            ("prefetch", None),
            ("prefetch", 0),
            ("error", "net::ERR_FAILED"),
            ("key", None),
        ):
            with self.subTest(field=key):
                changed = dict(event, **{key: value})
                self.assertFalse(
                    _eof_observed(
                        changed, [dict(record, **{key: value})], [changed], True
                    )
                )
        for records, requests, verified in (
            ([], [event], True),
            ([record] * 2, [event], True),
            ([record], [event] * 2, True),
            ([record], [event], False),
        ):
            with self.subTest(
                records=len(records), requests=len(requests), verified=verified
            ):
                self.assertFalse(_eof_observed(event, records, requests, verified))
        for field in CANCEL_COUNTERS:
            with self.subTest(missing=field):
                self.assertFalse(
                    _eof_observed(
                        event,
                        [{k: v for k, v in record.items() if k != field}],
                        [event],
                        True,
                    )
                )


class ConnectWorkspaceTests(browser_fixtures.BrowserFixture, unittest.TestCase):
    """Exercise real copy and navigation without accounts or backend requests."""

    def route(self, route, state):
        """Permit local static/page reads; fail closed on all APIs and writes."""
        request, target = route.request, urlparse(route.request.url)
        local = target.scheme == "http" and target.netloc == self.origin
        page_query = not target.query or re.fullmatch(
            r"_rsc=[A-Za-z0-9_-]+", target.query
        )
        if target.path == "/login" and target.query:
            query = parse_qs(target.query, keep_blank_values=True)
            page_query = page_query or (
                set(query) <= {"next", "_rsc"}
                and query.get("next") == ["/workspace"]
                and (
                    "_rsc" not in query
                    or len(query["_rsc"]) == 1
                    and re.fullmatch(r"[A-Za-z0-9_-]+", query["_rsc"][0])
                )
            )
        allowed = target.path.startswith("/_next/") or (
            target.path in PAGES and page_query
        )
        if local and request.method == "GET" and allowed:
            route.continue_()
        elif local and request.method == "GET" and target.path == "/favicon.ico":
            route.fulfill(status=204)
        else:
            state["failures"].append(f"Blocked {request.method} {target.path}")
            route.abort()

    def open_guide(self, locale, width):
        """Create an isolated anonymous browser with only a known locale preference."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            locale=locale,
            service_workers="block",
        )
        context.add_cookies(
            [
                {
                    "name": "agentspore_locale",
                    "value": locale,
                    "url": self.options.base_url,
                }
            ]
        )
        self.origin = urlparse(self.options.base_url).netloc
        state = {
            "case": f"{locale}-{width}",
            "phase": "opening",
            "failures": [],
            "request_failures": [],
            "requests": {},
            "snapshots": [],
        }
        self.addCleanup(self.close_guide, context, state)
        context.route("**/*", lambda route: self.route(route, state))
        context.add_init_script(
            script="const CONNECT_READ_PAGES = "
            + json.dumps(sorted(PAGES))
            + ";\n"
            + Path(__file__).with_name("connect_workspace_read_observer.js").read_text()
        )
        context.add_init_script("""
            window.publicPromptCopy = null;
            Object.defineProperty(navigator, 'clipboard', {value: {
                writeText: async text => { window.publicPromptCopy = text; }
            }});
        """)
        page = context.new_page()
        page.set_default_timeout(5000)
        self.watch_page(page, state)
        page.goto(self.options.base_url + "/docs/connect", wait_until="networkidle")
        expect(page.locator("html")).to_have_attribute("lang", locale)
        page.evaluate("document.fonts.ready")
        state["phase"] = "active"
        return page, state

    def watch_page(self, page, state):
        """Bind every callback to its context; retain only finite safe request flags."""
        failures = state["failures"]
        page.on("pageerror", lambda _: failures.append("Browser pageerror"))
        page.on(
            "console",
            lambda msg: (
                failures.append("Console error") if msg.type == "error" else None
            ),
        )
        page.on("request", lambda req: self.remember_request(req, state))
        page.on("requestfailed", lambda req: self.record_failure(req, state))
        page.on(
            "response",
            lambda res: (
                failures.append(f"HTTP {res.status} {urlparse(res.url).path}")
                if res.status >= 400
                else None
            ),
        )

    def remember_request(self, request, state):
        """Capture no query, cookie, auth header, body, or unrestricted header values."""
        state["requests"][request] = {
            "method": request.method,
            "path": urlparse(request.url).path,
            "local": urlparse(request.url).netloc == self.origin,
            "start_phase": state["phase"],
            "key": self.request_key(request.url),
            "rsc": self.request_flag(request, "rsc"),
            "prefetch": self.request_flag(request, "next-router-prefetch"),
        }

    @staticmethod
    def request_key(url):
        target = urlparse(url)
        query = parse_qs(target.query, keep_blank_values=True)
        values, next_value = query.get("_rsc", []), query.get("next", [])
        valid_next = (
            not next_value or target.path == "/login" and next_value == ["/workspace"]
        )
        if (
            target.path not in PAGES
            or set(query) - {"_rsc", "next"}
            or len(values) != 1
            or not re.fullmatch(r"[A-Za-z0-9_-]+", values[0])
            or not valid_next
        ):
            return None
        payload = json.dumps(
            [target.path, "workspace-return" if next_value else "", values[0]],
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def request_flag(request, name):
        value = request.headers.get(name)
        return True if value == "1" else False if value is None else None

    def snapshot_reads(self, page, state):
        state["snapshots"].append(page.evaluate("() => __connectReadSnapshot()"))

    def assert_body_gate(self, state):
        """Keep the raw gate separate from the observed-body gate and UX assertions."""
        records, verified = {}, bool(state["snapshots"])
        for snapshot in state["snapshots"]:
            verified = verified and snapshot["verified"] and snapshot["overflow"] == 0
            seen = set()
            for record in snapshot["records"]:
                key = (snapshot["documentID"], record["id"])
                verified = verified and key not in seen
                seen.add(key)
                previous = records.get(key)
                verified = verified and _extends_read_history(previous, record)
                records[key] = record
        application = [r for r in records.values() if not r["control"]]
        requests = list(state["requests"].values())
        events = state["request_failures"]
        verified = verified and len({_identity(e) for e in events}) == len(events)
        classified = sum(
            _eof_observed(e, application, requests, verified) for e in events
        )
        other = [
            failure
            for failure in state["failures"]
            if not failure.startswith("Request failed ")
        ]
        state["body_gate"] = {
            "eof_observed_despite_ERR_ABORTED": classified,
            "unclassified": len(state["request_failures"]) - classified,
            "observer_verified": bool(verified),
            "other_failures": other,
        }
        state["raw_network_count"] = len(state["request_failures"])
        state["raw_strict_gate"] = "RED" if state["failures"] else "GREEN"
        print(
            json.dumps(
                {
                    "case": state["case"],
                    "raw_strict_gate": state["raw_strict_gate"],
                    "raw_network_count": state["raw_network_count"],
                    **state["body_gate"],
                }
            )
        )
        self.close_evidence(state)
        self.assertTrue(verified, "Read observer controls/identity failed")
        self.assertEqual(
            state["body_gate"]["unclassified"], 0, "Unclassified request failure"
        )
        self.assertEqual(other, [], "Browser/HTTP/block gate")

    def record_failure(self, request, state):
        """Keep all cancellations, including those emitted by context teardown."""
        event = dict(state["requests"].get(request, {}))
        event.update(phase=state["phase"], error=request.failure)
        state["request_failures"].append(event)
        with (self.options.screenshots / "request-failures.jsonl").open("a") as log:
            log.write(json.dumps({"case": state["case"], **event}) + "\n")
        state["failures"].append(
            f"Request failed {urlparse(request.url).path} {request.failure}"
        )

    def close_guide(self, context, state):
        """Retain raw context-owned failures after close, without classifying them away."""
        if state["phase"] == "closed":
            return
        state["phase"] = "closing"
        context.close()
        state["phase"] = "closed"
        self.close_evidence(state)

    def close_evidence(self, state):
        evidence = {key: value for key, value in state.items() if key != "requests"}
        (self.options.screenshots / f"{state['case']}-network.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )

    def capture(self, page, name):
        """Retain rendered evidence and reject horizontal document overflow."""
        page.screenshot(
            path=self.options.screenshots / f"{self.tag}-{name}.png", full_page=True
        )
        self.assertTrue(
            page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        )

    def assert_handoff(self, page, copy, state):
        """Use only the guide's final section, then inspect the real anonymous destination."""
        final = page.get_by_role("region", name=copy["next"], exact=True)
        expect(
            final.get_by_role("link", name=copy["agents"], exact=True)
        ).to_have_attribute("href", "/agents")
        action = final.locator('a[href="/workspace"]')
        expect(action).to_have_count(1)
        expect(action).to_have_accessible_name(re.compile(r"\S"))
        action.focus()
        expect(action).to_be_focused()
        self.capture(page, "workspace-action")
        self.snapshot_reads(page, state)
        action.press("Enter")
        page.wait_for_url(
            self.options.base_url + "/workspace", wait_until="networkidle"
        )
        main = page.get_by_role("main")
        expect(
            main.get_by_role("heading", name=copy["title"], exact=True)
        ).to_be_visible()
        expect(main.get_by_text(copy["auth"], exact=True)).to_be_visible()
        expect(
            main.get_by_role("link", name=copy["signin"], exact=True)
        ).to_have_attribute("href", "/login?next=/workspace")
        expect(
            main.get_by_role("heading", name=copy["setup"], exact=True)
        ).to_be_visible()
        self.capture(page, "anonymous-workspace")

    def test_handoff_matrix(self):
        for locale in ("en", "ru"):
            for width in (390, 1440):
                with self.subTest(locale=locale, width=width):
                    copy, self.tag = COPY[locale], f"{locale}-{width}"
                    page, state = self.open_guide(locale, width)
                    try:
                        prompt = page.get_by_test_id("connect-prompt")
                        rendered = prompt.inner_text()
                        page.get_by_role(
                            "button", name=copy["copy"], exact=True
                        ).click()
                        expect(page.get_by_role("status")).to_have_text(copy["copied"])
                        copied = page.evaluate("window.publicPromptCopy")
                        self.assertEqual(copied, rendered)
                        self.capture(page, "guide")
                        with self.subTest(
                            contract="owner_email in rendered and copied prompt"
                        ):
                            self.assertRegex(rendered, r"\bowner_email\b")
                            self.assertRegex(copied, r"\bowner_email\b")
                        with self.subTest(
                            contract="final workspace action and anonymous state"
                        ):
                            self.assert_handoff(page, copy, state)
                        state["checked_failures"] = list(state["failures"])
                        self.snapshot_reads(page, state)
                    finally:
                        self.close_guide(page.context, state)
                    self.assert_body_gate(state)


if __name__ == "__main__":
    ConnectWorkspaceTests.options, unittest_args = browser_fixtures.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
