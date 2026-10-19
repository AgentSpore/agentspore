"""Measure the closed Header SIZE/TEXT/NAV matrix on an explicit loopback stand.

Reuse accepted Header unit and navigation proofs; contrast is outside this suite.
"""

import json
import unittest
from urllib.parse import urlparse

import auth_form_fixtures as auth
import test_battles_accessibility as browser
import test_battles_data_accessibility as metrics
from playwright.sync_api import expect

LAYOUTS = [(locale, width) for locale in ("en", "ru") for width in (320, 390, 1024, 1280)]
PROFILE = dict(
    auth.USER,
    name="Synthetic Long Header Owner " * 4,
    email="synthetic-long-header-owner@example.invalid",
    token_balance=1234567,
)
HEADER = "[data-agent-header]"
CONTROLS = "a[href], button"
STATE = (
    """root => {
    const controls = [...root.querySelectorAll('a[href], button')];
    return controls.map((el, index) => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        let clipTop = 0, clipBottom = innerHeight;
        for (let parent = el.parentElement; parent; parent = parent.parentElement) {
            if (!['auto', 'scroll', 'hidden', 'clip'].includes("""
    """getComputedStyle(parent).overflowY)) continue;
            const bounds = parent.getBoundingClientRect();
            clipTop = Math.max(clipTop, bounds.top);
            clipBottom = Math.min(clipBottom, bounds.bottom);
        }
        return {index, label: el.getAttribute('aria-label') || el.innerText,
            href: el.getAttribute('href'), visible: el.checkVisibility(),
            x: r.x, right: r.right, top: r.top, bottom: r.bottom,
            width: r.width, height: r.height, focused: el === document.activeElement,
            clipTop, clipBottom,
            focusVisible: el.matches(':focus-visible'), outline: s.outlineStyle,
            outlineWidth: parseFloat(s.outlineWidth), shadow: s.boxShadow,
            hit: el === hit || el.contains(hit)};
    });
}"""
)
DENIAL = """(() => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = function(key) {
        if (window.__syntheticHeaderStorageDenied) throw new DOMException('Synthetic denial');
        return original.call(this, key);
    };
})();"""


class HeaderHTTP(auth.AuthHTTP):
    """Hold only the real profile read; inherited routing rejects unknown traffic."""

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if (
            target.scheme == "http"
            and target.netloc == self.origin
            and target.path == "/api/v1/auth/me"
            and not target.query
            and request.method == "GET"
        ):
            self.pending.append(route)
        else:
            super().route(route)

    def settle(self, payload):
        route = self.pending.pop(0)
        if payload is None:
            route.fulfill(status=503, json={"detail": "Synthetic profile unavailable"})
        else:
            route.fulfill(json=payload)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


