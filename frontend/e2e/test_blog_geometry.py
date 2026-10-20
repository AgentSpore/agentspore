"""Measure blog-owned typography, standalone actions and complete essential text."""

import json
import unittest
from argparse import Namespace
from urllib.parse import parse_qs

from auth_form_fixtures import BrowserFixture, USER, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_blog_comments import OWNER, TOKEN, comment
from test_blog_reads import AGENT_ID, BASE, POST_ID, BlogReadHTTP, post
from test_project_detail_reads import TARGET
from test_public_chat_accessibility import FOCUS

TITLE = "Synthetic publication " + "t" * 278
NAME = "SyntheticAgent" + "n" * 86
HANDLE = "synthetic-" + "h" * 54
INLINE_URL = "https://example.invalid/" + "p" * 140
CODE = "const synthetic = '" + "k" * 150 + "';"
AUTHOR = "CommentAuthor" + "a" * 87
COMMENT = "UnbrokenComment" + "c" * 200
BODY = f"# {TITLE}\n\n{INLINE_URL}\n\n```typescript\n{CODE}\n```\n\n" + (
    "| Column A | Column B |\n| --- | --- |\n| " + "v" * 100 + " | " + "w" * 100 + " |\n\n"
    "1. First numbered paragraph.\n2. Second numbered paragraph.\n\nSynthetic readable paragraph. " * 2
)
COPY = {
    "en": ("Home", "Read more →", "Write a comment...", "Delete", "← Blog", "No blog posts yet"),
    "ru": ("Главная", "Читать далее →", "Напишите комментарий...", "Удалить", "← Блог", "Записей в блоге пока нет"),
}
DECORATIVE = {"/", "·", "+", "?", "×", "..."}
PSEUDO = """root=>[...root.querySelectorAll('ol li')].map(el=>{const s=getComputedStyle(el,'::before');
    return {text:'ordered-list marker',size:parseFloat(s.fontSize)};})"""


def publication():
    return dict(post(), title=TITLE, agent_name=NAME, agent_handle=HANDLE, content=BODY)


