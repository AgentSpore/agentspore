"""Exercise shared agent-chat history and current-session send recovery."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import USER, AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_chat_ownership import OwnershipHTTP
from test_detail_width import RESOURCES
from test_public_chat_accessibility import FOCUS

RESOURCE = RESOURCES["agent"]
if not isinstance(RESOURCE, dict):
    raise TypeError("Expected agent mapping fixture")
AGENT = dict(RESOURCE, name="Synthetic DM agent", handle="synthetic-dm", skills=[])
AGENT_PATH = f"/agents/{AGENT['id']}"
PAGE = AGENT_PATH + "/chat"
BASE = "/api/v1/chat/dm/synthetic-dm"
SESSION, DETAIL = "synthetic-dm-session", "Synthetic private failure sentinel"
OWNER = dict(USER, name="Synthetic verified visitor")
COPY = {
    "en": (
        "Could not load messages.",
        "Could not load older messages.",
        "Could not refresh messages.",
        "Could not verify your account.",
        "Failed to send",
        "Retry",
        "No messages yet",
        "Checking your account…",
        "human",
        "you",
        "Messages in this chat are public.",
        "Sign in to send messages",
    ),
    "ru": (
        "Не удалось загрузить сообщения.",
        "Не удалось загрузить предыдущие сообщения.",
        "Не удалось обновить сообщения.",
        "Не удалось проверить аккаунт.",
        "Не удалось отправить сообщение",
        "Повторить",
        "Сообщений пока нет",
        "Проверяем аккаунт…",
        "человек",
        "вы",
        "Сообщения в этом чате видны всем.",
        "Войдите, чтобы отправлять сообщения",
    ),
}


def row(identifier, content=None):
    return {
        "id": identifier,
        "from_name": OWNER["name"],
        "from_handle": None,
        "sender_type": "human",
        "content": content or f"Synthetic DM {identifier}",
        "is_read": False,
        "created_at": "2026-10-20T12:00:00Z",
    }


class DirectHTTP(OwnershipHTTP):
    def __init__(self, base_url, group):
        super().__init__(base_url, "dm")
        self.active = "initial" if group == "read" else None
        self.rows = [row(f"dm-{i}") for i in range(50)]
        self.rows[1]["from_name"] = "Different synthetic visitor"
        self.calls, self.empty, self.expected_body = [], False, ""

    def accepts_page(self, request, target):
        if request.method == "GET" and target.path == PAGE and not target.query:
            return True
        query = parse_qs(target.query, keep_blank_values=True)
        if (
            request.method == "GET"
            and target.path in {AGENT_PATH, "/docs/connect", "/docs/glossary"}
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
            and set(query) == {"_rsc"}
            and all(len(v) == 1 for v in query.values())
        ):
            return True
        return AuthHTTP.accepts_page(self, request, target)

    def hold(self, route, operation):
        self.calls.append(
            {
                "operation": operation,
                "method": route.request.method,
                "path": urlparse(route.request.url).path,
                "query": urlparse(route.request.url).query,
                "body": route.request.post_data_json if operation == "post" else None,
            }
        )
        self.pending.append((route, operation))

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/auth/me" and not target.query:
            if request.headers.get("authorization") != "Bearer " + SESSION:
                return self.reject(route)
            self.profile_requests.append("initial")
            return self.hold(route, "profile")
        if request.method == "GET" and target.path == "/api/v1" + AGENT_PATH and not target.query:
            route.fulfill(json=AGENT)
            return
        if request.method == "GET" and target.path == BASE + "/messages":
            return self.history(route)
        if request.method == "POST" and target.path == BASE and not target.query:
            if (
                request.headers.get("authorization") != "Bearer " + SESSION
                or request.headers.get("content-type") != "application/json"
                or request.post_data_json != {"name": OWNER["name"], "content": self.expected_body}
                or self.active != "post"
            ):
                return self.reject(route)
            return self.hold(route, "post")
        return AuthHTTP.route(self, route)

    def history(self, route):
        target = urlparse(route.request.url)
        if route.request.headers.get("authorization") and not self.profile_requests:
            return self.reject(route)
        if target.query == "limit=50":
            operation = "initial"
        elif target.query == "limit=10":
            operation = "poll"
        elif target.query == "limit=50&before=dm-0":
            operation = "older"
        else:
            return self.reject(route)
        if self.active == operation:
            return self.hold(route, operation)
        self.calls.append(
            {
                "operation": operation,
                "query": target.query,
                "method": "GET",
                "path": target.path,
                "body": None,
            }
        )
        data = self.rows[-10:] if operation == "poll" else self.rows
        body = [] if self.empty or operation == "older" else list(reversed(data))
        route.fulfill(json=body)


class PrivateChatStateTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_chat(self, locale, width, group):
        self.locale, self.width, self.errors, self.metrics = locale, width, [], None
        context = self.browser.new_context(viewport={"width": width, "height": 900}, service_workers="block")
        context.add_cookies(
            [
                {
                    "name": "agentspore_locale",
                    "value": locale,
                    "url": self.options.base_url,
                }
            ]
        )
        if group == "send":
            context.add_init_script("localStorage.setItem('access_token', " + json.dumps(SESSION) + ")")
        self.http = DirectHTTP(self.options.base_url, group)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__dmState", lambda key: self.count(key))
        self.page.expose_function("__dmHeld", lambda key: sum(k == key for _, k in self.http.pending))
        self.page.goto(self.options.base_url + PAGE, wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        self.input = self.page.locator("form textarea")
        expect(self.page.get_by_text("Synthetic DM agent", exact=True)).to_be_visible()
        self.settle()

    def settle(self):
        self.page.evaluate("""async () => {
            await Promise.all(document.getAnimations().filter(a=>
                a.effect?.getComputedTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
        }""")

    def count(self, operation):
        return sum(call["operation"] == operation for call in self.http.calls)

    def wait_count(self, operation, count, held=False):
        self.page.evaluate(
            """async ([key,count,held])=>{
            const end=performance.now()+12000;
            while(await window[held?'__dmHeld':'__dmState'](key)!==count){
                if(performance.now()>end)throw Error('Unexpected DM HTTP count');
                await new Promise(resolve=>setTimeout(resolve,25));
            }
        }""",
            [operation, count, held],
        )

    def notice(self, operation):
        return self.page.get_by_role("alert").filter(has_text=COPY[self.locale][operation])

    def fail_operation(self, operation):
        selected = [(route, key) for route, key in self.http.pending if key == operation]
        self.assertTrue(selected)
        for route, key in selected:
            self.http.pending.remove((route, key))
            if self.locale == "en":
                route.fulfill(status=503, json={"detail": DETAIL})
            else:
                route.abort("failed")
        self.http.active = None

    def complete(self, operation):
        route, key = self.http.pending.pop()
        self.assertEqual(key, operation)
        self.http.active = None
        if operation == "profile":
            body = OWNER
        elif operation == "post":
            identifier = f"accepted-{self.count('post')}"
            self.http.rows.append(row(identifier, self.http.expected_body))
            body = {
                "status": "ok",
                "message_id": identifier,
                "agent_name": AGENT["name"],
                "note": "Synthetic accepted message",
            }
        elif operation == "older":
            old = row("dm-older")
            self.http.rows.insert(0, old)
            body = [self.http.rows[1], old]
        else:
            body = list(reversed(self.http.rows[-10:] if operation == "poll" else self.http.rows))
        route.fulfill(json=body)

    def native_retry(self, operation):
        notice = self.notice(operation)
        button = notice.get_by_role("button", name=COPY[self.locale][5], exact=True)
        self.settle()
        for _ in range(self.page.locator("a[href],button,input,textarea").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if button.evaluate("el=>el===document.activeElement"):
                break
        expect(button).to_be_focused()
        self.metrics = button.evaluate(FOCUS)
        self.assertTrue(all(self.metrics[k] for k in ("active", "visible", "painted")))
        self.metrics["geometry"] = notice.evaluate("""el=>{
            const b=el.querySelector('button'),r=b.getBoundingClientRect(),n=el.getBoundingClientRect();
            let left=0,right=innerWidth;
            for(let p=el.parentElement;p;p=p.parentElement){
                if(/hidden|clip|auto|scroll/.test(getComputedStyle(p).overflowX)){
                    const a=p.getBoundingClientRect();left=Math.max(left,a.left);right=Math.min(right,a.right);}}
            const fonts=[],w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
            while(w.nextNode())if(w.currentNode.textContent.trim())
                fonts.push(parseFloat(getComputedStyle(w.currentNode.parentElement).fontSize));
            return {width:r.width,height:r.height,fonts,contained:n.left>=left&&n.right<=right&&r.left>=left&&r.right<=right};
        }""")
        geometry = self.metrics["geometry"]
        self.assertGreaterEqual(geometry["width"], 48)
        self.assertGreaterEqual(geometry["height"], 48)
        self.assertTrue(geometry["fonts"] and min(geometry["fonts"]) >= 14)
        self.assertTrue(geometry["contained"])
        self.page.keyboard.press("Enter")

    def save(self, state):
        self.settle()
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "calls": self.http.calls,
            "profiles": len(self.http.profile_requests),
            "draft": self.input.input_value() if self.input.count() else None,
            "rows": self.main.locator('[id^="dm-"]').all_text_contents(),
            "alerts": self.page.get_by_role("alert").all_text_contents(),
            "retry_metrics": self.metrics,
            "unexpected": self.http.unexpected,
            "page_errors": self.errors,
            "document_width": self.page.evaluate("document.documentElement.scrollWidth"),
        }
        stem = f"private-chat-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertLessEqual(record["document_width"], self.width)
        expect(self.page.get_by_text(DETAIL, exact=False)).to_have_count(0)

    def shared_history(self):
        expect(self.page.get_by_text(COPY[self.locale][10], exact=True)).to_be_visible()
        self.assertGreaterEqual(self.page.get_by_text(COPY[self.locale][8], exact=True).count(), 1)
        expect(self.main.get_by_text(COPY[self.locale][9], exact=True)).to_have_count(0)

    def initial_failure(self):
        self.wait_count("initial", 1, held=True)
        expect(self.main.get_by_text(COPY[self.locale][6], exact=True)).to_have_count(0)
        self.save("initial-pending")
        self.fail_operation("initial")
        try:
            expect(self.notice(0)).to_be_visible()
            expect(self.main.get_by_text(COPY[self.locale][6], exact=True)).to_have_count(0)
        finally:
            self.save("initial-error")
        self.http.active = "initial"
        self.native_retry(0)
        self.wait_count("initial", 1, held=True)
        self.assertEqual(self.count("initial"), 2)
        self.complete("initial")
        expect(self.main.get_by_text("Synthetic DM dm-49", exact=True)).to_be_visible()
        self.shared_history()

    def older_failure(self):
        self.http.active = "older"
        self.main.evaluate("""async el=>{el.scrollTo({top:1});await new Promise(requestAnimationFrame);
            el.scrollTo({top:0});await new Promise(requestAnimationFrame);} """)
        self.wait_count("older", 1, held=True)
        self.main.dispatch_event("scroll")
        self.assertEqual(self.count("older"), 1)
        self.fail_operation("older")
        expect(self.notice(1)).to_be_visible()
        for item in self.http.rows:
            expect(self.main.get_by_text(item["content"], exact=True)).to_have_count(1)
        self.main.dispatch_event("scroll")
        self.assertEqual(self.count("older"), 1)
        self.save("older-error")
        self.http.active = "older"
        self.native_retry(1)
        self.wait_count("older", 1, held=True)
        self.complete("older")
        expect(self.main.get_by_text("Synthetic DM dm-older", exact=True)).to_have_count(1)
        expect(self.main.get_by_text("Synthetic DM dm-0", exact=True)).to_have_count(1)
        self.main.dispatch_event("scroll")
        self.assertEqual(self.count("older"), 2)

    def poll_failure(self):
        before = self.count("poll")
        self.http.active = "poll"
        self.wait_count("poll", 1, held=True)
        self.fail_operation("poll")
        expect(self.notice(2)).to_be_visible()
        expect(self.main.get_by_text("Synthetic DM dm-49", exact=True)).to_have_count(1)
        self.save("poll-error")
        self.http.active = "poll"
        self.native_retry(2)
        self.wait_count("poll", 1, held=True)
        self.assertEqual(self.count("poll"), before + 2)
        self.http.rows.append(row("polled"))
        self.complete("poll")
        expect(self.main.get_by_text("Synthetic DM polled", exact=True)).to_have_count(1)
        self.wait_count("poll", before + 3)
        expect(self.notice(2)).to_have_count(0)
        self.save("poll-recovered")

    def read_flow(self):
        self.initial_failure()
        self.older_failure()
        self.poll_failure()
        self.http.empty = True
        self.page.reload(wait_until="domcontentloaded")
        expect(self.main.get_by_text(COPY[self.locale][6], exact=True)).to_be_visible()
        expect(self.page.get_by_text(COPY[self.locale][10], exact=True)).to_be_visible()
        expect(self.page.get_by_role("link", name=COPY[self.locale][11], exact=True)).to_be_visible()
        self.save("empty")

    def profile_flow(self):
        self.wait_count("profile", 2, held=True)
        expect(self.page.get_by_role("status").filter(has_text=COPY[self.locale][7])).to_be_visible()
        expect(self.input).to_have_count(0)
        self.save("profile-checking")
        self.fail_operation("profile")
        expect(self.notice(3)).to_be_visible()
        self.save("profile-error")
        self.native_retry(3)
        self.wait_count("profile", 1, held=True)
        self.assertEqual(len(self.http.profile_requests), 3)
        expect(self.input).to_have_count(0)
        self.complete("profile")
        expect(self.input).to_be_visible()
        self.shared_history()

    def submit(self, text):
        self.http.expected_body, self.http.active = text, "post"
        self.input.fill(text)
        self.input.press("Enter")
        self.wait_count("post", 1, held=True)

    def send_flow(self):
        self.submit("Submitted A")
        self.input.fill("Newer B")
        self.input.press("Enter")
        self.assertEqual(self.count("post"), 1)
        self.save("post-pending")
        self.complete("post")
        try:
            expect(self.input).to_have_value("Newer B")
            expect(self.main.get_by_text("Submitted A", exact=True)).to_have_count(1)
        finally:
            self.save("post-success-newer-draft")
        self.submit("Newer B")
        self.fail_operation("post")
        expect(self.notice(4)).to_be_visible()
        expect(self.input).to_have_value("Newer B")
        self.input.fill("Current C")
        expect(self.notice(4)).to_have_count(0)
        self.submit("Current C")
        self.fail_operation("post")
        expect(self.notice(4)).to_be_visible()
        self.save("send-error")
        self.http.expected_body, self.http.active = "Current C", "post"
        self.native_retry(4)
        self.wait_count("post", 1, held=True)
        self.assertEqual(self.count("post"), 4)
        self.complete("post")
        expect(self.input).to_have_value("")
        expect(self.main.get_by_text("Current C", exact=True)).to_have_count(1)
        self.save("send-recovered")

    def run_flow(self, group):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_chat(locale, width, group)
                        if group == "read":
                            self.read_flow()
                        else:
                            self.profile_flow()
                            self.send_flow()
                    finally:
                        self.doCleanups()

    def test_public_history_read_recovery(self):
        self.run_flow("read")

    def test_verified_profile_and_current_draft_recovery(self):
        self.run_flow("send")


if __name__ == "__main__":
    PrivateChatStateTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
