"""Exercise concurrent block removals on the real compiled profile widget."""

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

import test_auth_forms as auth
import test_battles_accessibility as battles
import test_project_cards as cards
from auth_form_fixtures import AuthHTTP
from playwright.sync_api import expect

TARGET_POINTS = """el => {
    const r = el.getBoundingClientRect();
    return [r.left+4, r.left+r.width/2, r.right-4].flatMap(x =>
      [r.top+4, r.top+r.height/2, r.bottom-4].map(y => {
        const hit = document.elementFromPoint(x, y);
        return {x, y, hit: hit === el || el.contains(hit),
          coveringLabel: hit?.closest('button')?.getAttribute('aria-label') || hit?.tagName};
      }));
}"""
FLOATING_STYLE = """el => {
    const read = node => {
      const style = getComputedStyle(node);
      return {visibility: style.visibility, transitionProperty: style.transitionProperty,
        opacity: style.opacity};
    };
    return {parent: read(el.parentElement), child: read(el)};
}"""
SCROLL_SOURCE = (Path(__file__).resolve().parents[1] / "src/lib/i18n/shared.ts").read_text()
SCROLL_LABELS = {
    locale: json.loads(re.search(r'"scrollToTop": ("[^"\n]+")', part).group(1))
    for locale, part in zip(("en", "ru"), SCROLL_SOURCE.split('"ru": {', 1))
}
SOURCE = (Path(__file__).resolve().parents[1] / "src/lib/i18n/battles.ts").read_text()
KEYS = (
    "$ ls battles/blocks/",
    "Loading blocks...",
    "unblock",
    "failed to load blocks",
    "failed to remove the block",
    "No blocks yet. You can block an owner from the battle page by declining their challenge.",
)
MESSAGES = {
    locale: {
        key: json.loads(
            re.search(
                r"^\s*" + re.escape(json.dumps(key)) + r': ("(?:[^"\\]|\\.)*")',
                section,
                re.M,
            ).group(1)
        )
        for key in KEYS
    }
    for locale, section in zip(("en", "ru"), SOURCE.split("  ru: {", 1))
}
BLOCKS = [
    {
        "id": "00000000-0000-4000-8000-000000000001",
        "blocked_owner_id": "00000000-0000-4000-8000-000000000011",
        "created_at": "2026-01-02T00:00:00Z",
    },
    {
        "id": "00000000-0000-4000-8000-000000000002",
        "blocked_owner_id": "00000000-0000-4000-8000-000000000012",
        "created_at": "2026-01-01T00:00:00Z",
    },
]