class GeometryHTTP(BlogReadHTTP):
    """Serve exact synthetic read contracts; all mutations remain forbidden."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.empty, self.missing = False, False

    def accepts_page(self, request, target):
        if request.method != "GET" or request.headers.get("rsc") != "1":
            return super().accepts_page(request, target)
        query = parse_qs(target.query, keep_blank_values=True)
        if set(query) != {"_rsc"} or len(query["_rsc"]) != 1:
            return super().accepts_page(request, target)
        if target.path in {"/docs/connect", "/docs/glossary"}:
            return request.headers.get("next-router-prefetch") == "1"
        if target.path in {"/blog", f"/blog/{POST_ID}", f"/agents/{AGENT_ID}"}:
            return request.headers.get("next-router-prefetch") in {None, "1"}
        return super().accepts_page(request, target)

    def read(self, route, target):
        request = route.request
        if request.method != "GET":
            return self.reject(route)
        if target.path == "/api/v1/auth/me" and not target.query:
            if request.headers.get("authorization") != f"Bearer {TOKEN}":
                return self.reject(route)
            return route.fulfill(json=dict(USER, id=OWNER, name="Reader"))
        if request.headers.get("authorization") is not None:
            return self.reject(route)
        self.reads.append({"path": target.path, "query": target.query, "anonymous": True})
        if target.path == BASE and parse_qs(target.query, keep_blank_values=True) == {"limit": ["20"], "offset": ["0"]}:
            posts = [] if self.empty else [publication(), *[post(index) for index in range(1, 20)]]
            return route.fulfill(json={"posts": posts, "total": 0 if self.empty else 21, "limit": 20, "offset": 0})
        return self.detail_read(route, target)

    def detail_read(self, route, target):
        if target.query:
            return self.reject(route)
        if target.path == f"{BASE}/{POST_ID}":
            return route.fulfill(status=404 if self.missing else 200, json={} if self.missing else publication())
        if target.path == f"{BASE}/{POST_ID}/comments":
            return route.fulfill(json=[dict(comment(), author_name=AUTHOR, content=COMMENT)])
        return self.reject(route)


def metric_issues(metrics, width):
    issues = [{"font": item} for item in metrics["text"] if item["size"] < 12]
    issues.extend({"clip": item} for item in metrics["clipped"])
    issues.extend({"ellipsis": item} for item in metrics["essential"] if item["ellipsis"])
    issues.extend(
        {"target": item}
        for item in metrics["targets"]
        if item["width"] < 44 or item["height"] < 44 or not item["contained"]
    )
    if metrics["document_width"] > width:
        issues.append({"document_width": metrics["document_width"]})
    return issues


class BlogGeometryTests(BrowserFixture, unittest.TestCase):
    """Collect all scoped metrics before asserting the aggregate geometry result."""

    options: Namespace

    def open_route(self, surface, locale, width):
        self.surface, self.locale, self.width = surface, locale, width
        self.records, self.errors, self.focus, self.targets, self.behavior = [], [], [], [], {}
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        context.add_init_script(f"localStorage.setItem('access_token',{json.dumps(TOKEN)});")
        self.http = GeometryHTTP(self.options.base_url)
        self.addCleanup(context.close)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.navigate("/blog" if surface == "feed" else f"/blog/{POST_ID}")
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.settle()

    def navigate(self, path):
        self.page.goto(self.options.base_url + path, wait_until="domcontentloaded")
        self.main = self.page.get_by_role("main")

    def settle(self):
        self.page.evaluate("""async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);
            await Promise.all(document.getAnimations().filter(a=>
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}""")

    def place(self, target):
        target.scroll_into_view_if_needed()
        target.evaluate("""el=>{const h=document.querySelector('header')?.getBoundingClientRect().bottom||0;
            window.scrollBy(0,el.getBoundingClientRect().top-h-16)}""")
        self.settle()

    def native(self, target, inline=False):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        self.place(target)
        self.focus.append(target.evaluate(FOCUS))
        self.targets.append(dict(target.evaluate(TARGET), inline_exception=inline))

    def measure(self):
        result = self.main.evaluate(METRICS)
        result["text"] = [item for item in result["text"] if item["text"] not in DECORATIVE]
        result["text"].extend(self.main.evaluate(PSEUDO))
        result["targets"] = [item for item in result["targets"] if item["name"] != INLINE_URL]
        if self.page.url.endswith(f"/blog/{POST_ID}") and self.main.locator("article").count():
            regions = [self.main.locator("article"), self.main.locator("section")]
            result["clipped"] = [item for region in regions for item in region.evaluate(METRICS)["clipped"]]
            result["clip_exception"] = (
                "Duplicate breadcrumb title only; font/targets still measured globally, article/section text clipping remains strict."
            )
        return result

    def save(self, state, target):
        self.place(target)
        record = {
            "surface": self.surface,
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": self.measure(),
            "focus": list(self.focus),
            "targets": list(self.targets),
            "behavior": dict(self.behavior),
            "reads": list(self.http.reads),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"blog-geometry-{self.surface}-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))

    def feed_flow(self):
        heading = self.main.get_by_role("heading", name=TITLE, exact=True)
        card = heading.locator("xpath=ancestor::article")
        author = card.get_by_role("link", name=NAME, exact=False)
        self.native(author)
        self.behavior["author_href"] = author.get_attribute("href") == f"/agents/{AGENT_ID}"
        self.save("populated-card", heading)
        inline = card.locator(f'a[href="{INLINE_URL}"]')
        self.native(inline, inline=True)
        more = card.get_by_role("link", name=COPY[self.locale][1], exact=True)
        self.native(more)
        title_link = heading.locator("..")
        self.native(title_link)
        self.page.keyboard.press("Enter")
        expect(self.page).to_have_url(self.options.base_url + f"/blog/{POST_ID}")
        expect(self.main.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.settle()
        self.behavior["native_post_navigation"] = True
        self.save("native-publication", self.main.get_by_role("heading", name=TITLE, exact=True))
        self.http.empty = True
        self.navigate("/blog")
        expect(self.main.get_by_text(COPY[self.locale][5], exact=True)).to_be_visible()
        self.native(self.main.get_by_role("link", name=COPY[self.locale][0], exact=False))
        self.save("confirmed-empty-chrome", self.main)

    def detail_flow(self):
        self.native(self.main.locator(f'a[href="/agents/{AGENT_ID}"]'))
        self.save("author-full-title", self.main.get_by_role("heading", name=TITLE, exact=True))
        inline = self.main.locator(f'.prose-blog a[href="{INLINE_URL}"]')
        self.native(inline, inline=True)
        self.behavior["full_code_preserved"] = CODE in self.main.locator("pre").inner_text()
        self.behavior["full_comment_preserved"] = self.main.get_by_text(COMMENT, exact=True).count() == 1
        self.behavior["full_author_preserved"] = self.main.get_by_text(AUTHOR, exact=True).count() == 1
        textarea = self.main.get_by_role("textbox", name=COPY[self.locale][2], exact=True)
        expect(textarea).to_be_visible()
        self.native(textarea)
        self.native(self.main.get_by_role("button", name=COPY[self.locale][3], exact=True))
        self.save("comments-native-focus", textarea)
        self.http.missing = True
        self.navigate(f"/blog/{POST_ID}")
        back = self.main.get_by_role("link", name=COPY[self.locale][4], exact=True)
        expect(back).to_be_visible()
        self.native(back)
        self.save("not-found-back", back)
        self.page.keyboard.press("Enter")
        expect(self.page).to_have_url(self.options.base_url + "/blog")
        self.behavior["native_back_navigation"] = True

    def verify(self):
        issues = []
        self.assertEqual(len(self.records), 3)
        for record in self.records:
            issues.extend(metric_issues(record["metrics"], self.width))
            issues.extend({"unknown_http": item} for item in record["unknown_http"])
            issues.extend({"page_error": item} for item in record["page_errors"])
        issues.extend(
            {"focus": item} for item in self.focus if not all(item[k] for k in ("active", "visible", "painted"))
        )
        issues.extend(
            {"target": item}
            for item in self.targets
            if not item["inline_exception"] and (item["width"] < 44 or item["height"] < 44 or not item["contained"])
        )
        self.assertTrue(all(self.behavior.values()), self.behavior)
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(issues, [])

    def run_surface(self, surface):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(surface=surface, locale=locale, width=width):
                    try:
                        self.open_route(surface, locale, width)
                        getattr(self, f"{surface}_flow")()
                        self.verify()
                    finally:
                        self.doCleanups()

    def test_feed_geometry_and_native_links(self):
        self.run_surface("feed")

    def test_detail_geometry_and_native_controls(self):
        self.run_surface("detail")


if __name__ == "__main__":
    BlogGeometryTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
