"""Exercise battle controls against a local production build with synthetic HTTP data.

Run with the existing Playwright environment; no service credentials are used:
python test_battles_accessibility.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
"""

import argparse
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

AGENT_ID = "00000000-0000-4000-8000-000000000011"
LABELS = {
    "en": {
        "completed": "Completed",
        "category": "Category",
        "difficulty": "Difficulty",
        "agent": "Your agent",
        "ready": "Battle-ready",
        "withdraw": "Withdrawn from battles",
        "add": "+ Add criterion",
        "remove": "Remove criterion 2",
        "hard": "Hard",
    },
    "ru": {
        "completed": "Завершены",
        "category": "Категория",
        "difficulty": "Сложность",
        "agent": "Ваш агент",
        "ready": "Готов к боям",
        "withdraw": "Не участвует в боях",
        "add": "+ Добавить критерий",
        "remove": "Удалить критерий 2",
        "hard": "Сложная",
    },
}


TEXT_METRICS = """() => {
    const canvas = document.createElement('canvas').getContext('2d');
    const rgba = color => {
        canvas.clearRect(0, 0, 1, 1); canvas.fillStyle = color; canvas.fillRect(0, 0, 1, 1);
        return [...canvas.getImageData(0, 0, 1, 1).data].map(v => v / 255);
    };
    const over = (fg, bg) => fg.slice(0, 3).map((v, i) => v * fg[3] + bg[i] * (1 - fg[3]));
    const luminance = rgb => rgb.map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
        .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    const walker = document.createTreeWalker(document.querySelector('main'), NodeFilter.SHOW_TEXT);
    const records = [];
    while (walker.nextNode()) {
        const node = walker.currentNode, element = node.parentElement;
        const text = node.textContent.trim();
        if (!text || !element.checkVisibility({opacityProperty: true, visibilityProperty: true}) ||
            element.closest('[aria-hidden="true"], :disabled')) continue;
        const closed = element.closest('details:not([open])');
        if (closed && !closed.querySelector('summary')?.contains(element)) continue;
        const style = getComputedStyle(element), ancestors = [], unknown = [];
        for (let current = element; current; current = current.parentElement) {
            const css = getComputedStyle(current); ancestors.unshift(css.backgroundColor);
            if (css.backgroundImage !== 'none' || Number(css.opacity) !== 1) unknown.push('paint');
        }
        let background = [1, 1, 1];
        for (const color of ancestors) background = over(rgba(color), background);
        const foreground = over(rgba(style.color), background);
        const light = luminance(foreground), dark = luminance(background);
        records.push({text: text.slice(0, 100), size: parseFloat(style.fontSize), unknown,
            ratio: (Math.max(light, dark) + .05) / (Math.min(light, dark) + .05)});
    }
    return records;
}"""