class HeaderStateTests(unittest.TestCase):
    """Check missing rendered predicates without multiplying unrelated branches."""

    @classmethod
    def setUpClass(cls):
        browser.BattleAccessibilityTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.BattleAccessibilityTests.tearDownClass.__func__(cls)

    def open_header(self, layout, session=False):
        self.locale, self.width = layout
        context = self.browser.new_context(
            viewport={"width": self.width, "height": 600 if self.width == 320 else 900},
            service_workers="block",
        )
        context.add_cookies(
            [{"name": "agentspore_locale", "value": self.locale, "url": self.options.base_url}]
        )
        context.add_init_script(DENIAL)
        if session:
            context.add_init_script("localStorage.setItem('access_token', 'synthetic-header')")
        self.http = HeaderHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(7000)
        self.errors, self.trace = [], []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.expose_function("__headerPending", lambda: len(self.http.pending))
        page.goto(self.options.base_url + "/login")
        page.locator(HEADER).wait_for()
        return page

    def button(self, page, english, russian):
        return page.locator(HEADER).get_by_role(
            "button", name=english if self.locale == "en" else russian, exact=True
        )

    def menu(self, page):
        return self.button(page, "Menu", "Меню")

    def account(self, page):
        return (
            page.locator(HEADER)
            .locator("button[aria-controls]")
            .filter(has_text="Synthetic Long Header Owner")
        )

    def tab_to(self, page, target):
        for _ in range(page.locator("a[href], button, input, select, textarea").count() * 2 + 4):
            page.keyboard.press("Tab")
            if target.evaluate("el => el === document.activeElement"):
                return
        self.save(page, "unreachable")
        self.fail("Native Tab did not reach the expected Header control")

    def activate(self, page, target):
        self.tab_to(page, target)
        page.keyboard.press("Enter")

    def save(self, page, tag):
        page.evaluate("document.fonts.ready")
        page.locator(HEADER).evaluate("el => el.getBoundingClientRect()")
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        root = page.locator(HEADER)
        records = {
            "state": tag,
            "locale": self.locale,
            "width": self.width,
            "actions": root.evaluate(STATE),
            "text": root.evaluate(metrics.TEXT),
            "keyboard": self.trace,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "document_width": page.evaluate("document.documentElement.scrollWidth"),
            "contrast": "not measured; RR.4",
        }
        target = self.options.screenshots / f"header-{tag}-{self.locale}-{self.width}"
        target.with_suffix(".json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
        page.screenshot(path=str(target.with_suffix(".png")), full_page=False)
        return records

    def audit(self, page, tag):
        records = self.save(page, tag)
        self.assertEqual(records["unexpected"], [])
        self.assertEqual(records["page_errors"], [])
        self.assertLessEqual(records["document_width"], self.width)
        self.assertEqual(self.menu(page).is_visible(), self.width < 1024)
        self.assertEqual(page.locator(f"{HEADER} .xl\\:inline").is_visible(), self.width >= 1280)
        for record in records["text"]:
            with self.subTest(state=tag, text=record["text"]):
                self.assertGreaterEqual(record["size"], 12, record)
        for record in records["actions"]:
            if record["visible"]:
                with self.subTest(state=tag, action=record["label"]):
                    self.assertGreaterEqual(record["width"], 44, record)
                    self.assertGreaterEqual(record["height"], 44, record)
                    self.assertGreaterEqual(record["x"], -1, record)
                    self.assertLessEqual(record["right"], self.width + 1, record)

    def traverse(self, page, tag):
        for key in ("Tab", "Shift+Tab"):
            self._traverse_direction(page, tag + "-" + key, key)

    def _traverse_direction(self, page, tag, key):
        root, self.trace = page.locator(HEADER), []
        expected = {r["index"] for r in root.evaluate(STATE) if r["visible"]}
        reached = set()
        for _ in range(page.locator("a[href], button, input, select, textarea").count() * 2 + 4):
            page.keyboard.press(key)
            focused = [r for r in root.evaluate(STATE) if r["focused"]]
            if focused:
                self.trace.extend(focused)
                reached.add(focused[0]["index"])
            if reached == expected:
                break
        self.save(page, tag)
        self.assertEqual(reached, expected)
        for record in self.trace:
            with self.subTest(state=tag, focus=record["label"]):
                self.assertTrue(
                    record["visible"] and record["focusVisible"] and record["hit"], record
                )
                self.assertTrue(
                    (record["outline"] != "none" and record["outlineWidth"] > 0)
                    or record["shadow"] != "none",
                    record,
                )
                self.assertGreaterEqual(record["top"], record["clipTop"] - 1, record)
                self.assertLessEqual(record["bottom"], record["clipBottom"] + 1, record)

    def authenticate(self, page):
        page.evaluate(
            "localStorage.setItem('access_token', 'synthetic-header'); "
            "window.dispatchEvent(new Event('storage'))"
        )
        page.wait_for_function("async () => await window.__headerPending() > 0")

    def resolve(self, page, payload):
        self.http.settle(payload)
        if payload is None:
            page.locator(f"{HEADER} a[href='/login']:visible").wait_for()
        elif self.width >= 1024:
            self.account(page).wait_for()
        else:
            page.locator(f"{HEADER} .lg\\:hidden .ml-auto > .rounded-full").wait_for()

    def test_desktop_shell_and_panels(self):
        for layout in (value for value in LAYOUTS if value[1] >= 1024):
            with self.subTest(layout=layout):
                page = self.open_header(layout)
                page.locator(f"{HEADER} a[href='/login']:visible").wait_for()
                self.audit(page, "H02-shell")
                self.authenticate(page)
                self.audit(page, "H03-pending")
                self.resolve(page, PROFILE)
                self.audit(page, "H05-account-closed")
                self.activate(page, self.account(page))
                self.audit(page, "H06-account")
                self.traverse(page, "H06-native")
                more = self.button(page, "More", "Ещё")
                self.activate(page, more)
                self.audit(page, "H08-both")
                self.traverse(page, "H08-native")
                self.escape_from(page, self.account(page), more)
                self.activate(page, more)
                self.audit(page, "H07-more")
                self.traverse(page, "H07-native")
                self.activate(page, self.account(page))
                self.escape_from(page, more, self.account(page))
                page.context.close()

    def escape_from(self, page, account, more):
        target = page.locator(f"{HEADER} a[href='/profile']:visible")
        if account.inner_text() in ("More", "Ещё"):
            target = page.locator(f"{HEADER} a[href='/chat']:visible")
        origin = target.get_attribute("href").strip("/")
        self.tab_to(page, target)
        page.keyboard.press("Escape")
        self.save(page, "H08-escape-" + origin)
        expect(account).to_have_attribute("aria-expanded", "false")
        expect(more).to_have_attribute("aria-expanded", "false")
        expect(account).to_be_focused()

    def test_mobile_shell_and_authenticated_footer(self):
        for layout in (value for value in LAYOUTS if value[1] < 1024):
            with self.subTest(layout=layout):
                page = self.open_header(layout)
                self.audit(page, "H02-mobile-shell")
                self.activate(page, self.menu(page))
                expect(page.locator(f"{HEADER} a[href='/login']:visible")).to_be_visible()
                self.audit(page, "H10-anonymous-menu")
                self.traverse(page, "H10-native")
                self.authenticate(page)
                self.audit(page, "H11-pending-menu")
                self.assertEqual(page.locator(f"{HEADER} a[href='/login']:visible").count(), 0)
                self.resolve(page, PROFILE)
                self.audit(page, "H09-authenticated-menu")
                self.traverse(page, "H09-native")
                page.keyboard.press("Escape")
                self.audit(page, "H05-mobile-avatar")
                page.context.close()

    def test_zero_balance_empty_name_and_failure(self):
        for layout in (("en", 1024), ("ru", 320)):
            with self.subTest(layout=layout):
                page = self.open_header(layout, session=True)
                page.wait_for_function("async () => await window.__headerPending() > 0")
                if self.width < 1024:
                    self.activate(page, self.menu(page))
                self.resolve(page, None)
                self.audit(page, "H04-profile-failure")
                page.evaluate(
                    "localStorage.setItem('access_token', 'synthetic-header-next'); "
                    "window.dispatchEvent(new Event('storage'))"
                )
                page.wait_for_function("async () => await window.__headerPending() > 0")
                self.http.settle(dict(PROFILE, name="", token_balance=0))
                avatar = page.locator(f"{HEADER} .rounded-full:visible").filter(has_text="?")
                avatar.first.wait_for()
                if self.width >= 1024:
                    self.activate(page, avatar.locator(".."))
                self.audit(page, "H06-H09-zero-fallback")
                self.assertEqual(
                    page.locator(HEADER).get_by_text("0 $ASPORE", exact=True).count(),
                    0 if self.width >= 1024 else 1,
                )
                page.context.close()

    def test_storage_loss_and_restoration(self):
        page = self.open_header(("en", 320), session=True)
        page.wait_for_function("async () => await window.__headerPending() > 0")
        page.evaluate(
            "window.__syntheticHeaderStorageDenied = true; "
            "window.dispatchEvent(new Event('storage'))"
        )
        self.activate(page, self.menu(page))
        page.locator(f"{HEADER} a[href='/login']:visible").wait_for()
        self.audit(page, "storage-denied-pending")
        self.http.settle(PROFILE)
        self.save(page, "storage-denied-late")
        expect(page.locator(HEADER).get_by_text(PROFILE["email"], exact=True)).to_have_count(0)
        page.evaluate(
            "window.__syntheticHeaderStorageDenied = false; "
            "window.dispatchEvent(new Event('storage'))"
        )
        page.wait_for_function("async () => await window.__headerPending() > 0")
        self.resolve(page, PROFILE)
        self.audit(page, "storage-restored-H09")
        page.evaluate(
            "window.__syntheticHeaderStorageDenied = true; "
            "window.dispatchEvent(new Event('storage'))"
        )
        page.locator(f"{HEADER} a[href='/login']:visible").wait_for()
        self.audit(page, "storage-lost-authenticated")
        expect(page.locator(HEADER).get_by_text(PROFILE["email"], exact=True)).to_have_count(0)
        page.context.add_init_script("window.__syntheticHeaderStorageDenied = true")
        page.reload()
        self.activate(page, self.menu(page))
        page.locator(f"{HEADER} a[href='/login']:visible").wait_for()
        self.audit(page, "storage-denied-initially")

    def test_breakpoint_hidden_controls_and_reduced_motion(self):
        page = self.open_header(("en", 1280), session=True)
        page.wait_for_function("async () => await window.__headerPending() > 0")
        self.resolve(page, PROFILE)
        self.activate(page, self.account(page))
        self.activate(page, self.button(page, "More", "Ещё"))
        page.set_viewport_size({"width": 320, "height": 600})
        self.width = 320
        self.activate(page, self.menu(page))
        self.traverse(page, "breakpoint-mobile")
        page.set_viewport_size({"width": 1280, "height": 900})
        self.width = 1280
        self.traverse(page, "breakpoint-desktop")
        page.emulate_media(reduced_motion="reduce")
        self.save(page, "reduced-motion")
        self.assertEqual(
            page.locator(HEADER).evaluate("el => getComputedStyle(el).transitionDuration"), "0s"
        )

    def test_scrolled_mobile_focus(self):
        page = self.open_header(("en", 320))
        page.keyboard.press("End")
        self.save(page, "scrolled-shell")
        self.assertGreater(page.evaluate("window.scrollY"), 8)
        self.assertAlmostEqual(page.locator(HEADER).bounding_box()["y"], 0, delta=1)
        self.activate(page, self.menu(page))
        self.traverse(page, "scrolled-native")


if __name__ == "__main__":
    HeaderStateTests.options, unittest_args = browser.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
