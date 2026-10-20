"""Prove independent public blog reads and retries on the actual compiled routes."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs, urlparse

from auth_form_fixtures import AuthHTTP, BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_project_detail_reads import TARGET
from test_public_chat_accessibility import FOCUS

POST_ID = "00000000-0000-4000-8000-0000000000a1"
AGENT_ID = "00000000-0000-4000-8000-0000000000a2"
BASE = "/api/v1/blog/posts"
PRIVATE = "Synthetic private blog read failure"
TITLE = "Synthetic independent publication"
COPY = {
    "en": (
        "Could not load the data. Check your connection and try again.",
        "Retry",
        "Next",
        "Prev",
        "No blog posts yet",
    ),
    "ru": (
        "Не удалось загрузить данные. Проверьте соединение и повторите попытку.",
        "Повторить",
        "Далее",
        "Назад",
        "Записей в блоге пока нет",
    ),
}


def post(index=0):
    return {
        "id": POST_ID if index == 0 else f"00000000-0000-4000-8000-{index + 100:012d}",
        "agent_id": AGENT_ID,
        "agent_name": "Synthetic Agent",
        "agent_handle": "synthetic-agent",
        "title": TITLE if index == 0 else f"Synthetic publication {index}",
        "content": f"# {TITLE if index == 0 else 'Synthetic publication'}\n\nSynthetic readable body.",
        "reactions": {"like": 0, "fire": 0, "insightful": 0, "funny": 0},
        "created_at": "2026-10-20T09:00:00Z",
    }


class BlogReadHTTP(AuthHTTP):
    def __init__(self, base_url):
        super().__init__(base_url)
        self.reads = []

    def accepts_page(self, request, target):
        paths = {"/blog", f"/blog/{POST_ID}", f"/agents/{AGENT_ID}"}
        if request.method == "GET" and target.path in paths and not target.query:
            return True
        prefetch_paths = paths | {f"/blog/{post(index)['id']}" for index in range(21)}
        if request.method == "GET" and target.path in prefetch_paths:
            query = parse_qs(target.query, keep_blank_values=True)
            return (
                request.headers.get("rsc") == "1"
                and request.headers.get("next-router-prefetch") == "1"
                and set(query) == {"_rsc"}
                and len(query["_rsc"]) == 1
            )
        return super().accepts_page(request, target)

    def route(self, route):
        target = urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if target.path.startswith("/api/"):
            return self.read(route, target)
        return super().route(route)

    def read(self, route, target):
        request = route.request
        if request.method != "GET" or request.headers.get("authorization") is not None:
            return self.reject(route)
        if target.path == BASE:
            query = parse_qs(target.query, keep_blank_values=True)
            if query not in ({"limit": ["20"], "offset": ["0"]}, {"limit": ["20"], "offset": ["20"]}):
                return self.reject(route)
            resource, offset = "feed", int(query["offset"][0])
        elif not target.query and target.path in {BASE + "/" + POST_ID, BASE + "/" + POST_ID + "/comments"}:
            resource, offset = ("comments" if target.path.endswith("/comments") else "post"), 0
        else:
            return self.reject(route)
        self.reads.append(
            {
                "method": "GET",
                "path": target.path,
                "query": target.query,
                "resource": resource,
                "offset": offset,
                "anonymous": True,
            }
        )
        self.pending.append((route, resource, offset))
        return None

    def count(self, key):
        resource, offset = key.split(":")
        return sum(kind == resource and off == int(offset) for _, kind, off in self.pending)

    def close(self, context):
        while self.pending:
            self.pending.pop()[0].abort("failed")
        context.close()


class BlogReadTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_route(self, kind, locale, width):
        self.kind, self.locale, self.width = kind, locale, width
        self.records, self.errors, self.focus, self.targets = [], [], [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = BlogReadHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__blogReadCount", self.http.count)
        path = "/blog" if kind == "feed" else f"/blog/{POST_ID}"
        self.page.goto(self.options.base_url + path, wait_until="domcontentloaded")
        self.main = self.page.get_by_role("main")
        self.wait_pending("feed" if kind == "feed" else "post")
        if kind == "detail":
            self.wait_pending("comments")
        self.settle()

    def settle(self):
        self.page.evaluate("""async()=>{await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}""")

    def wait_pending(self, resource, offset=0):
        self.page.evaluate(
            """async key=>{const end=performance.now()+7000;while(await window.__blogReadCount(key)<1){
            if(performance.now()>end)throw Error('Expected public blog read');await new Promise(requestAnimationFrame);}}""",
            f"{resource}:{offset}",
        )

    def place(self, root):
        root.scroll_into_view_if_needed()
        root.evaluate("""el=>{const h=document.querySelector('header')?.getBoundingClientRect().bottom||0;
            window.scrollBy(0,el.getBoundingClientRect().top-h-16)}""")
        self.settle()

    def native(self, target):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.place(target)
        self.focus.append(target.evaluate(FOCUS))
        self.targets.append(target.evaluate(TARGET))

    def alert(self):
        return self.main.get_by_role("alert").filter(has_text=COPY[self.locale][0])

    def pager(self):
        return self.main.get_by_role("button", name=COPY[self.locale][2], exact=True).locator("..")

    def save(self, state, surface):
        self.settle()
        present = surface.count() == 1
        if present:
            self.place(surface)
        record = {
            "kind": self.kind,
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": surface.evaluate(METRICS) if present else None,
            "scoped_state_present": present,
            "focus": list(self.focus),
            "targets": list(self.targets),
            "reads": list(self.http.reads),
            "known_post_present": self.main.get_by_text(TITLE, exact=True).count(),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"blog-reads-{self.kind}-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.main.get_by_text(PRIVATE, exact=False)).to_have_count(0)
        self.assertTrue(present, "Expected scoped blog read state")

    def complete(self, resource, offset=0, status=200):
        self.wait_pending(resource, offset)
        matching = [item for item in self.http.pending if item[1:] == (resource, offset)]
        self.assertEqual(len(matching), 1)
        route, _, _ = matching[0]
        self.http.pending.remove(matching[0])
        if status == 0:
            route.abort("failed")
        elif status >= 400:
            route.fulfill(status=status, json={"detail": PRIVATE})
        elif resource == "feed":
            route.fulfill(
                json={
                    "posts": [post(index) for index in range(offset, min(offset + 20, 21))],
                    "total": 21,
                    "limit": 20,
                    "offset": offset,
                }
            )
        elif resource == "post":
            route.fulfill(json=post())
        else:
            route.fulfill(
                json=[
                    {
                        "id": "00000000-0000-4000-8000-0000000000a3",
                        "post_id": POST_ID,
                        "author_type": "user",
                        "author_id": "00000000-0000-4000-8000-0000000000b1",
                        "author_name": "Synthetic commenter",
                        "content": "Synthetic recovered comment",
                        "created_at": "2026-10-20T10:00:00Z",
                    }
                ]
            )
        self.settle()

    def retry(self):
        target = self.alert().get_by_role("button", name=COPY[self.locale][1], exact=True)
        self.native(target)
        self.page.keyboard.press("Enter")

    def feed_flow(self):
        self.save("initial-pending", self.main.get_by_role("status"))
        expect(self.main.get_by_text("0", exact=True)).to_have_count(0)
        self.complete("feed", status=503 if self.locale == "en" else 0)
        self.save("initial-error", self.alert())
        expect(self.main.get_by_text(COPY[self.locale][4], exact=True)).to_have_count(0)
        self.retry()
        self.complete("feed")
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.save("ready", self.pager())
        next_button = self.main.get_by_role("button", name=COPY[self.locale][2], exact=True)
        self.native(next_button)
        self.page.keyboard.press("Enter")
        self.wait_pending("feed", 20)
        expect(next_button).to_be_disabled()
        self.page.keyboard.press("Enter")
        self.assertEqual(len(self.http.reads), 3)
        self.save("page-pending", self.pager())
        self.complete("feed", 20, 503)
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.native(self.alert().get_by_role("button", name=COPY[self.locale][1], exact=True))
        self.save("page-error-retained", self.alert())
        self.page.keyboard.press("Enter")
        self.complete("feed", 20)
        expect(self.main.get_by_role("heading", name="Synthetic publication 20", exact=True)).to_be_visible()
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_have_count(0)
        expect(next_button).to_be_disabled()
        self.save("page-recovered", self.pager())
        self.assertEqual([row["offset"] for row in self.http.reads], [0, 0, 20, 20])

    def detail_flow(self):
        self.save("primary-pending", self.main)
        self.complete("post", status=503)
        self.save("primary-error", self.alert())
        self.assertEqual(len(self.http.reads), 2)
        self.retry()
        self.complete("post")
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.assertEqual([row["resource"] for row in self.http.reads], ["post", "comments", "post"])
        self.save("post-ready-comments-pending", self.main.get_by_role("status"))
        self.complete("comments", status=503)
        self.native(self.alert().get_by_role("button", name=COPY[self.locale][1], exact=True))
        self.save("comments-error", self.alert())
        self.page.keyboard.press("Enter")
        self.wait_pending("comments")
        self.save("comments-retry-pending", self.main.get_by_role("status"))
        self.complete("comments")
        expect(self.main.get_by_text("Synthetic recovered comment", exact=True)).to_be_visible()
        expect(self.alert()).to_have_count(0)
        self.save("comments-recovered", self.main.get_by_text("Synthetic recovered comment", exact=True))
        self.assertEqual([row["resource"] for row in self.http.reads], ["post", "comments", "post", "comments"])

    def metric_issues(self, metrics):
        issues = [{"font": item} for item in metrics["text"] if item["size"] < 12]
        issues.extend({"clip": item} for item in metrics["clipped"])
        issues.extend(
            {"target": item}
            for item in metrics["targets"]
            if item["width"] < 44 or item["height"] < 44 or not item["contained"]
        )
        if metrics["document_width"] > self.width:
            issues.append({"document_width": metrics["document_width"]})
        return issues

    def verify(self):
        issues = [issue for record in self.records for issue in self.metric_issues(record["metrics"])]
        issues.extend(
            {"focus": item} for item in self.focus if not all(item[key] for key in ("active", "visible", "painted"))
        )
        issues.extend(
            {"target": item}
            for item in self.targets
            if item["font"] < 12
            or item["width"] < 44
            or item["height"] < 44
            or not item["contained"]
            or not item["in_view"]
        )
        self.assertEqual(issues, [])

    def run_flow(self, kind):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(kind=kind, locale=locale, width=width):
                    try:
                        self.open_route(kind, locale, width)
                        self.feed_flow() if kind == "feed" else self.detail_flow()
                        self.verify()
                    finally:
                        self.doCleanups()

    def test_feed_read_truth_and_retained_page_retry(self):
        self.run_flow("feed")

    def test_independent_post_and_comment_read_retry(self):
        self.run_flow("detail")


if __name__ == "__main__":
    BlogReadTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
