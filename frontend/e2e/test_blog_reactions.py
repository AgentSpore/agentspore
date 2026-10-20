"""Exercise global blog toggles and count recovery through native controls."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs

from auth_form_fixtures import BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_blog_reads import BASE, POST_ID, TITLE, BlogReadHTTP, post
from test_public_chat_accessibility import FOCUS

TOKEN = "synthetic-blog-reaction"
POST = f"{BASE}/{POST_ID}"
REACTIONS = f"{POST}/reactions"
COPY = {
    "en": ("Like", "Fire", "Insightful", "Funny", "Reaction saved. Could not refresh counts.", "Retry"),
    "ru": ("Нравится", "Огонь", "Полезно", "Смешно", "Реакция сохранена. Не удалось обновить счётчики.", "Повторить"),
}


def publication(likes=1):
    value = post()
    value["reactions"]["like"] = likes
    return value


class ReactionHTTP(BlogReadHTTP):
    """Admit exact synthetic public reads and the current member's toggle writes."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.writes, self.counts = [], 0
        self.awaiting_counts = False

    def read(self, route, target):
        request = route.request
        authorization = request.headers.get("authorization")
        if request.method == "GET" and target.path == "/api/v1/auth/me" and not target.query:
            if authorization != f"Bearer {TOKEN}":
                return self.reject(route)
            return route.fulfill(json=USER)
        if request.method in {"POST", "DELETE"}:
            return self.write(route, target)
        if request.method != "GET" or authorization is not None:
            return self.reject(route)
        return self.public_read(route, target)

    def public_read(self, route, target):
        self.reads.append({"method": "GET", "path": target.path, "query": target.query})
        if target.path == POST and not target.query:
            if self.awaiting_counts:
                self.awaiting_counts = False
                self.counts += 1
                self.pending.append((route, "counts"))
                return None
            return route.fulfill(json=publication())
        if target.path == BASE and parse_qs(target.query, keep_blank_values=True) == {"limit": ["20"], "offset": ["0"]}:
            return route.fulfill(json={"posts": [publication()], "total": 1, "limit": 20, "offset": 0})
        if target.path == f"{POST}/comments" and not target.query:
            return route.fulfill(json=[])
        return self.reject(route)

    def write(self, route, target):
        request = route.request
        if target.query or request.headers.get("authorization") != f"Bearer {TOKEN}":
            return self.reject(route)
        if request.method == "POST" and target.path == REACTIONS:
            if request.post_data_json != {"reaction": "like"}:
                return self.reject(route)
        elif request.method != "DELETE" or target.path != f"{REACTIONS}/like" or request.post_data:
            return self.reject(route)
        self.writes.append({"method": request.method, "path": target.path, "authenticated": True})
        self.pending.append((route, request.method))
        return None

    def close(self, context):
        while self.pending:
            self.pending.pop()[0].abort("failed")
        context.close()


