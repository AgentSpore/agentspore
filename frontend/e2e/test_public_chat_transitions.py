"""Prove residual public-chat pending, scroll-anchor and native-input transitions."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import USER, AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_public_chat_accessibility import FOCUS, METRICS
from test_public_chat_history_auth import CURSOR_QUERY, OLDER, ROWS, TOKEN_A
from test_public_chat_states import STREAM

HISTORY = [dict(item, sender_user_id=None, agent_name="Synthetic visitor") for item in ROWS]
OLD = dict(OLDER, sender_user_id=None, content="An older page\n\n" + "Earlier synthetic paragraph.\n\n" * 6)
COPY = {
    "en": ("Loading messages...", "Loading older messages", "The chat is quiet"),
    "ru": ("Загрузка сообщений...", "Загрузка предыдущих сообщений", "В чате тихо"),
}
PREFIX = "Keyboard first line\nKeyboard second line\n"
CAPPED = PREFIX + "x" * (2000 - len(PREFIX))


class TransitionHTTP(AuthHTTP):
    def __init__(self, base_url, hold_initial):
        super().__init__(base_url)
        self.hold_initial, self.calls, self.writes = hold_initial, [], []

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/chat" and not target.query:
            return True
        query = parse_qs(target.query, keep_blank_values=True)
        if (
            request.method == "GET"
            and target.path in {"/docs/connect", "/docs/glossary"}
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and set(query) == {"_rsc"}
            and all(len(v) == 1 for v in query.values())
        ):
            return True
        return super().accepts_page(request, target)

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/auth/me" and not target.query:
            if request.headers.get("authorization") != "Bearer " + TOKEN_A:
                return self.reject(route)
            route.fulfill(json=USER)
            return
        if request.method == "GET" and target.path == "/api/v1/chat/messages":
            if (
                target.query not in {"limit=50", CURSOR_QUERY}
                or request.headers.get("authorization") != "Bearer " + TOKEN_A
            ):
                return self.reject(route)
            self.calls.append({"method": "GET", "query": target.query, "authorization_matched": True})
            if self.hold_initial or target.query == CURSOR_QUERY:
                self.pending.append(route)
            else:
                route.fulfill(json=HISTORY)
            return
        if request.method == "POST" and target.path == "/api/v1/chat/human-message" and not target.query:
            if (
                request.headers.get("authorization") != "Bearer " + TOKEN_A
                or request.headers.get("content-type") != "application/json"
                or request.post_data_json != {"content": CAPPED}
            ):
                return self.reject(route)
            self.writes.append({"path": target.path, "body": request.post_data_json, "authorization_matched": True})
            self.pending.append(route)
            return
        return super().route(route)

    def close(self, context):
        while self.pending:
            self.pending.pop().abort("failed")
        context.close()


SCROLL = """el=>({top:el.scrollTop,height:el.scrollHeight,client:el.clientHeight,
    distance:el.scrollHeight-el.scrollTop-el.clientHeight,dpr:devicePixelRatio})"""
ANCHOR = """main=>{
    const b=main.getBoundingClientRect();
    const rows=[...main.querySelectorAll('[data-message-id]')];
    const row=rows.find(el=>{const r=el.getBoundingClientRect();return r.top>=b.top&&r.bottom<=b.bottom;});
    if(!row)throw Error('No fully visible history anchor');
    return {id:row.dataset.messageId,y:row.getBoundingClientRect().top-b.top,dpr:devicePixelRatio};
}"""
FORM = """el=>{const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom,
    left:r.left,right:r.right,viewport:innerHeight,scroll:el.scrollWidth,client:el.clientWidth};}"""


class PublicChatTransitionTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_chat(self, locale, width, history):
        self.locale, self.width, self.errors, self.diagnostics = locale, width, [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.add_init_script(STREAM)
        context.add_init_script("localStorage.setItem('access_token', " + json.dumps(TOKEN_A) + ")")
        self.http = TransitionHTTP(self.options.base_url, history)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__transitionHeld", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + "/chat", wait_until="domcontentloaded")
        self.main, self.input = self.page.locator("#main-content"), self.page.locator("form textarea")
        self.form = self.page.locator("form")
        expect(self.input).to_be_visible()
        self.settle()

    def settle(self):
        self.page.evaluate("""async()=>{
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
                Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        }""")

    def held(self):
        self.page.evaluate("""async()=>{const end=performance.now()+7000;
            while(await window.__transitionHeld()!==1){if(performance.now()>end)throw Error('Expected one held transition');
                await new Promise(requestAnimationFrame);}}""")
        self.assertEqual(len(self.http.pending), 1)

    def complete(self, body):
        self.held()
        self.http.pending.pop().fulfill(json=body)
        self.settle()

    def scroll_settle(self, bottom=False):
        return self.main.evaluate(
            """async(el,bottom)=>{
            const end=performance.now()+7000;let old=null,stable=0;
            while(performance.now()<end){await new Promise(requestAnimationFrame);
                const state={top:el.scrollTop,height:el.scrollHeight,client:el.clientHeight,
                    distance:el.scrollHeight-el.scrollTop-el.clientHeight,dpr:devicePixelRatio};
                stable=old===state.top?stable+1:0;old=state.top;
                if(stable>=8&&(!bottom||state.distance<=1/devicePixelRatio))return state;}
            throw Error('Native history scroll did not settle');
        }""",
            bottom,
        )

    def emit(self, identifier):
        message = dict(HISTORY[0], id=identifier, content=f"Synthetic live {identifier}", ts="2026-10-20T12:00:00Z")
        self.page.evaluate("message=>window.__chatStream.onmessage({data:JSON.stringify(message)})", message)
        expect(self.main.locator(f'[data-message-id="{identifier}"]')).to_have_count(1)

    def anchor_y(self, identifier):
        return self.main.locator(f'[data-message-id="{identifier}"]').evaluate(
            "el=>el.getBoundingClientRect().top-document.querySelector('#main-content').getBoundingClientRect().top"
        )

    def metrics(self):
        self.settle()
        metrics = self.page.evaluate(METRICS)
        metrics["form"] = self.form.evaluate(FORM)
        return metrics

    def geometry_issues(self, metrics):
        issues = []
        for text in metrics["text"]:
            if text["size"] < 12:
                issues.append({"small_text": text})
        for control in metrics["controls"]:
            if control["width"] < 44 or control["height"] < 44:
                issues.append({"small_target": control})
        form = metrics["form"]
        if (
            form["top"] < 0
            or form["bottom"] > form["viewport"]
            or form["left"] < 0
            or form["right"] > self.width
            or form["scroll"] > form["client"]
        ):
            issues.append({"form_bounds": form})
        if metrics["document_width"] > self.width:
            issues.append({"document_width": metrics["document_width"]})
        return issues

    def save(self, state, extra=None):
        metrics = self.metrics()
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "extra": extra,
            "diagnostics": self.diagnostics,
            "scroll": self.main.evaluate(SCROLL),
            "requests": self.http.calls,
            "writes": self.http.writes,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "draft": self.input.input_value(),
        }
        stem = f"public-transitions-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        return metrics

    def history_flow(self):
        self.held()
        expect(self.main.get_by_text(COPY[self.locale][0], exact=True)).to_be_visible()
        expect(self.main.get_by_text(COPY[self.locale][2], exact=True)).to_have_count(0)
        issues = self.geometry_issues(self.save("initial-pending"))
        self.complete(HISTORY)
        self.scroll_settle(bottom=True)
        self.main.evaluate("el=>{el.scrollTop=30}")
        self.held()
        expect(self.main.get_by_text(COPY[self.locale][1], exact=True)).to_be_visible()
        self.emit("during-older")
        self.scroll_settle()
        before = self.main.evaluate(ANCHOR)
        self.assertGreater(self.main.evaluate("el=>el.scrollTop"), 0)
        issues.extend(self.geometry_issues(self.save("older-pending", {"anchor": before})))
        self.complete([OLD])
        after = self.anchor_y(before["id"])
        delta = abs(after - before["y"])
        self.diagnostics.append(
            {"anchor_before": before, "after_y": after, "delta": delta, "allowed_physical_pixel": 1 / before["dpr"]}
        )
        if delta > 1 / before["dpr"]:
            issues.append({"pagination_anchor_shift": self.diagnostics[-1]})
        self.save("older-anchor", {"before": before, "after_y": after})
        count = len(self.http.calls)
        self.main.evaluate("el=>{el.scrollTop=1}")
        self.scroll_settle()
        self.main.evaluate("el=>{el.scrollTop=0}")
        self.scroll_settle()
        self.assertEqual(len(self.http.calls), count)
        self.save("older-added")
        away = self.main.evaluate(ANCHOR)
        self.assertGreater(self.main.evaluate(SCROLL)["distance"], 100)
        self.emit("away")
        self.scroll_settle()
        away_after = self.anchor_y(away["id"])
        self.diagnostics.append({"away": away, "after_y": away_after})
        if abs(away_after - away["y"]) > 1 / away["dpr"]:
            issues.append({"away_anchor_shift": self.diagnostics[-1]})
        self.main.evaluate("el=>{el.scrollTop=el.scrollHeight-el.clientHeight-40}")
        near = self.main.evaluate(SCROLL)
        self.assertGreater(near["distance"], 0)
        self.assertLess(near["distance"], 100)
        self.emit("near")
        bottom = self.scroll_settle(bottom=True)
        self.save("live-follow", {"near": near, "bottom": bottom})
        self.assertEqual(issues, [])

    def native_input_flow(self):
        expect(self.main.locator("[data-message-id]")).to_have_count(50)
        for _ in range(self.page.locator("a[href],button,textarea,input").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if self.input.evaluate("el=>el===document.activeElement"):
                break
        expect(self.input).to_be_focused()
        focus = self.input.evaluate(FOCUS)
        self.assertTrue(all(focus[key] for key in ("active", "visible", "painted")))
        self.page.keyboard.type("Keyboard first line")
        self.page.keyboard.press("Shift+Enter")
        self.page.keyboard.type("Keyboard second line")
        self.page.keyboard.press("Shift+Enter")
        expect(self.input).to_have_value(PREFIX)
        self.assertEqual(self.http.writes, [])
        self.page.keyboard.insert_text("x" * (2000 - len(PREFIX)))
        expect(self.input).to_have_value(CAPPED)
        self.page.keyboard.type("Z")
        expect(self.input).to_have_value(CAPPED)
        self.assertEqual(len(self.input.input_value()), 2000)
        self.assertEqual(self.http.writes, [])
        issues = self.geometry_issues(self.save("native-cap", {"focus": focus, "length": 2000}))
        self.page.keyboard.press("Enter")
        self.held()
        self.assertEqual(len(self.http.writes), 1)
        self.assertEqual(self.http.writes[0]["body"], {"content": CAPPED})
        self.complete({"status": "ok"})
        expect(self.input).to_have_value("")
        self.save("native-sent")
        self.assertEqual(issues, [])

    def run_flow(self, kind):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_chat(locale, width, kind == "history")
                        if kind == "history":
                            self.history_flow()
                        else:
                            self.native_input_flow()
                    finally:
                        self.doCleanups()

    def test_history_pending_anchor_exhaustion_and_live_scroll(self):
        self.run_flow("history")

    def test_native_multiline_cap_and_exact_send(self):
        self.run_flow("input")


if __name__ == "__main__":
    PublicChatTransitionTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
