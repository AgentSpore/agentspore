"""Exercise comment drafts, author-only deletion and GET-only recovery on the compiled page."""

import json
import unittest
from argparse import Namespace

from auth_form_fixtures import BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_blog_reads import BASE, POST_ID, TITLE, BlogReadHTTP, post
from test_project_detail_reads import TARGET
from test_public_chat_accessibility import FOCUS

TOKEN = "synthetic-comment-owner"
OWNER = "00000000-0000-4000-8000-0000000000c1"
OWN = "00000000-0000-4000-8000-000000000001"
FOREIGN = "00000000-0000-4000-8000-000000000002"
COMMENTS = f"{BASE}/{POST_ID}/comments"
PRIVATE = "Synthetic private comment failure"
COPY = {
    "en": ("Write a comment...", "Post", "Delete", "Retry", "Could not post comment.", "Could not delete comment."),
    "ru": (
        "Напишите комментарий...",
        "Опубликовать",
        "Удалить",
        "Повторить",
        "Не удалось отправить комментарий.",
        "Не удалось удалить комментарий.",
    ),
}


def comment(identifier=OWN, content="Own comment", author=OWNER):
    return {
        "id": identifier,
        "post_id": POST_ID,
        "author_type": "user",
        "author_id": author,
        "author_name": "Reader",
        "content": content,
        "created_at": "2026-10-20T09:00:00Z",
    }


def metric_issues(metrics, width):
    issues = [{"font": item} for item in metrics["text"] if item["size"] < 12]
    issues.extend({"clip": item} for item in metrics["clipped"])
    issues.extend(
        {"target": item}
        for item in metrics["targets"]
        if item["width"] < 44 or item["height"] < 44 or not item["contained"]
    )
    if metrics["document_width"] > width:
        issues.append({"document_width": metrics["document_width"]})
    return issues


