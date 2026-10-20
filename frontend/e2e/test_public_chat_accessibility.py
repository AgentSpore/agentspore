"""Exercise public-chat geometry, native keyboard actions and filter semantics."""

import json
import re
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_public_chat_states import STREAM

SESSION = "synthetic-accessibility"
DRAFT = "A synthetic draft across filters"
LONG = "synthetic_long_identifier_" * 9
COPY = {
    "en": (
        "Edit",
        "Delete",
        "Cancel (Esc)",
        "All",
        "Idea",
        "Alert",
        "No messages of this type.",
        "The chat is quiet",
        "Send",
        "Message",
        "Save",
    ),
    "ru": (
        "Изменить",
        "Удалить",
        "Отмена (Esc)",
        "Все",
        "Идея",
        "Предупреждение",
        "Сообщений этого типа нет.",
        "В чате тихо",
        "Отправить",
        "Сообщение",
        "Сохранить",
    ),
}


def messages():
    """Actual history DTOs; adjacent human rows deliberately form one group."""
    base = dict(
        agent_id=None,
        agent_name=USER["name"],
        sender_user_id=USER["id"],
        specialization="user",
        sender_type="user",
        message_type="text",
    )
    rows = [
        dict(
            base, id="a11y-owned", content="Synthetic editable message", ts="2026-10-20T09:00:00Z"
        ),
        dict(
            base,
            id="a11y-edited",
            content="Synthetic grouped edited message",
            edited_at="2026-10-20T09:02:00Z",
            ts="2026-10-20T09:01:00Z",
        ),
        dict(
            base, id="a11y-deleted", content="[deleted]", is_deleted=True, ts="2026-10-20T09:02:00Z"
        ),
        dict(
            base,
            id="a11y-agent",
            agent_id="synthetic-agent",
            agent_name="Synthetic agent",
            sender_user_id=None,
            specialization="programmer",
            sender_type="agent",
            message_type="idea",
            ts="2026-10-20T09:03:00Z",
            content=f"Synthetic idea with `inline code`.\n\n```text\n{LONG}\n```\n\n[https://example.invalid/{LONG}](https://example.invalid/{LONG})",
        ),
    ]
    return list(reversed(rows))


class AccessibilityHTTP(AuthHTTP):
    """Accept inspected loopback chat reads/writes; reject unknown outward requests."""

    def __init__(self, base_url, empty):
        super().__init__(base_url)
        self.empty = empty

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == "/chat" and not target.query:
            return True
        query = parse_qs(target.query)
        if (
            request.method == "GET"
            and target.path in {"/agents/synthetic-agent", "/docs/connect", "/docs/glossary"}
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and set(query) == {"_rsc"}
            and len(query["_rsc"]) == 1
        ):
            return True
        return super().accepts_page(request, target)

    def mutation(self, route):
        request, target = route.request, urlparse(route.request.url)
        valid = {
            "POST": "/api/v1/chat/human-message",
            "DELETE": "/api/v1/chat/human-messages/a11y-owned",
        }
        if request.method not in valid or target.path != valid[request.method] or target.query:
            return self.reject(route)
        body = request.post_data_json if request.method == "POST" else None
        if request.method == "POST" and body != {"content": "Keyboard synthetic send"}:
            return self.reject(route)
        self.requests.append({"method": request.method, "body": body})
        route.fulfill(json={"status": "ok", "message_id": "a11y-owned"})

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if not target.path.startswith("/api/v1/chat/"):
            return super().route(route)
        if request.headers.get("authorization") != "Bearer " + SESSION:
            return self.reject(route)
        if (
            request.method == "GET"
            and target.path == "/api/v1/chat/messages"
            and target.query == "limit=50"
        ):
            route.fulfill(json=[] if self.empty else messages())
            return
        self.mutation(route)


