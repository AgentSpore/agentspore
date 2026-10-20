"""Measure shared agent-chat geometry and native keyboard composer transitions."""

import json
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect
from test_private_chat_states import (
    AGENT,
    AGENT_PATH,
    OWNER,
    PAGE,
    SESSION,
    DirectHTTP,
    row,
)
from test_public_chat_accessibility import FOCUS

HANDLE = "synthetic-research-agent-01234567890123456789"
NAME = "Synthetic Research Agent — помощник для обсуждения исследовательских идей"
SENDER = "SyntheticVisitorWithLongUnbrokenName0123456789012"
TOKEN = "synthetic_unbroken_identifier_0123456789_" * 5
URL = "https://example.invalid/" + TOKEN
REPLY = "Full synthetic reply preview: " + TOKEN
CODE = TOKEN + "\n" + TOKEN
MARKDOWN = f"A multiline message.\n\nSecond paragraph with `inline code`.\n\n```text\n{CODE}\n```\n\n[{URL}]({URL})"
BASE = f"/api/v1/chat/dm/{HANDLE}"
LABELS = {"en": ("Message", "Send"), "ru": ("Сообщение", "Отправить")}
BODY = "Keyboard first line\nKeyboard second line"


def geometry_rows():
    rows = [
        dict(row("target", REPLY), from_name=SENDER, from_handle=HANDLE, sender_type="agent"),
        dict(row("grouped", MARKDOWN), from_name=SENDER),
        dict(row("multiline", "First human paragraph\n\nSecond human paragraph"), from_name=SENDER),
        dict(row("reply", "Reply message body"), reply_to={"id": "target", "content": REPLY}),
    ]
    return [dict(item, created_at=f"2026-10-20T09:{i:02}:00Z") for i, item in enumerate(rows)]


class GeometryHTTP(DirectHTTP):
    def __init__(self, base_url):
        super().__init__(base_url, "send")
        self.rows, self.expected_body, self.entry = geometry_rows(), BODY, None

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/auth/me" and not target.query:
            if request.headers.get("authorization") != "Bearer " + SESSION:
                return self.reject(route)
            self.profile_requests.append("initial")
            route.fulfill(json=OWNER)
            return
        if request.method == "GET" and target.path == "/api/v1" + AGENT_PATH and not target.query:
            if self.entry == "loading":
                return self.hold(route, "agent")
            route.fulfill(json=dict(AGENT, name=NAME, handle=HANDLE))
            return
        if request.method == "GET" and target.path == BASE + "/messages":
            return self.history(route)
        if request.method == "POST" and target.path == BASE and not target.query:
            if (
                request.headers.get("authorization") != "Bearer " + SESSION
                or request.headers.get("content-type") != "application/json"
                or request.post_data_json != {"name": OWNER["name"], "content": BODY}
            ):
                return self.reject(route)
            return self.hold(route, "post")
        return super().route(route)