class BattleAccessibilityTests(unittest.TestCase):
    """Keep the rendered hit areas and keyboard effects of battle controls usable."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=cls.options.executable)
        cls.options.screenshots.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def open_page(self, path, locale, width):
        context = self.browser.new_context(viewport={"width": width, "height": 900}, locale=locale)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.add_init_script("localStorage.setItem('access_token', 'synthetic-browser-test')")
        page = context.new_page()
        page.set_default_timeout(7000)
        self.unexpected = []
        page.route("**/*", self.route_request)
        self.addCleanup(context.close)
        page.goto(self.options.base_url + path)
        expect(page.locator("main h1")).to_be_visible()
        return page

    def route_request(self, route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.netloc != urlparse(self.options.base_url).netloc:
            self.unexpected.append("external request")
            route.abort()
            return
        if not parsed.path.startswith("/api/"):
            route.continue_()
            return
        path = parsed.path
        agent = {
            "id": AGENT_ID,
            "name": "Synthetic battle agent",
            "handle": "synthetic-battle",
            "specialization": "programmer",
            "model_provider": "synthetic",
            "model_name": "synthetic",
            "is_active": True,
            "is_hosted": False,
            "karma": 0,
            "projects_created": 0,
            "github_connected": False,
            "created_at": None,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        }
        payloads = {
            "/api/v1/auth/me": {
                "id": "synthetic-owner",
                "email": "synthetic@example.invalid",
                "name": "Synthetic owner",
                "avatar_url": None,
                "token_balance": 0,
                "is_admin": False,
            },
            "/api/v1/users/me/external-agents": [agent],
            "/api/v1/battles/tasks": {
                "minimum_pool_size": 20,
                "cooldown_days": 30,
                "pools": [
                    {
                        "category": "backend",
                        "difficulty": "hard",
                        "fresh_count": 24,
                        "challenge_available": True,
                    }
                ],
            },
            "/api/v1/battles": [],
            "/api/v1/battles/leaderboard": {"contenders": [], "approaches": []},
            "/api/v1/battles/contenders": [],
            f"/api/v1/agents/{AGENT_ID}": {**agent, "available_for_battles": False},
        }
        if path.endswith("/battle-availability") and request.method == "PATCH":
            route.fulfill(json={"agent_id": AGENT_ID, **request.post_data_json})
        elif path in payloads and request.method == "GET":
            route.fulfill(json=payloads[path])
        else:
            self.unexpected.append(f"{request.method} {path}")
            route.fulfill(status=503, json={"detail": "No synthetic fixture for this endpoint"})

    def assert_targets(self, controls):
        self.assertGreater(controls.count(), 0)
        for index in range(controls.count()):
            control = controls.nth(index)
            control.scroll_into_view_if_needed()
            bounds = control.bounding_box()
            with self.subTest(control=control.inner_text().strip()):
                self.assertIsNotNone(bounds)
                self.assertGreaterEqual(bounds["height"], 44)
                self.assertGreaterEqual(bounds["width"], 44)

    def save_screen(self, page, scenario, locale, width):
        page.evaluate("window.scrollTo(0, 0)")
        page.evaluate(
            "() => new Promise(resolve => "
            "requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        page.screenshot(
            path=str(self.options.screenshots / f"{scenario}-{locale}-{width}.png"), full_page=True
        )
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
        self.assertEqual(self.unexpected, [])
        metrics = page.evaluate(TEXT_METRICS)
        (self.options.screenshots / f"{scenario}-{locale}-{width}.json").write_text(
            json.dumps(metrics)
        )
        for record in metrics:
            with self.subTest(text=record["text"]):
                self.assertGreaterEqual(record["size"], 12)
                self.assertEqual(record["unknown"], [])
                self.assertGreaterEqual(record["ratio"], 4.5)

    def test_status_filters_are_large_and_keyboard_selection_updates_results(self):
        for locale in LABELS:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    page = self.open_page("/battles", locale, width)
                    completed = page.get_by_role(
                        "button", name=LABELS[locale]["completed"], exact=True
                    )
                    for _ in range(60):
                        page.keyboard.press("Tab")
                        if completed.evaluate("el => el === document.activeElement"):
                            break
                    expect(completed).to_be_focused()
                    box = completed.bounding_box()
                    with self.subTest(native_tab_visibility=True):
                        self.assertGreaterEqual(box["x"], 0)
                        self.assertLessEqual(box["x"] + box["width"], width)
                    with page.expect_request(
                        lambda r: (
                            urlparse(r.url).path == "/api/v1/battles"
                            and parse_qs(urlparse(r.url).query).get("status") == ["completed"]
                        )
                    ):
                        page.keyboard.press("Enter")
                    with self.subTest(selected_state=True):
                        expect(completed).to_have_attribute("aria-pressed", "true", timeout=1000)
                    self.assert_targets(completed.locator("..").get_by_role("button"))
                    toggle = page.locator("main button[aria-pressed]").last
                    self.assert_targets(toggle)
                    self.save_screen(page, "filters", locale, width)
                    page.context.close()

    def assert_keyboard_access(self, page, control, tag):
        for _ in range(80):
            page.keyboard.press("Tab")
            if control.evaluate("el => el === document.activeElement"):
                break
        expect(control).to_be_focused()
        page.evaluate(
            "() => new Promise(resolve => "
            "requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        result = control.evaluate("""el => {
            const box = el.getBoundingClientRect();
            const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
            return {visible: box.x >= 0 && box.y >= 0 &&
                    box.right <= innerWidth && box.bottom <= innerHeight,
                centerHit: hit === el || el.contains(hit),
                hitText: hit?.textContent.trim().slice(0, 100)};
        }""")
        page.screenshot(path=str(self.options.screenshots / f"keyboard-{tag}.png"))
        with self.subTest(native_keyboard=tag):
            self.assertTrue(result["visible"], result)
            self.assertTrue(result["centerHit"], result)

    def test_challenge_theme_and_availability_controls_remain_operable(self):
        for locale in LABELS:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    page = self.open_page("/battles/new", locale, width)
                    page.get_by_label(LABELS[locale]["agent"], exact=True).select_option(AGENT_ID)
                    avatar = page.get_by_role("img", name="Synthetic battle agent", exact=True)
                    expect(avatar).to_be_visible()
                    self.assertGreaterEqual(
                        avatar.locator("span").evaluate(
                            "el => parseFloat(getComputedStyle(el).fontSize)"
                        ),
                        12,
                    )
                    ready = page.get_by_role("button", name=LABELS[locale]["ready"], exact=True)
                    expect(ready).to_be_visible()
                    page.evaluate("document.fonts.ready")
                    page.evaluate(
                        "Promise.all(document.getAnimations().filter(a => "
                        "a.effect?.getComputedTiming().iterations !== Infinity)"
                        ".map(a => a.finished.catch(() => {})))"
                    )
                    page.evaluate("document.activeElement?.blur()")
                    self.assert_keyboard_access(page, ready, f"{locale}-{width}-availability")
                    for name in ("category", "difficulty"):
                        group = page.get_by_role(
                            "radiogroup", name=LABELS[locale][name], exact=True
                        )
                        self.assert_keyboard_access(
                            page, group.get_by_role("radio").last, f"{locale}-{width}-{name}"
                        )
                    with page.expect_response(
                        lambda r: r.url.endswith("/battle-availability")
                    ) as response:
                        ready.focus()
                        page.keyboard.press("Enter")
                    self.assertEqual(
                        response.value.request.post_data_json, {"available_for_battles": True}
                    )
                    for group_name in ("category", "difficulty"):
                        group = page.get_by_role(
                            "radiogroup", name=LABELS[locale][group_name], exact=True
                        )
                        radio = group.get_by_role("radio").last
                        radio.focus()
                        page.keyboard.press("Space")
                        expect(radio).to_have_attribute("aria-checked", "true")
                        self.assert_targets(group.get_by_role("radio"))
                    self.assert_targets(ready.locator("..").get_by_role("button"))
                    self.save_screen(page, "challenge", locale, width)
                    page.context.close()

    def test_task_criteria_and_difficulty_controls_remain_operable(self):
        for locale in LABELS:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    page = self.open_page("/battles/tasks/new", locale, width)
                    add = page.get_by_role("button", name=LABELS[locale]["add"], exact=True)
                    add.focus()
                    page.keyboard.press("Enter")
                    descriptions = page.locator("textarea")
                    expect(descriptions).to_have_count(3)
                    remove = page.get_by_role("button", name=LABELS[locale]["remove"], exact=True)
                    remove.focus()
                    page.keyboard.press("Enter")
                    expect(descriptions).to_have_count(2)
                    difficulty = page.get_by_role(
                        "radiogroup", name=LABELS[locale]["difficulty"], exact=True
                    )
                    hard = difficulty.get_by_role("radio").last
                    hard.focus()
                    page.keyboard.press("Space")
                    expect(hard).to_have_attribute("aria-checked", "true")
                    self.assert_targets(difficulty.get_by_role("radio"))
                    self.assert_targets(add)
                    self.save_screen(page, "task", locale, width)
                    page.context.close()


def parse_options():
    """Require an explicit local stand and existing browser, avoiding live-service probes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--screenshots", type=Path, required=True)
    options, remaining = parser.parse_known_args()
    target = urlparse(options.base_url)
    if target.scheme != "http" or target.hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("--base-url must be an HTTP loopback URL")
    if (
        target.username
        or target.password
        or target.path not in ("", "/")
        or target.query
        or target.fragment
    ):
        parser.error("--base-url must contain only a loopback origin")
    options.base_url = options.base_url.rstrip("/")
    return options, remaining


if __name__ == "__main__":
    BattleAccessibilityTests.options, unittest_args = parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
