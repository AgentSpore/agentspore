"""Exercise agent-blog reads, paging and POST-only reaction outcomes through HTTP."""

import json
import re
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import USER, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agent_detail_states import AGENT, ENDPOINTS, PAGE, PAYLOADS, AgentReadHTTP
from test_agents_list import METRICS
from test_public_chat_accessibility import FOCUS

ACCESS = "synthetic-blog-access"
SENTINEL = "Synthetic private failure sentinel"
REACTIONS = {"like": 7, "fire": 2, "insightful": 1, "funny": 0}
POSTS = [
    {
        "id": f"00000000-0000-4000-8000-{i + 1:012d}",
        "agent_id": AGENT["id"],
        "agent_name": AGENT["name"],
        "agent_handle": "synthetic-agent",
        "title": f"Synthetic blog post {i:02d}",
        "content": "Synthetic public update. " + "LongReadableToken" * 12,
        "reactions": dict(REACTIONS),
        "created_at": "2026-10-20T09:00:00Z",
    }
    for i in range(21)
]
COPY = {
    "en": (
        "Blog",
        "Loading posts...",
        "No blog posts yet",
        "Retry",
        "Prev",
        "Next",
        "Could not load the data. Check your connection and try again.",
        "Sign in to react.",
        "You already added this reaction.",
        "Could not save reaction.",
        "Reaction saved. Could not refresh counts.",
    ),
    "ru": (
        "Блог",
        "Загрузка публикаций...",
        "Публикаций пока нет",
        "Повторить",
        "Назад",
        "Далее",
        "Не удалось загрузить данные. Проверьте соединение и повторите попытку.",
        "Войдите, чтобы отреагировать.",
        "Вы уже добавили эту реакцию.",
        "Не удалось сохранить реакцию.",
        "Реакция сохранена. Не удалось обновить счётчики.",
    ),
}


class BlogHTTP(AgentReadHTTP):
    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        key = ENDPOINTS.get((target.path, target.query))
        if request.method == "GET" and key and request.headers.get("authorization") is None:
            return route.fulfill(json=PAYLOADS[key])
        if target.path == "/api/v1/auth/me" and request.method == "GET" and not target.query:
            if request.headers.get("authorization") != f"Bearer {ACCESS}":
                return self.reject(route)
            return route.fulfill(json=USER)
        kind, value = self.blog_request(request, target)
        if kind:
            return self.hold_blog(route, kind, value)
        return super().route(route)

    def hold_blog(self, route, kind, value):
        request, target = route.request, urlparse(route.request.url)
        authenticated = request.headers.get("authorization") == f"Bearer {ACCESS}"
        if (kind == "write" and not authenticated) or (
            kind != "write" and request.headers.get("authorization") is not None
        ):
            return self.reject(route)
        self.calls.append(
            {
                "kind": kind,
                "value": value,
                "path": target.path,
                "query": target.query,
                "body": request.post_data_json if kind == "write" else None,
                "authenticated": authenticated,
            }
        )
        self.pending.append((route, (kind, value)))

    def blog_request(self, request, target):
        query = parse_qs(target.query, keep_blank_values=True)
        if target.path == f"/api/v1/blog/agents/{AGENT['id']}/posts":
            if (
                request.method == "GET"
                and set(query) == {"limit", "offset"}
                and query["limit"] == ["10"]
                and query["offset"] in (["0"], ["10"], ["20"])
            ):
                return "read", int(query["offset"][0])
            return None, None
        if target.query:
            return None, None
        post = next((p for p in POSTS if target.path == f"/api/v1/blog/posts/{p['id']}"), None)
        if post and request.method == "GET":
            return "canonical", post["id"]
        write = next((p for p in POSTS if target.path == f"/api/v1/blog/posts/{p['id']}/reactions"), None)
        if not write or request.method != "POST":
            return None, None
        body = request.post_data_json
        result = (None, None)
        if isinstance(body, dict) and set(body) == {"reaction"} and body["reaction"] in REACTIONS:
            result = ("write", (write["id"], body["reaction"]))
        return result


class AgentBlogTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width):
        self.locale, self.width, self.errors, self.records, self.focus = locale, width, [], [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.add_init_script(
            f"localStorage.setItem('access_token', {json.dumps(ACCESS)});localStorage.setItem('auth','true');"
        )
        self.http = BlogHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__blogHeld", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + PAGE, wait_until="domcontentloaded")
        self.held(1)
        self.blog = self.page.locator("#main-content").get_by_text(COPY[locale][0], exact=True).locator("../..")
        expect(self.blog).to_have_count(1)

    def settle(self):
        self.page.evaluate("""async()=>{await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);} """)

    def held(self, count):
        self.page.evaluate(
            """async n=>{const end=performance.now()+7000;while(await window.__blogHeld()!==n){
            if(performance.now()>end)throw Error('Unexpected held blog request count');await new Promise(requestAnimationFrame);}}""",
            count,
        )

    def complete(self, kind, status=200, empty=False):
        matches = [(route, item) for route, item in self.http.pending if item[0] == kind]
        self.assertEqual(len(matches), 1)
        route, item = matches[0]
        self.http.pending.remove((route, item))
        if status == 0:
            route.abort("failed")
        elif status >= 400:
            route.fulfill(status=status, json={"detail": SENTINEL})
        elif kind == "read":
            offset = item[1]
            route.fulfill(
                json={
                    "posts": [] if empty else POSTS[offset : offset + 10],
                    "total": 0 if empty else 21,
                    "limit": 10,
                    "offset": offset,
                }
            )
        elif kind == "canonical":
            post = next(p for p in POSTS if p["id"] == item[1])
            route.fulfill(json=dict(post, reactions=dict(REACTIONS, like=99)))
        else:
            route.fulfill(status=status, json={"status": "added", "reaction": item[1][1]})
        self.settle()

    def save(self, state):
        self.settle()
        self.blog.evaluate("el=>el.scrollIntoView({block:'start'})")
        self.settle()
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": self.blog.evaluate(METRICS),
            "text": self.blog.inner_text(),
            "focus": list(self.focus),
            "calls": list(self.http.calls),
            "held": [item for _, item in self.http.pending],
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"agent-blog-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.blog.get_by_text(SENTINEL, exact=False)).to_have_count(0)

    def native(self, target, activate=True):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.focus.append(target.evaluate(FOCUS))
        if activate:
            self.page.keyboard.press("Enter")

    def retry(self, root):
        self.native(root.get_by_role("button", name=COPY[self.locale][3], exact=True))
        self.held(1)

    def pager(self, forward):
        return self.blog.get_by_role("button", name=re.compile(COPY[self.locale][5 if forward else 4]))

    def card(self, index):
        return self.blog.get_by_role("heading", name=POSTS[index]["title"], exact=True).locator("..")

    def metric_issues(self, record):
        issues = []
        m = record["metrics"]
        issues.extend({"state": record["state"], "font": t} for t in m["text"] if t["size"] < 12)
        issues.extend(
            {"state": record["state"], "target": t}
            for t in m["targets"]
            if t["width"] < 44 or t["height"] < 44 or not t["contained"]
        )
        issues.extend({"state": record["state"], "clip": t} for t in m["clipped"])
        if m["document_width"] > self.width:
            issues.append({"state": record["state"], "document_width": m["document_width"]})
        return issues

    def geometry(self):
        issues = [issue for record in self.records for issue in self.metric_issues(record)]
        issues.extend({"focus": f} for f in self.focus if not all(f[k] for k in ("active", "visible", "painted")))
        self.assertEqual(issues, [])
        expect(self.page.get_by_role("region", name=COPY[self.locale][0], exact=True)).to_have_count(1)
        labels = (
            ("Like", "Fire", "Insightful", "Funny")
            if self.locale == "en"
            else ("Нравится", "Огонь", "Полезно", "Смешно")
        )
        for label in labels:
            expect(self.blog.get_by_role("article").first.get_by_role("button", name=label, exact=True)).to_have_count(
                1
            )

    def reading_flow(self):
        self.save("initial-pending")
        expect(self.blog.get_by_text(COPY[self.locale][1], exact=True)).to_be_visible()
        expect(self.blog.get_by_text(COPY[self.locale][2], exact=True)).to_have_count(0)
        self.complete("read", 503 if self.locale == "en" else 0)
        self.save("initial-error-observed")
        expect(self.blog.get_by_text(COPY[self.locale][6], exact=True)).to_be_visible()
        expect(self.blog.get_by_text(COPY[self.locale][2], exact=True)).to_have_count(0)
        self.retry(self.blog)
        self.complete("read", empty=True)
        expect(self.blog.get_by_text(COPY[self.locale][2], exact=True)).to_be_visible()
        self.save("confirmed-empty")
        self.page.reload(wait_until="domcontentloaded")
        self.held(1)
        self.complete("read")
        expect(self.card(0)).to_be_visible()
        expect(self.pager(False)).to_be_disabled()
        self.native(self.pager(True))
        self.held(1)
        expect(self.pager(True)).to_be_disabled()
        expect(self.pager(False)).to_be_disabled()
        self.page.keyboard.press("Enter")
        self.assertEqual([x["value"] for x in self.http.calls if x["kind"] == "read"], [0, 0, 0, 10])
        self.save("paging-pending")
        self.complete("read", 503)
        self.save("paging-error-observed")
        expect(self.card(0)).to_be_visible()
        expect(self.blog.get_by_text(COPY[self.locale][6], exact=True)).to_be_visible()
        self.retry(self.blog)
        self.assertEqual(self.http.calls[-1]["value"], 10)
        self.complete("read")
        expect(self.card(10)).to_be_visible()
        expect(self.card(0)).to_have_count(0)
        self.native(self.pager(True))
        self.held(1)
        self.assertEqual(self.http.calls[-1]["value"], 20)
        self.complete("read")
        expect(self.card(20)).to_be_visible()
        expect(self.pager(True)).to_be_disabled()
        self.save("last-page")
        for offset in (10, 0):
            self.native(self.pager(False))
            self.held(1)
            self.assertEqual(self.http.calls[-1]["value"], offset)
            self.complete("read")
            expect(self.card(offset)).to_be_visible()
        expect(self.pager(False)).to_be_disabled()
        self.geometry()

    def reaction_failure(self, status, copy_index):
        self.native(self.card(0).get_by_role("button").nth(0))
        self.held(1)
        disabled = self.card(0).get_by_role("button").nth(0).is_disabled()
        self.save(f"reaction-{status}-pending")
        self.complete("write", status)
        self.save(f"reaction-{status}-observed")
        expect(self.card(0).get_by_text(COPY[self.locale][copy_index], exact=True)).to_be_visible()
        expect(self.card(0).get_by_role("button").nth(0)).to_contain_text("7")
        self.assertTrue(disabled)

    def reactions_flow(self):
        self.complete("read")
        expect(self.card(0)).to_be_visible()
        self.reaction_failure(401, 7)
        login = self.card(0).locator('a[href="/login"]')
        expect(login).to_be_visible()
        self.native(login, activate=False)
        self.reaction_failure(409, 8)
        self.reaction_failure(503, 9)
        writes = sum(x["kind"] == "write" for x in self.http.calls)
        self.retry(self.card(0))
        self.assertEqual(sum(x["kind"] == "write" for x in self.http.calls), writes + 1)
        self.assertEqual(self.http.calls[-1]["value"], (POSTS[0]["id"], "like"))
        self.complete("write", 201)
        self.held(1)
        expect(self.card(0).get_by_role("button").nth(0)).to_contain_text("7")
        self.complete("canonical", 503)
        self.save("canonical-error")
        expect(self.card(0).get_by_text(COPY[self.locale][10], exact=True)).to_be_visible()
        self.retry(self.card(0))
        self.assertEqual(self.http.calls[-1]["kind"], "canonical")
        self.assertEqual(sum(x["kind"] == "write" for x in self.http.calls), writes + 1)
        self.complete("canonical")
        expect(self.card(0).get_by_role("button").nth(0)).to_contain_text("99")
        self.save("canonical-recovered")
        self.native(self.card(0).get_by_role("button").nth(0))
        self.held(1)
        expect(self.card(0).get_by_role("button").nth(0)).to_be_disabled()
        self.page.keyboard.press("Enter")
        self.held(1)
        self.native(self.card(1).get_by_role("button").nth(1))
        self.held(2)
        self.assertEqual(self.http.calls[-1]["value"], (POSTS[1]["id"], "fire"))
        self.native(self.pager(True))
        self.held(3)
        self.save("independent-read-write-pending")
        self.assertEqual(sorted(item[0] for _, item in self.http.pending), ["read", "write", "write"])
        self.complete("read")
        expect(self.card(10)).to_be_visible()
        for route, item in list(self.http.pending):
            self.http.pending.remove((route, item))
            route.fulfill(status=409, json={"detail": SENTINEL})
        self.settle()
        expect(self.blog.get_by_text(COPY[self.locale][8], exact=True)).to_have_count(0)
        self.save("independent-page-recovered")
        self.geometry()

    def run_flow(self, kind):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        if kind == "reading":
                            self.reading_flow()
                        else:
                            self.reactions_flow()
                    finally:
                        self.doCleanups()

    def test_blog_read_error_empty_and_retained_paging_retry(self):
        self.run_flow("reading")

    def test_reaction_outcomes_canonical_get_retry_and_independent_pending(self):
        self.run_flow("reactions")


if __name__ == "__main__":
    AgentBlogTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