METRICS = """()=>{
    const targets=[],text=[],clipped=[],pre=[];
    const painted=el=>{for(let n=el;n;n=n.parentElement){const s=getComputedStyle(n);
        if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0||s.clipPath==='inset(50%)')return false;
        const c=s.clip.match(/-?[0-9.]+px/g)?.map(parseFloat);
        if(/^(absolute|fixed)$/.test(s.position)&&c?.length===4&&(c[2]<=c[0]||c[1]<=c[3]))return false;}return true;};
    const clips=el=>{const a=[{left:0,right:innerWidth}];for(let n=el.parentElement;n;n=n.parentElement){
        if(/^(hidden|clip|auto|scroll)$/.test(getComputedStyle(n).overflowX)){
            const r=n.getBoundingClientRect(),left=r.left+n.clientLeft;a.push({left,right:left+n.clientWidth});}}return a;};
    const all=[...document.querySelectorAll('main button,form button,form textarea,a[href="PROFILE"],a[href="/login"],a[href="/"]')];
    for(const el of all){if(!painted(el)||el.closest('header'))continue;const r=el.getBoundingClientRect();
        targets.push({name:el.getAttribute('aria-label')||el.getAttribute('title')||el.textContent.trim(),
            tag:el.tagName,disabled:el.disabled||false,width:r.width,height:r.height,
            contained:clips(el).every(c=>r.left>=c.left&&r.right<=c.right)});}
    const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    while(w.nextNode()){const n=w.currentNode,el=n.parentElement;
        if(!n.textContent.trim()||el.closest('header,svg,[aria-hidden="true"],script,style,next-route-announcer')||!painted(el))continue;
        text.push({text:n.textContent.trim(),size:parseFloat(getComputedStyle(el).fontSize)});
        if(el.closest('pre'))continue;
        const r=document.createRange();r.selectNodeContents(n);
        for(const b of r.getClientRects())for(const c of clips(el))
            if(b.left<c.left||b.right>c.right)clipped.push({text:n.textContent.trim(),left:b.left,right:b.right,clip:c});
    }
    for(const el of document.querySelectorAll('main pre')){const r=el.getBoundingClientRect();
        pre.push({text:el.textContent,width:r.width,client:el.clientWidth,scroll:el.scrollWidth,
            contained:clips(el).every(c=>r.left>=c.left&&r.right<=c.right),tabindex:el.tabIndex});}
    return {targets,text,clipped,pre,document_width:document.documentElement.scrollWidth};
}""".replace("PROFILE", AGENT_PATH)
FORM = """el=>{const r=el.getBoundingClientRect(),clips=[];
    for(let n=el.parentElement;n;n=n.parentElement){if(/^(hidden|clip|auto|scroll)$/.test(getComputedStyle(n).overflowY)){
        const b=n.getBoundingClientRect(),top=b.top+n.clientTop;clips.push({top,bottom:top+n.clientHeight});}}
    return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height,viewport:innerHeight,clips};}"""


class PrivateChatAccessibilityTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_chat(self, locale, width):
        self.locale, self.width, self.errors, self.states = locale, width, [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
            storage_state={
                "cookies": [],
                "origins": [
                    {"origin": self.options.base_url, "localStorage": [{"name": "access_token", "value": SESSION}]}
                ],
            },
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = GeometryHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__geometryWrites", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + PAGE, wait_until="domcontentloaded")
        self.main, self.input = self.page.locator("#main-content"), self.page.locator("form textarea")
        self.form = self.page.locator("form")
        expect(self.input).to_be_visible()
        expect(self.main.locator('[id^="dm-"]')).to_have_count(4)
        self.settle()

    def settle(self):
        self.page.evaluate("""async()=>{
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
                Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        }""")

    def save(self, state, extra=None):
        self.settle()
        metrics = self.page.evaluate(METRICS)
        record = {
            "state": state,
            "locale": self.locale,
            "width": self.width,
            "metrics": metrics,
            "extra": extra,
            "calls": self.http.calls,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "draft": self.input.input_value() if self.input.count() else None,
        }
        self.states.append(record)
        stem = f"private-geometry-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        return metrics

    def geometry_issues(self, metrics):
        issues = []
        for target in metrics["targets"]:
            if target["width"] < 48 or target["height"] < 48:
                issues.append({"target_size": target})
            if not target["name"]:
                issues.append({"target_name": target})
            if not target["contained"]:
                issues.append({"target_clipped": target})
        for text in metrics["text"]:
            if text["size"] < 12:
                issues.append({"small_text": text})
        issues.extend({"text_clipped": item} for item in metrics["clipped"])
        for pre in metrics["pre"]:
            if not pre["contained"] or pre["scroll"] > pre["client"]:
                issues.append({"unwrapped_code": pre})
        if metrics["document_width"] > self.width:
            issues.append({"document_width": metrics["document_width"]})
        return issues

    def form_state(self):
        self.settle()
        return self.form.evaluate(FORM)

    def form_issues(self, form):
        issues = []
        if form["top"] < 0 or form["bottom"] > form["viewport"] or form["left"] < 0 or form["right"] > self.width:
            issues.append({"form_outside_viewport": form})
        for clip in form["clips"]:
            if form["top"] < clip["top"] or form["bottom"] > clip["bottom"]:
                issues.append({"form_clipped": form})
        return issues

    def tab_to(self, target):
        for _ in range(self.page.locator("a[href],button,textarea,input,[tabindex='0']").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        focus = target.evaluate(FOCUS)
        self.assertTrue(all(focus[key] for key in ("active", "visible", "painted")), focus)
        return focus

    def geometry_flow(self):
        code = self.main.locator("pre")
        expect(code).to_have_text(CODE + "\n")
        link = self.main.get_by_role("link", name=URL, exact=True)
        expect(link).to_have_attribute("href", URL)
        reply = self.main.locator("#dm-reply").get_by_text(REPLY, exact=True)
        expect(reply).to_have_text(REPLY)
        preview = reply.evaluate(
            """el=>{const s=getComputedStyle(el);return {text:el.textContent,display:s.display,
                clamp:s.webkitLineClamp,height:el.getBoundingClientRect().height,
                line_height:parseFloat(s.lineHeight),scroll:el.scrollHeight,client:el.clientHeight};}"""
        )
        populated = self.save("populated", {"preview": preview})
        issues = self.geometry_issues(populated)
        self.assertEqual(preview["text"], REPLY)
        self.assertGreater(preview["height"], 0)
        self.assertGreater(preview["line_height"], 0)
        self.assertLessEqual(preview["height"], 2 * preview["line_height"])
        self.assertLess(preview["client"], preview["scroll"])
        expect(self.page.get_by_text(NAME, exact=True)).to_have_count(1)
        expect(self.page.get_by_text("@" + HANDLE, exact=True)).to_have_count(1)
        self.input.fill("A multiline composer\n" * 5 + TOKEN)
        self.input.focus()
        form = self.form_state()
        issues.extend(self.form_issues(form))
        multiline = self.save("multiline", {"form": form})
        issues.extend(self.geometry_issues(multiline))
        issues.extend(self.entry_states())
        self.assertEqual(issues, [])

    def entry_states(self):
        self.page.evaluate("""()=>{localStorage.removeItem('access_token');
            window.dispatchEvent(new Event('storage'));}""")
        self.http.empty = True
        self.page.reload(wait_until="domcontentloaded")
        empty = "No messages yet" if self.locale == "en" else "Сообщений пока нет"
        expect(self.main.get_by_text(empty, exact=True)).to_be_visible()
        signin = self.page.get_by_role(
            "link",
            name="Sign in to send messages" if self.locale == "en" else "Войдите, чтобы отправлять сообщения",
            exact=True,
        )
        signin_focus = self.tab_to(signin)
        issues = self.geometry_issues(self.save("anonymous-empty", {"focus": signin_focus}))
        self.http.entry = "loading"
        self.page.reload(wait_until="domcontentloaded")
        loading = "Loading chat..." if self.locale == "en" else "Загрузка чата..."
        expect(self.main.get_by_text(loading, exact=True)).to_be_visible()
        issues.extend(self.geometry_issues(self.save("agent-loading")))
        route, operation = self.http.pending.pop()
        self.assertEqual(operation, "agent")
        route.fulfill(status=404, json={"detail": "Synthetic absent agent"})
        expect(
            self.main.get_by_text("Agent not found" if self.locale == "en" else "Агент не найден", exact=True)
        ).to_be_visible()
        back = self.main.get_by_role("link")
        back_focus = self.tab_to(back)
        issues.extend(self.geometry_issues(self.save("agent-missing", {"focus": back_focus})))
        return issues

    def keyboard_flow(self):
        profile_focus = self.tab_to(self.page.locator(f'a[href="{AGENT_PATH}"]'))
        reply = self.main.locator("#dm-reply").get_by_role("button").filter(has_text=REPLY)
        reply_focus = self.tab_to(reply)
        self.page.keyboard.press("Enter")
        target = self.main.locator("#dm-target")
        expect(target).to_be_focused()
        expect(target).to_be_in_viewport()
        self.settle()
        box = target.bounding_box()
        self.assertIsNotNone(box)
        if box is None:
            self.fail("Focused reply target has no bounding box")
        history_box = self.main.bounding_box()
        self.assertIsNotNone(history_box)
        if history_box is None:
            self.fail("History has no bounding box")
        self.assertLess(box["y"], history_box["y"] + history_box["height"])
        self.assertGreater(box["y"] + box["height"], history_box["y"])
        self.save("reply-jumped", {"profile_focus": profile_focus, "reply_focus": reply_focus})
        input_focus = self.tab_to(self.input)
        expect(self.page.get_by_role("textbox", name=LABELS[self.locale][0], exact=True)).to_be_focused()
        self.page.keyboard.type("Keyboard first line")
        self.page.keyboard.press("Shift+Enter")
        self.page.keyboard.type("Keyboard second line")
        expect(self.input).to_have_value(BODY)
        self.assertEqual([c for c in self.http.calls if c["operation"] == "post"], [])
        form = self.form_state()
        issues = self.form_issues(form)
        self.save("keyboard-composer", {"focus": input_focus, "form": form})
        send = self.form.get_by_role("button", name=LABELS[self.locale][1], exact=True)
        send_focus = self.tab_to(send)
        self.page.keyboard.press("Enter")
        self.page.evaluate("""async()=>{const end=performance.now()+7000;
            while(await window.__geometryWrites()!==1){if(performance.now()>end)throw Error('Expected held DM POST');
                await new Promise(requestAnimationFrame);}}""")
        expect(send).to_be_disabled()
        pending = self.save("send-pending", {"focus": send_focus})
        issues.extend(self.geometry_issues(pending))
        self.assertEqual(len([c for c in self.http.calls if c["operation"] == "post"]), 1)
        route, operation = self.http.pending.pop()
        self.assertEqual(operation, "post")
        self.http.rows.append(row("accepted", BODY))
        route.fulfill(json={"status": "ok", "message_id": "accepted", "agent_name": NAME, "note": "Synthetic accepted"})
        expect(self.input).to_have_value("")
        expect(self.main.get_by_text(BODY, exact=True)).to_have_count(1)
        self.save("send-complete")
        self.assertEqual(issues, [])

    def run_flow(self, kind):
        for locale in LABELS:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_chat(locale, width)
                        if kind == "geometry":
                            self.geometry_flow()
                        else:
                            self.keyboard_flow()
                    finally:
                        self.doCleanups()

    def test_painted_text_control_respects_zero_area_clip_and_native_focus(self):
        context = self.browser.new_context(viewport={"width": 320, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto("about:blank")
        page.set_content("""<style>p{font-size:10px}.hidden,.focusable{clip-path:inset(50%)}
            .focusable:focus{clip-path:none;outline:2px solid white}</style>
            <p>Visible tiny text</p><p class="hidden">Hidden tiny text</p>
            <p class="focusable" tabindex="0">Focusable tiny text</p>""")
        before = page.evaluate(METRICS)
        self.assertEqual([item["text"] for item in before["text"]], ["Visible tiny text"])
        self.assertEqual([item["size"] for item in before["text"]], [10])
        page.keyboard.press("Tab")
        expect(page.get_by_text("Focusable tiny text", exact=True)).to_be_focused()
        after = page.evaluate(METRICS)
        self.assertEqual([item["text"] for item in after["text"]], ["Visible tiny text", "Focusable tiny text"])
        self.assertEqual([item["size"] for item in after["text"]], [10, 10])
        result = {
            "before": before,
            "after_native_focus": after,
            "focused_clip_path": page.get_by_text("Focusable tiny text", exact=True).evaluate(
                "el=>getComputedStyle(el).clipPath"
            ),
        }
        self.assertEqual(result["focused_clip_path"], "none")
        (self.options.screenshots / "painted-text-controls.json").write_text(json.dumps(result, indent=2))

    def test_populated_geometry_and_multiline_composer(self):
        self.run_flow("geometry")

    def test_native_reply_jump_and_keyboard_send(self):
        self.run_flow("keyboard")


if __name__ == "__main__":
    PrivateChatAccessibilityTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