class BlocksHTTP(AuthHTTP):
    """Hold only exact list and bodyless owner-block DELETE requests."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.held = {}

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        prefix = "/api/v1/battles/blocks"
        if target.netloc == self.origin and target.scheme == "http" and not target.query:
            key = (
                "list"
                if target.path == prefix
                else next(
                    (row["id"] for row in BLOCKS if target.path == prefix + "/" + row["id"]),
                    None,
                )
            )
            method = "GET" if key == "list" else "DELETE"
            if key and request.method == method and not request.post_data:
                self.requests.append({"method": method, "path": target.path, "body": None})
                self.held[key] = route
                return
        super().route(route)


class ProfileBlocksTests(unittest.TestCase):
    """Measure only this widget; reuse accepted browser and action contracts."""

    setUpClass = classmethod(auth.AuthFormTests.setUpClass.__func__)
    tearDownClass = classmethod(auth.AuthFormTests.tearDownClass.__func__)
    assert_action = auth.AuthFormTests.assert_action

    def open_blocks(self, locale, width, mode):
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            locale=locale,
            service_workers="block",
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [
                {
                    "name": "agentspore_locale",
                    "value": locale,
                    "url": self.options.base_url,
                }
            ]
        )
        context.add_init_script("localStorage.setItem('access_token','synthetic-blocks-session')")
        self.http, self.errors = BlocksHTTP(self.options.base_url), []
        self.copy, self.width, self.locale = MESSAGES[locale], width, locale
        self.tag = f"blocks-{locale}-{width}-{mode}"
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(self.options.base_url + "/profile")
        self.widget = (
            page.get_by_role("heading", name=self.copy[KEYS[0]], exact=True)
            .locator("..")
            .locator("..")
        )
        expect(self.widget).to_be_visible()
        expect(self.widget.get_by_text(self.copy[KEYS[1]], exact=True)).to_be_visible()
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "Promise.all(document.getAnimations()"
            ".filter(a => a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        return page

    def capture(self, page, state):
        """Capture full target hit coverage and preserve accepted center/focus checks."""
        actions = []
        for control in self.widget.locator("button:visible:not([disabled]),a:visible").all():
            for _ in range(80):
                page.keyboard.press("Tab")
                if control.evaluate("el => el === document.activeElement"):
                    break
            action = control.evaluate(cards.ACTION)
            action["inline"] = False
            action["points"] = control.evaluate(TARGET_POINTS)
            action["floating"] = page.get_by_role(
                "button", name=SCROLL_LABELS[self.locale], exact=True, include_hidden=True
            ).evaluate(FLOATING_STYLE)
            actions.append(action)
        data = {
            "state": state,
            "actions": actions,
            "text": self.widget.evaluate(cards.TEXT),
            "rows": [
                row["id"]
                for row in BLOCKS
                if self.widget.get_by_text(row["blocked_owner_id"], exact=True).count()
            ],
            "alerts": self.widget.get_by_role("alert").all_text_contents(),
            "viewport": self.width,
            "documentWidth": page.evaluate("document.documentElement.scrollWidth"),
            "requests": list(self.http.requests),
            "unexpected": list(self.http.unexpected),
            "pageErrors": list(self.errors),
            "contrast": "unknown: rendered backgrounds unmeasured",
        }
        path = self.options.screenshots / f"{self.tag}-{state}"
        path.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        self.widget.screenshot(path=str(path.with_suffix(".png")))
        with self.subTest(state=state, measured=True):
            self.assertLessEqual(data["documentWidth"], self.width)
            self.assertEqual(data["unexpected"], [])
            self.assertEqual(data["pageErrors"], [])
            for action in actions:
                self.assert_action(action)
                with self.subTest(floating_visibility=action["name"]):
                    if action["floating"]["parent"]["visibility"] == "hidden":
                        self.assertEqual(
                            action["floating"]["child"]["visibility"], "hidden", action
                        )
                with self.subTest(full_target=action["name"]):
                    self.assertTrue(all(point["hit"] for point in action["points"]), action)
            for text in data["text"]:
                self.assertGreaterEqual(text["size"], 12, text)

    def remove(self, index):
        """Activate the row's real button with Enter and hold its exact DELETE."""
        row = BLOCKS[index]
        button = (
            self.widget.get_by_text(row["blocked_owner_id"], exact=True)
            .locator("..")
            .locator("..")
            .get_by_role("button", name=self.copy["unblock"], exact=True)
        )
        button.press("Enter")
        expect(self.widget.get_by_text(row["blocked_owner_id"], exact=True)).to_have_count(0)
        self.assertIn(row["id"], self.http.held)

    def reply(self, key, status, body=None):
        """Release one exact held boundary response without inventing a204 body."""
        route = self.http.held.pop(key)
        if status == 204:
            route.fulfill(status=status)
        else:
            route.fulfill(status=status, json=body)

    def feedback(self, page, key, state):
        """Capture old feedback too, then require the localized accessible error."""
        expect(self.widget.get_by_role("button")).to_have_count(
            1 if key == "failed to remove the block" else 0
        )
        self.capture(page, state)
        with self.subTest(feedback=state):
            expect(self.widget.get_by_role("alert")).to_contain_text(self.copy[key])

    def parallel_flow(self, page, mode):
        self.remove(0)
        self.remove(1)
        self.capture(page, "both-pending")
        if mode == "success-first":
            self.reply(BLOCKS[1]["id"], 204)
            self.reply(BLOCKS[0]["id"], 503, {"detail": "Synthetic blocks unavailable"})
        else:
            self.reply(BLOCKS[0]["id"], 503, {"detail": "Synthetic blocks unavailable"})
            self.reply(BLOCKS[1]["id"], 404, {"detail": "block not found"})
        expect(self.widget.get_by_text(BLOCKS[0]["blocked_owner_id"], exact=True)).to_be_visible()
        self.capture(page, "rollback")
        with self.subTest(concurrent_rows=True):
            expect(
                self.widget.get_by_text(BLOCKS[1]["blocked_owner_id"], exact=True)
            ).to_have_count(0)
        with self.subTest(localized_error=True):
            expect(self.widget.get_by_role("alert")).to_contain_text(
                self.copy["failed to remove the block"]
            )
        self.remove(0)
        self.reply(BLOCKS[0]["id"], 204)
        self.capture(page, "retried")
        with self.subTest(empty_after_retry=True):
            expect(self.widget.get_by_text(self.copy[KEYS[5]], exact=True)).to_be_visible()

    def run_flow(self, page, mode):
        self.capture(page, "loading")
        self.reply(
            "list",
            503 if mode == "load-error" else 200,
            None if mode == "load-error" else ([] if mode == "empty" else BLOCKS),
        )
        expect(self.widget.get_by_text(self.copy[KEYS[1]], exact=True)).to_have_count(0)
        if mode == "load-error":
            self.feedback(page, "failed to load blocks", "load-error")
        elif mode == "empty":
            expect(self.widget.get_by_text(self.copy[KEYS[5]], exact=True)).to_be_visible()
            self.capture(page, "empty")
            self.scroll_control(page)
        else:
            expect(
                self.widget.get_by_text(BLOCKS[0]["blocked_owner_id"], exact=True)
            ).to_be_visible()
            self.capture(page, "two-rows")
            if mode == "network":
                self.remove(0)
                self.http.held.pop(BLOCKS[0]["id"]).abort("failed")
                expect(
                    self.widget.get_by_text(BLOCKS[0]["blocked_owner_id"], exact=True)
                ).to_be_visible()
                self.capture(page, "network-error")
                with self.subTest(network_feedback=True):
                    expect(self.widget.get_by_role("alert")).to_contain_text(
                        self.copy["failed to remove the block"]
                    )
                self.remove(0)
                self.reply(BLOCKS[0]["id"], 404, {"detail": "block not found"})
                self.capture(page, "network-recovered")
                expect(
                    self.widget.get_by_text(BLOCKS[0]["blocked_owner_id"], exact=True)
                ).to_have_count(0)
            else:
                self.parallel_flow(page, mode)

    def scroll_control(self, page):
        """Body focus keeps the floating action reachable; native Enter returns to top."""
        page.evaluate(
            "document.activeElement.blur(); "
            "window.scrollTo(0, document.documentElement.scrollHeight)"
        )
        button = page.get_by_role(
            "button", name=SCROLL_LABELS[self.locale], exact=True, include_hidden=True
        )
        expect(button).to_be_enabled()
        button.press("Enter")
        try:
            page.wait_for_function("window.scrollY === 0")
        finally:
            data = page.evaluate("""() => ({scrollY,
              reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
              scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
              focused: document.activeElement?.tagName,
              focusedLabel: document.activeElement?.getAttribute('aria-label')})""")
            data["button"] = button.evaluate("""el => ({disabled: el.disabled,
              hidden: el.getAttribute('aria-hidden'), visibility: getComputedStyle(el).visibility,
              top: el.getBoundingClientRect().top})""")
            path = self.options.screenshots / f"{self.tag}-scroll-control.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        expect(button).to_be_disabled()
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def test_blocks_in_six_layouts(self):
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                for mode in (
                    "empty",
                    "load-error",
                    "success-first",
                    "failure-first",
                    "network",
                ):
                    with self.subTest(locale=locale, width=width, mode=mode):
                        page = self.open_blocks(locale, width, mode)
                        try:
                            self.run_flow(page, mode)
                        finally:
                            page.context.close()


if __name__ == "__main__":
    ProfileBlocksTests.options, unittest_args = battles.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