class CommentHTTP(BlogReadHTTP):
    """Hold exact user writes and every subsequent anonymous comments read."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.writes, self.comments_reads = [], 0
        self.comments = [comment(), comment(FOREIGN, "Foreign comment", FOREIGN)]

    def read(self, route, target):
        request = route.request
        if target.query:
            return self.reject(route)
        if request.method == "GET" and target.path == "/api/v1/auth/me":
            if request.headers.get("authorization") != f"Bearer {TOKEN}":
                return self.reject(route)
            return route.fulfill(json=dict(USER, id=OWNER, name="Reader"))
        if request.method in {"POST", "DELETE"}:
            return self.write(route, target)
        if request.method != "GET" or request.headers.get("authorization") is not None:
            return self.reject(route)
        return self.public_read(route, target)

    def public_read(self, route, target):
        self.reads.append({"method": "GET", "path": target.path, "anonymous": True})
        if target.path == f"{BASE}/{POST_ID}":
            return route.fulfill(json=post())
        if target.path != COMMENTS:
            return self.reject(route)
        self.comments_reads += 1
        if self.comments_reads == 1:
            return route.fulfill(json=self.comments)
        self.pending.append((route, "comments"))
        return None

    def write(self, route, target):
        request = route.request
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return self.reject(route)
        if request.method == "POST" and target.path == COMMENTS:
            body = request.post_data_json
            if not isinstance(body, dict) or set(body) != {"content"} or not isinstance(body["content"], str):
                return self.reject(route)
        elif request.method != "DELETE" or target.path != f"{COMMENTS}/{OWN}" or request.post_data:
            return self.reject(route)
        self.writes.append(
            {
                "method": request.method,
                "path": target.path,
                "body": request.post_data_json if request.method == "POST" else None,
            }
        )
        self.pending.append((route, request.method))
        return None

    def complete(self, kind, status):
        route, actual = self.pending.pop()
        if kind != actual:
            raise AssertionError("Expected exact pending comment operation")
        body = self.comments
        if kind == "POST" and status == 201:
            body = comment(
                f"00000000-0000-4000-8000-{len(self.writes) + 100:012d}", route.request.post_data_json["content"]
            )
            self.comments.append(body)
        if kind == "DELETE" and status == 200:
            self.comments = [row for row in self.comments if row["id"] != OWN]
            body = {"status": "deleted"}
        route.fulfill(status=status, json={"detail": PRIVATE} if status >= 400 else body)

    def close(self, context):
        while self.pending:
            self.pending.pop()[0].abort("failed")
        context.close()


class BlogCommentTests(BrowserFixture, unittest.TestCase):
    """Measure only touched composer, mutation actions and their recovery notices."""

    options: Namespace

    def open_route(self, locale, width):
        self.locale, self.width = locale, width
        self.records, self.errors, self.focus, self.targets = [], [], [], []
        context = self.browser.new_context(viewport={"width": width, "height": 900}, service_workers="block")
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.add_init_script(f"localStorage.setItem('access_token',{json.dumps(TOKEN)});")
        self.http = CommentHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__commentHeld", lambda: [kind for _, kind in self.http.pending])
        self.page.goto(f"{self.options.base_url}/blog/{POST_ID}", wait_until="domcontentloaded")
        self.main = self.page.get_by_role("main")
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.draft = self.main.get_by_role("textbox", name=COPY[locale][0], exact=True)
        expect(self.draft).to_be_visible()
        self.form = self.draft.locator("..")
        self.submit = self.form.get_by_role("button", name=COPY[locale][1], exact=True)
        self.own = self.main.get_by_text("Own comment", exact=True).locator("..")
        self.foreign = self.main.get_by_text("Foreign comment", exact=True).locator("..")
        expect(self.own.get_by_role("button", name=COPY[locale][2], exact=True)).to_have_count(1)
        expect(self.foreign.get_by_role("button", name=COPY[locale][2], exact=True)).to_have_count(0)
        self.settle()

    def settle(self):
        self.page.evaluate("""async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);
            await Promise.all(document.getAnimations().filter(a=>
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}""")

    def held(self, kind):
        self.page.evaluate(
            """async kind=>{const end=performance.now()+7000;while(true){const pending=await window.__commentHeld();
            if(pending.length===1&&pending[0]===kind)return;if(performance.now()>end)
            throw Error('Expected one held comment operation');await new Promise(requestAnimationFrame);}}""",
            kind,
        )

    def complete(self, kind, status=200):
        self.held(kind)
        self.http.complete(kind, status)
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

    def place(self, root):
        root.scroll_into_view_if_needed()
        root.evaluate("""el=>{const h=document.querySelector('header')?.getBoundingClientRect().bottom||0;
            window.scrollBy(0,el.getBoundingClientRect().top-h-16)}""")
        self.settle()

    def activate(self, target):
        self.native(target)
        self.page.keyboard.press("Enter")

    def notice(self, message):
        return self.main.get_by_role("alert").filter(has_text=message)

    def save(self, state, surface):
        self.place(surface)
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": surface.evaluate(METRICS),
            "focus": list(self.focus),
            "targets": list(self.targets),
            "reads": list(self.http.reads),
            "writes": list(self.http.writes),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"blog-comments-{self.flow}-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.main.get_by_text(PRIVATE, exact=True)).to_have_count(0)

    def draft_recovery(self):
        self.draft.fill(" submitted draft ")
        self.activate(self.submit)
        self.held("POST")
        self.native(self.draft)
        self.draft.fill("newer draft")
        self.save("held-newer-draft", self.form)
        self.complete("POST", 201)
        self.complete("comments")
        expect(self.draft).to_have_value("newer draft")
        self.activate(self.submit)
        self.complete("POST", 503)
        failure = self.notice(COPY[self.locale][4])
        expect(failure).to_be_visible()
        self.assertEqual(self.http.comments_reads, 2)
        self.save("post-error-retained", failure)
        self.draft.fill("current recovery draft")
        self.activate(self.submit)
        self.complete("POST", 201)
        self.complete("comments")
        expect(self.draft).to_have_value("")
        expect(failure).to_have_count(0)
        self.save("current-payload-accepted", self.form)
        self.assertEqual(
            [item["body"] for item in self.http.writes],
            [{"content": value} for value in ("submitted draft", "newer draft", "current recovery draft")],
        )

    def deletion_and_read_recovery(self):
        self.activate(self.own.get_by_role("button", name=COPY[self.locale][2], exact=True))
        self.complete("DELETE", 404)
        failure = self.notice(COPY[self.locale][5])
        retry = failure.get_by_role("button", name=COPY[self.locale][3], exact=True)
        self.native(retry)
        self.save("delete-error-retry", failure)
        self.page.keyboard.press("Enter")
        self.complete("DELETE")
        expect(self.main.get_by_text("Own comment", exact=True)).to_have_count(0)
        self.draft.fill("canonical addition")
        self.activate(self.submit)
        self.complete("POST", 201)
        self.complete("comments", 503)
        notice = self.main.get_by_role("alert")
        retry = notice.get_by_role("button", name=COPY[self.locale][3], exact=True)
        self.native(retry)
        self.save("accepted-post-read-error", notice)
        self.page.keyboard.press("Enter")
        self.complete("comments")
        expect(self.main.get_by_text("canonical addition", exact=True)).to_be_visible()
        expect(self.main.get_by_text("Own comment", exact=True)).to_have_count(0)
        expect(notice).to_have_count(0)
        self.save("get-only-recovered", self.form)
        self.assertEqual([item["method"] for item in self.http.writes], ["DELETE", "DELETE", "POST"])
        self.assertEqual(self.http.comments_reads, 3)
        self.assertEqual(sum(item["path"] == f"{BASE}/{POST_ID}" for item in self.http.reads), 1)

    def verify(self):
        issues = []
        self.assertEqual(len(self.records), 3)
        for record in self.records:
            issues.extend(metric_issues(record["metrics"], self.width))
        self.assertTrue(all(all(item[key] for key in ("active", "visible", "painted")) for item in self.focus))
        self.assertTrue(
            all(
                item["width"] >= 44
                and item["height"] >= 44
                and item["font"] >= 12
                and item["contained"]
                and item["in_view"]
                for item in self.targets
            )
        )
        self.assertEqual(issues, [])

    def run_flow(self, flow):
        self.flow = flow
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(flow=flow, locale=locale, width=width):
                    try:
                        self.open_route(locale, width)
                        getattr(self, flow)()
                        self.verify()
                    finally:
                        self.doCleanups()

    def test_native_draft_revision_and_explicit_current_payload_recovery(self):
        self.run_flow("draft_recovery")

    def test_native_author_delete_and_get_only_comment_recovery(self):
        self.run_flow("deletion_and_read_recovery")


if __name__ == "__main__":
    BlogCommentTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