METRICS = """() => {
    const main=document.querySelector('#main-content'), form=document.querySelector('form');
    const roots=[main.previousElementSibling,main,form].filter(Boolean), text=[], controls=[], bounds=[];
    const visible=el=>{ for(let n=el;n;n=n.parentElement) {
        const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)return false;
    }return el.getClientRects().length>0;};
    for(const root of roots){
        const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
        while(walker.nextNode()) {const n=walker.currentNode,el=n.parentElement;
            if(n.textContent.trim()&&!el.closest('svg')&&visible(el))text.push({text:n.textContent.trim(),size:parseFloat(getComputedStyle(el).fontSize)});
        }
        for(const el of root.querySelectorAll('button,a[href],textarea'))if(visible(el)){
            const r=el.getBoundingClientRect();controls.push({tag:el.tagName,label:el.title||el.textContent.trim()||el.getAttribute('aria-label')||el.type,x:r.x,width:r.width,height:r.height});
        }
    }
    for(const el of main.querySelectorAll('[data-message-id]')){
        const r=el.getBoundingClientRect();bounds.push({id:el.dataset.messageId,x:r.x,right:r.right,width:r.width});
    }
    return {text,controls,bounds,document_width:document.documentElement.scrollWidth};
}"""
FOCUS = """el => {
    const s=getComputedStyle(el),canvas=document.createElement('canvas');canvas.width=canvas.height=1;
    const ctx=canvas.getContext('2d');ctx.fillStyle=s.outlineColor;ctx.fillRect(0,0,1,1);
    const alpha=ctx.getImageData(0,0,1,1).data[3],width=parseFloat(s.outlineWidth);
    return {active:el===document.activeElement,visible:el.matches(':focus-visible'),
        outline:s.outlineStyle,width,color:s.outlineColor,alpha,
        painted:s.outlineStyle!=='none'&&s.outlineStyle!=='hidden'&&width>0&&alpha>0};
}"""


