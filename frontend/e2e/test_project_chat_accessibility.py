"""Measure actual project Discussion geometry and native keyboard transitions."""

import json
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_chat_ownership import (
    ACCOUNT_A,
    ACCOUNT_B,
    COPY,
    DISPLAY,
    OwnershipHTTP,
    PROJECT_ID,
    synthetic_session,
    chat_rows,
)
from test_public_chat_accessibility import FOCUS
from test_public_chat_states import STREAM

TOKEN_TEXT = "synthetic_unbroken_identifier_" * 7
URL = "https://example.invalid/" + TOKEN_TEXT
MARKDOWN = f"A synthetic idea with `inline code`.\n\n```text\n{TOKEN_TEXT}\n```\n\n[{URL}]({URL})"
LABELS = {
    "en": ("Reply", "Edit", "Delete", "Message", "Send", "Save", "Cancel"),
    "ru": ("Ответить", "Изменить", "Удалить", "Сообщение", "Отправить", "Сохранить", "Отмена"),
}
BASE = f"/api/v1/chat/project/{PROJECT_ID}"


def discussion_rows():
    base = chat_rows("project")[-1]
    base["sender_type"] = "human"
    foreign = dict(base, sender_user_id=ACCOUNT_B, sender_name="Foreign synthetic owner")
    return [
        dict(base, id="geometry-own", content="Geometry own message"),
        dict(
            base,
            id="geometry-grouped",
            content="Grouped followup",
            created_at="2026-10-20T09:01:00Z",
            edited_at="2026-10-20T09:02:00Z",
        ),
        dict(foreign, id="geometry-foreign", content="Foreign message"),
        dict(
            base,
            id="geometry-agent",
            sender_user_id=None,
            sender_name=TOKEN_TEXT,
            sender_type="agent",
            message_type="idea",
            content=MARKDOWN,
        ),
        dict(
            base,
            id="geometry-reply",
            content="Reply preview message",
            reply_to={"id": "geometry-agent", "content": TOKEN_TEXT},
        ),
        dict(base, id="geometry-deleted", content="[deleted]", is_deleted=True),
    ]


class GeometryHTTP(OwnershipHTTP):
    """Keep accepted route resources; expose only exact synthetic chat contracts."""

    def __init__(self, base_url):
        super().__init__(base_url, "project")
        self.rows, self.writes = discussion_rows(), []

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/auth/me" and not target.query:
            if request.headers.get("authorization") != "Bearer " + synthetic_session("initial"):
                return self.reject(route)
            route.fulfill(json=dict(USER, id=ACCOUNT_A, name=DISPLAY))
            return
        if (
            request.method == "GET"
            and target.path == BASE + "/messages"
            and target.query == "limit=50"
        ):
            route.fulfill(json=list(reversed(self.rows)))
            return
        if request.method in {"POST", "PATCH", "DELETE"}:
            return self.mutation(route)
        return super().route(route)

    def mutation(self, route):
        request, target = route.request, urlparse(route.request.url)
        method = request.method
        path = BASE + "/human-messages" + ("" if method == "POST" else "/geometry-own")
        body = (
            {"content": "Geometry amended message"}
            if method == "PATCH"
            else {"name": DISPLAY, "content": "Geometry keyboard message", "message_type": "text"}
        )
        if (
            target.path != path
            or target.query
            or request.headers.get("authorization") != "Bearer " + synthetic_session("initial")
        ):
            return self.reject(route)
        if method != "DELETE" and request.post_data_json != body:
            return self.reject(route)
        self.writes.append(
            {
                "method": method,
                "path": path,
                "body": request.post_data_json if method != "DELETE" else None,
            }
        )
        if method == "PATCH":
            self.rows[0]["content"] = "Geometry amended message"
        if method == "DELETE":
            self.rows[0]["content"], self.rows[0]["is_deleted"] = "[deleted]", True
        route.fulfill(json={"status": "ok", "message_id": "geometry-own"})