class BlogReactionTests(BrowserFixture, unittest.TestCase):
    """Measure only reaction controls and recovery notices on both real callers."""

    options: Namespace

    def open_route(self, surface, locale, width):
        self.surface, self.locale, self.width = surface, locale, width
        self.records, self.errors, self.focus = [], [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.add_init_script(f"localStorage.setItem('access_token',{json.dumps(TOKEN)});")
        self.http = ReactionHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__reactionHeld", lambda: [kind for _, kind in self.http.pending])
        path = "/blog" if surface == "feed" else f"/blog/{POST_ID}"
        self.page.goto(self.options.base_url + path, wait_until="domcontentloaded")
        self.main = self.page.get_by_role("main")
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.like = self.main.get_by_role("button", name=COPY[locale][0], exact=True)
        self.bar = self.like.locator("..")
        self.settle()

    def settle(self):
        self.page.evaluate("""async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);
            await Promise.all(document.getAnimations().filter(a=>
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}""")

    def held(self, kind):
        self.page.evaluate(
            """async kind=>{const end=performance.now()+7000;while(true){const kinds=await window.__reactionHeld();
            if(kinds.length===1&&kinds[0]===kind)return;if(performance.now()>end)
            throw Error('Expected one held reaction request');await new Promise(requestAnimationFrame);}}""",
            kind,
        )

    def complete(self, kind, status=200, likes=1):
        self.held(kind)
        route, actual = self.http.pending.pop()
        self.assertEqual(actual, kind)
        self.http.awaiting_counts = (kind == "POST" and status == 201) or (kind == "DELETE" and status == 200)
        body = publication(likes) if kind == "counts" else {"status": "added" if status == 201 else "removed"}
        if status >= 400:
            body = {"detail": "Synthetic reaction outcome"}
        route.fulfill(status=status, json=body)
        self.settle()

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

    def expect_count(self, value):
        expect(self.like).to_have_js_property("disabled", False)
        self.assertEqual(self.like.evaluate("el=>Number(el.textContent.match(/\\d+/)?.[0]||0)"), value)

    def save(self, state, root=None):
        surface = self.bar if root is None else root
        self.place(surface)
        record = {
            "surface": self.surface,
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": surface.evaluate(METRICS),
            "focus": list(self.focus),
            "reads": list(self.http.reads),
            "writes": list(self.http.writes),
            "canonical_reads": self.http.counts,
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"blog-reactions-{self.surface}-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])

    def add_and_remove(self):
        for label in COPY[self.locale][:4]:
            expect(self.bar.get_by_role("button", name=label, exact=True)).to_have_count(1)
        self.expect_count(1)
        self.native(self.like)
        self.save("ready-focused")
        self.page.keyboard.press("Enter")
        self.held("POST")
        expect(self.like).to_be_disabled()
        self.save("post-pending")
        self.complete("POST", 201)
        self.held("counts")
        self.assertEqual(self.like.evaluate("el=>Number(el.textContent.match(/\\d+/)?.[0]||0)"), 1)
        self.save("counts-pending")
        self.complete("counts", likes=2)
        self.expect_count(2)
        self.save("added")
        self.native(self.like)
        self.page.keyboard.press("Enter")
        self.complete("POST", 409)
        self.held("DELETE")
        self.save("remove-pending")
        self.complete("DELETE")
        self.complete("counts", likes=1)
        self.expect_count(1)
        self.save("removed")

    def recover_counts(self):
        self.native(self.like)
        self.page.keyboard.press("Enter")
        self.complete("POST", 201)
        self.complete("counts", 503)
        notice = self.main.get_by_role("alert").filter(has_text=COPY[self.locale][4])
        expect(notice).to_be_visible()
        self.expect_count(1)
        retry = notice.get_by_role("button", name=COPY[self.locale][5], exact=True)
        self.native(retry)
        self.save("counts-error-retry-focused", notice)
        self.http.awaiting_counts = True
        self.page.keyboard.press("Enter")
        self.complete("counts", likes=2)
        self.expect_count(2)
        self.save("counts-recovered")
        self.assertEqual([item["method"] for item in self.http.writes], ["POST", "POST", "DELETE", "POST"])
        self.assertEqual(self.http.counts, 4)
        expect(notice).to_have_count(0)

    def verify(self):
        issues = []
        self.assertEqual(len(self.records), 8)
        for record in self.records:
            metrics = record["metrics"]
            issues.extend({"font": value} for value in metrics["text"] if value["size"] < 12)
            issues.extend({"clip": value} for value in metrics["clipped"])
            issues.extend(
                {"target": value}
                for value in metrics["targets"]
                if value["width"] < 44 or value["height"] < 44 or not value["contained"]
            )
            if metrics["document_width"] > self.width:
                issues.append({"document_width": metrics["document_width"]})
        issues.extend(
            {"focus": item} for item in self.focus if not all(item[key] for key in ("active", "visible", "painted"))
        )
        self.assertEqual(issues, [])

    def run_surface(self, surface):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(surface=surface, locale=locale, width=width):
                    try:
                        self.open_route(surface, locale, width)
                        self.add_and_remove()
                        self.recover_counts()
                        self.verify()
                    finally:
                        self.doCleanups()

    def test_feed_toggle_and_get_only_recovery(self):
        self.run_surface("feed")

    def test_detail_toggle_and_get_only_recovery(self):
        self.run_surface("detail")


if __name__ == "__main__":
    BlogReactionTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