class PublicChatAccessibilityTests(BrowserFixture, unittest.TestCase):
    """Use native Tab/Enter/Escape on the actual compiled public chat."""

    options: Namespace

    def open_chat(self, locale, width, empty=False):
        self.locale, self.width, self.errors, self.focus = locale, width, [], []
        self.context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        self.addCleanup(self.context.close)
        self.context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        self.context.add_init_script(STREAM)
        self.context.add_init_script(
            "localStorage.setItem('access_token', " + json.dumps(SESSION) + ")"
        )
        self.http = AccessibilityHTTP(self.options.base_url, empty)
        self.context.route("**/*", self.http.route)
        self.page = self.context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.options.base_url + "/chat", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.input = self.page.locator("form textarea")
        expect(self.input).to_be_visible()
        if not empty:
            expect(self.main.get_by_text(str(USER["name"]), exact=True)).to_have_count(1)
            expect(self.main.get_by_role("link", name="Synthetic agent", exact=True)).to_have_count(
                1
            )
            expect(self.main.locator('[data-message-id="a11y-owned"]')).to_have_count(1)
        self.filters = self.main.locator("xpath=preceding-sibling::div[1]")

    def row(self):
        return self.main.locator('[data-message-id="a11y-owned"]')

    def button(self, index):
        return self.row().get_by_role("button", name=COPY[self.locale][index], exact=True)

    def tab_to(self, control):
        for _ in range(self.page.locator("a[href],button,input,textarea").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if control.evaluate("el=>el===document.activeElement"):
                break
        expect(control).to_be_focused()
        if control.count() and control.evaluate("el=>el.closest('[data-message-id]')!==null"):
            expect(control.locator("..")).to_have_css("opacity", "1")
        self.focus.append(control.evaluate(FOCUS))

    def save(self, state):
        self.page.evaluate("document.fonts.ready")
        metrics = self.page.evaluate(METRICS)
        record = dict(
            metrics,
            pointer=self.page.evaluate(
                "({coarse:matchMedia('(pointer: coarse)').matches,hover_none:matchMedia('(hover: none)').matches})"
            ),
            locale=self.locale,
            width=self.width,
            state=state,
            focus=self.focus,
            requests=self.http.requests,
            unexpected=self.http.unexpected,
            page_errors=self.errors,
        )
        stem = f"public-accessibility-{self.locale}-{self.width}-{self._testMethodName}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(
            record["pointer"], {"coarse": self.width < 1280, "hover_none": self.width < 1280}
        )
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertLessEqual(metrics["document_width"], self.width)
        return metrics

    def assert_geometry(self, states):
        violations = []
        for name, metrics in states:
            violations.extend((name, "text", t) for t in metrics["text"] if t["size"] < 12)
            violations.extend(
                (name, "target", c)
                for c in metrics["controls"]
                if c["width"] < 44 or c["height"] < 44
            )
            violations.extend(
                (name, "bounds", b)
                for b in metrics["bounds"]
                if b["x"] < 0 or b["right"] > self.width
            )
        self.assertEqual(violations, [], json.dumps(violations, ensure_ascii=False))

    def geometry(self):
        if self.width == 1280:
            self.row().hover()
        states = [("populated", self.save("populated"))]
        expect(self.button(0).locator("..")).to_have_css("opacity", "1")
        self.tab_to(self.button(0))
        self.page.keyboard.press("Enter")
        expect(self.input).to_have_value("Synthetic editable message")
        states.append(("editing", self.save("editing")))
        self.assert_geometry(states)
        expect(self.input).to_have_accessible_name(COPY[self.locale][9])
        expect(self.page.locator("form button[type='submit']")).to_have_accessible_name(
            COPY[self.locale][10]
        )

    def filter_to(self, index):
        button = self.filters.get_by_role(
            "button", name=re.compile(re.escape(COPY[self.locale][index]))
        )
        self.tab_to(button)
        self.page.keyboard.press("Enter")
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(self.input).to_have_value(DRAFT)

    def keyboard_filters(self):
        self.tab_to(self.button(0))
        self.save("edit-focused")
        self.page.keyboard.press("Enter")
        expect(self.input).to_be_focused()
        expect(self.input).to_have_value("Synthetic editable message")
        self.page.keyboard.press("Escape")
        expect(self.input).to_be_focused()
        expect(self.input).to_have_value("")
        self.tab_to(self.button(0))
        self.page.keyboard.press("Enter")
        cancel = self.page.get_by_role("button", name=COPY[self.locale][2], exact=True)
        self.tab_to(cancel)
        self.save("cancel-focused")
        self.page.keyboard.press("Enter")
        expect(cancel).to_have_count(0)
        expect(self.input).to_be_focused()
        self.input.fill(DRAFT)
        self.filter_to(4)
        expect(self.main.locator("[data-message-id]")).to_have_count(1)
        expect(self.main.locator('[data-message-id="a11y-agent"]')).to_have_count(1)
        self.save("idea-filter")
        self.filter_to(5)
        expect(self.main.get_by_text(COPY[self.locale][6], exact=True)).to_be_visible()
        expect(self.main.locator("[data-message-id]")).to_have_count(0)
        self.save("empty-filter")
        self.filter_to(3)
        expect(self.main.locator("[data-message-id]")).to_have_count(4)
        self.input.fill("Keyboard synthetic send")
        send = self.page.locator("form button[type='submit']")
        expect(send).to_have_accessible_name(COPY[self.locale][8])
        self.tab_to(send)
        self.save("send-focused")
        self.page.keyboard.press("Enter")
        expect(self.input).to_have_value("")
        self.tab_to(self.button(1))
        self.save("delete-focused")
        self.page.keyboard.press("Enter")
        expect(self.row().get_by_role("button")).to_have_count(0)
        expect(self.row().get_by_text("[deleted]", exact=True)).to_have_count(1)
        self.assertEqual(
            self.http.requests,
            [
                {"method": "POST", "body": {"content": "Keyboard synthetic send"}},
                {"method": "DELETE", "body": None},
            ],
        )
        self.save("deleted")
        self.assertTrue(
            all(f["active"] and f["visible"] and f["painted"] for f in self.focus), self.focus
        )

    def empty(self):
        expect(self.main.get_by_text(COPY[self.locale][7], exact=True)).to_be_visible()
        expect(self.main.locator("[data-message-id]")).to_have_count(0)
        expect(self.main.get_by_text(COPY[self.locale][6], exact=True)).to_have_count(0)
        self.input.fill(DRAFT)
        states = [("empty", self.save("empty"))]
        self.assert_geometry(states)

    def matrix(self, scenario):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_chat(locale, width, scenario == "empty")
                        getattr(self, scenario)()
                    finally:
                        self.doCleanups()

    def test_focus_verifier_rejects_unpainted_controls(self):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        context.route("**/*", lambda route: route.abort("blockedbyclient"))
        page = context.new_page()
        page.set_content("""<button style="outline:2px solid transparent">Transparent</button>
            <div style="box-shadow:0 0 0 4px black"><button style="outline:none">Static shadow</button></div>
            <button style="outline:2px solid rgb(120, 60, 240)">Visible</button>""")
        results = []
        for label in ("Transparent", "Static shadow", "Visible"):
            page.keyboard.press("Tab")
            control = page.get_by_role("button", name=label, exact=True)
            expect(control).to_be_focused()
            results.append(dict(label=label, **control.evaluate(FOCUS)))
        (self.options.screenshots / "focus-verifier-controls.json").write_text(
            json.dumps(results, indent=2)
        )
        self.assertTrue(all(r["active"] and r["visible"] for r in results))
        self.assertEqual([r["painted"] for r in results], [False, False, True])

    def test_populated_and_editing_controls_text_and_bounds(self):
        self.matrix("geometry")

    def test_native_keyboard_mutations_and_filters_preserve_draft(self):
        self.matrix("keyboard_filters")

    def test_successful_empty_chat_remains_distinct_and_usable(self):
        self.matrix("empty")


if __name__ == "__main__":
    PublicChatAccessibilityTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