METRICS = """card=>{
    const controls=[],text=[],overflow=[],pre=[];
    const painted=el=>{for(let n=el;n;n=n.parentElement){const s=getComputedStyle(n);
        if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)return false;}return true;};
    const clips=el=>{const a=[];for(let n=el.parentElement;n;n=n.parentElement){
        if(['hidden','clip','auto','scroll'].includes(getComputedStyle(n).overflowX)){
            const r=n.getBoundingClientRect(),left=r.left+n.clientLeft;a.push({left,right:left+n.clientWidth});}}return a;};
    for(const el of card.querySelectorAll('button,textarea')){const r=el.getBoundingClientRect();
        controls.push({label:el.title||el.getAttribute('aria-label')||el.textContent.trim(),width:r.width,height:r.height,painted:painted(el)});}
    const w=document.createTreeWalker(card,NodeFilter.SHOW_TEXT);
    while(w.nextNode()){const n=w.currentNode,el=n.parentElement;if(!n.textContent.trim()||el.closest('svg')||!painted(el))continue;
        text.push({text:n.textContent.trim(),size:parseFloat(getComputedStyle(el).fontSize)});
        if(el.closest('pre'))continue;
        const range=document.createRange();range.selectNodeContents(n);
        for(const r of range.getClientRects())for(const c of clips(el))
            if(r.left<c.left||r.right>c.right)overflow.push({text:n.textContent.trim(),left:r.left,right:r.right,clip:c});
    }
    for(const el of card.querySelectorAll('pre')){const r=el.getBoundingClientRect();
        pre.push({width:r.width,scroll:el.scrollWidth,client:el.clientWidth,text:el.textContent,clips:clips(el),left:r.left,right:r.right});}
    return {controls,text,overflow,pre,document_width:document.documentElement.scrollWidth};
}"""
COMPOSER = """el=>{const r=el.getBoundingClientRect(),clips=[];
    for(let n=el.parentElement;n;n=n.parentElement){const s=getComputedStyle(n);
        if(['hidden','clip','auto','scroll'].includes(s.overflowY)){const b=n.getBoundingClientRect(),top=b.top+n.clientTop;clips.push({top,bottom:top+n.clientHeight});}}
    return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height,viewport:innerHeight,clips};}"""


class ProjectChatAccessibilityTests(BrowserFixture, unittest.TestCase):
    """Separate aggregate geometry from keyboard/composer diagnostics."""

    options: Namespace

    def open_chat(self, locale, width):
        self.locale, self.width, self.errors, self.diagnostics = locale, width, [], []
        self.names = []
        self.context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        self.context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.context.add_init_script(STREAM)
        self.context.add_init_script(
            "localStorage.setItem('access_token', " + json.dumps(synthetic_session("initial")) + ")"
        )
        self.http = GeometryHTTP(self.options.base_url)
        self.addCleanup(self.http.close, self.context)
        self.context.route("**/*", self.http.route)
        self.page = self.context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(
            self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded"
        )
        self.tab = self.page.get_by_role("button", name=COPY[locale][2], exact=True)
        self.tab.click()
        self.main = self.page.locator("#main-content")
        self.panel = self.main.locator(".chat-panel")
        self.card = self.panel.locator("..")
        self.input = self.card.locator("form textarea")
        expect(self.input).to_be_visible()
        expect(self.panel.locator(".group")).to_have_count(6)
        self.settle_animations()

    def settle_animations(self):
        self.page.evaluate("""async()=>{
            const finite=document.getAnimations().filter(a=>a instanceof CSSAnimation&&
                Number.isFinite(a.effect?.getComputedTiming().iterations));
            await Promise.all(finite.map(a=>a.finished));
        }""")

    def row(self, index=0):
        return self.panel.locator(".group").nth(index)

    def action(self, index):
        return self.row().get_by_role("button", name=LABELS[self.locale][index], exact=True)

    def tab_to(self, control):
        for _ in range(
            self.page.locator("a[href],button,input,textarea,[tabindex='0']").count() * 2 + 4
        ):
            self.page.keyboard.press("Tab")
            if control.evaluate("el=>el===document.activeElement"):
                break
        expect(control).to_be_focused()
        return control.evaluate(FOCUS)

    def save(self, state, metrics=None):
        self.page.evaluate("document.fonts.ready")
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": metrics,
            "diagnostics": self.diagnostics,
            "names": self.names,
            "writes": self.http.writes,
            "page_errors": self.errors,
            "unexpected": self.http.unexpected,
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
            "pointer": self.page.evaluate(
                "({coarse:matchMedia('(pointer: coarse)').matches,hover_none:matchMedia('(hover: none)').matches})"
            ),
        }
        stem = f"project-accessibility-{self.locale}-{self.width}-{self._testMethodName}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertLessEqual(record["document_width"], self.width)
        self.assertEqual(
            record["pointer"], {"coarse": self.width < 1280, "hover_none": self.width < 1280}
        )

    def geometry_state(self, state):
        metrics = self.card.evaluate(METRICS)
        box = self.tab.bounding_box()
        metrics["discussion_tab"] = box
        self.save(state, metrics)
        return metrics

    def snapshot_violations(self, metrics):
        violations = [
            ("target", c) for c in metrics["controls"] if c["width"] < 44 or c["height"] < 44
        ]
        violations.extend(("font", t) for t in metrics["text"] if t["size"] < 12)
        violations.extend(("clip", o) for o in metrics["overflow"])
        if self.width < 1280:
            violations.extend(("touch-hidden", c) for c in metrics["controls"] if not c["painted"])
        for pre in metrics["pre"]:
            violations.extend(
                ("code-clip", pre)
                for c in pre["clips"]
                if pre["left"] < c["left"] or pre["right"] > c["right"]
            )
        return violations

    def code_access(self):
        focus = self.tab_to(self.panel.get_by_role("link", name=URL, exact=True))
        self.assertTrue(all(focus[k] for k in ("active", "visible", "painted")))
        pre = self.panel.locator("pre")
        self.assertEqual(pre.inner_text().strip(), TOKEN_TEXT)
        self.assertTrue(pre.evaluate("el=>el.scrollWidth<=el.clientWidth"))

    def geometry(self):
        snapshots = []
        for index in range(6):
            if self.width == 1280:
                self.row(index).hover()
            snapshots.append(self.card.evaluate(METRICS))
        snapshots.append(self.geometry_state("populated"))
        self.action(1).click()
        expect(self.input).to_have_value("Geometry own message")
        snapshots.append(self.geometry_state("editing"))
        self.input.press("Escape")
        self.action(0).click()
        snapshots.append(self.geometry_state("reply"))
        held = []
        url = self.options.base_url + BASE + "/messages?limit=50"
        reject = self.http.reject
        self.page.route(url, lambda r: held.append(r) if r.request.method == "GET" else reject(r))
        self.page.reload(wait_until="domcontentloaded")
        with self.page.expect_request(url):
            self.tab.click()
        expect(self.panel.get_by_role("status")).to_be_visible()
        self.settle_animations()
        snapshots.append(self.geometry_state("loading"))
        self.assertEqual(len(held), 1)
        held[0].fulfill(json=[])
        empty = "No messages yet" if self.locale == "en" else "Сообщений пока нет"
        expect(self.panel.get_by_text(empty, exact=True)).to_be_visible()
        snapshots.append(self.geometry_state("empty"))
        violations = [v for m in snapshots for v in self.snapshot_violations(m)]
        tab = snapshots[-1]["discussion_tab"]
        self.assertIsNotNone(tab)
        if tab is not None and (tab["width"] < 44 or tab["height"] < 44):
            violations.append(("discussion-target", tab))
        self.assertEqual(violations, [], json.dumps(violations, ensure_ascii=False))

    def composer_state(self, state, control=None):
        self.page.evaluate(
            "async()=>{for(const frame of [0,1])await new Promise(requestAnimationFrame)}"
        )
        if control is None:
            control = self.input
        focus = control.evaluate(FOCUS)
        box = self.card.locator("form").evaluate(COMPOSER)
        shown = control.evaluate(
            """el=>{for(let n=el;n;n=n.parentElement)if(Number(getComputedStyle(n).opacity)===0)return false;return true;}"""
        )
        self.diagnostics.append({"state": state, "focus": focus, "shown": shown, "composer": box})
        self.save(state)

    def reply_preview(self):
        preview = self.card.locator("form p[tabindex='0']")
        focus = self.tab_to(preview)
        self.page.keyboard.press("End")
        scroll = preview.evaluate(
            "async el=>{for(const frame of [0,1])await new Promise(requestAnimationFrame);return el.scrollTop}"
        )
        self.diagnostics[-1]["reply_preview"] = {
            "focus": focus,
            "scroll_top": scroll,
            "text": preview.inner_text(),
        }
        self.assertEqual(preview.inner_text(), MARKDOWN)
        self.assertTrue(all(focus[k] for k in ("active", "visible", "painted")))
        self.assertGreater(scroll, 0)

    def keyboard(self):
        self.tab_to(self.action(1))
        self.composer_state("edit-focused", self.action(1))
        self.page.keyboard.press("Enter")
        expect(self.input).to_have_value("Geometry own message")
        self.composer_state("editor-open")
        self.page.keyboard.press("Escape")
        self.composer_state("escaped")
        self.tab_to(self.row(3).get_by_role("button", name=LABELS[self.locale][0], exact=True))
        self.page.keyboard.press("Enter")
        self.composer_state("reply-open")
        self.reply_preview()
        cancel = self.card.locator("form button[type='button']")
        self.tab_to(cancel)
        self.names.append(cancel.get_attribute("aria-label"))
        self.page.keyboard.press("Enter")
        self.composer_state("cancelled")
        self.tab_to(self.action(1))
        self.page.keyboard.press("Enter")
        self.input.fill("Geometry amended message")
        save = self.card.locator("form button[type='submit']")
        self.tab_to(save)
        self.names.append(save.get_attribute("aria-label"))
        self.page.keyboard.press("Enter")
        expect(self.panel.get_by_text("Geometry amended message", exact=True)).to_have_count(1)
        self.tab_to(self.action(2))
        self.page.keyboard.press("Enter")
        expect(self.row().get_by_role("button")).to_have_count(0)
        self.tab_to(self.input)
        self.input.fill("Geometry keyboard message")
        send = self.card.locator("form button[type='submit']")
        self.tab_to(send)
        self.page.keyboard.press("Enter")
        expect(self.input).to_have_value("")
        self.save("sent")
        self.assertEqual([w["method"] for w in self.http.writes], ["PATCH", "DELETE", "POST"])
        failures = []
        for d in self.diagnostics:
            if not all(d["focus"][k] for k in ("active", "visible", "painted")) or not d["shown"]:
                failures.append(d)
            if d["state"] != "edit-focused":
                b = d["composer"]
                if (
                    b["top"] < 0
                    or b["bottom"] > b["viewport"]
                    or any(b["top"] < c["top"] or b["bottom"] > c["bottom"] for c in b["clips"])
                ):
                    failures.append(d)
        self.assertEqual(failures, [], json.dumps(failures))
        expect(self.input).to_have_accessible_name(LABELS[self.locale][3])
        expect(send).to_have_accessible_name(LABELS[self.locale][4])
        self.assertEqual(self.names, [LABELS[self.locale][6], LABELS[self.locale][5]])
        self.code_access()

    def matrix(self, scenario):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_chat(locale, width)
                        getattr(self, scenario)()
                    finally:
                        self.doCleanups()

    def test_discussion_targets_text_and_long_content_stay_contained(self):
        self.matrix("geometry")

    def test_native_keyboard_actions_leave_composer_reachable(self):
        self.matrix("keyboard")


if __name__ == "__main__":
    ProjectChatAccessibilityTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
