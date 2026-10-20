"""Exercise anonymous project votes, errors and retained server counts through HTTP."""

import json
import unittest
from argparse import Namespace
from urllib.parse import urlparse

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect
from test_agents_list import METRICS
from test_chat_ownership import PROJECT_ID, OwnershipHTTP
from test_project_detail_reads import OWNERSHIP_DATA, PROJECT_DATA, TARGET
from test_public_chat_accessibility import FOCUS

TITLE = "Synthetic vote project"
DATA = dict(PROJECT_DATA, title=TITLE, votes_up=12, votes_down=2)
BASE = f"/api/v1/projects/{PROJECT_ID}"
READS = {
    BASE: DATA,
    BASE + "/ownership": dict(OWNERSHIP_DATA, project_title=TITLE),
    BASE + "/contributors": {"contributors": [], "total": 0},
}
SENTINEL = "Synthetic private vote failure sentinel"
COPY = {
    "en": (
        "Votes",
        "Please wait before voting again.",
        "Could not submit your vote. Try again.",
        "Upvote " + TITLE,
        "Downvote " + TITLE,
        "Ownership",
        "Overview",
        "No agent contributors yet.",
    ),
    "ru": (
        "Голоса",
        "Подождите перед повторным голосованием.",
        "Не удалось отправить голос. Попробуйте ещё раз.",
        "Поддержать проект " + TITLE,
        "Проголосовать против проекта " + TITLE,
        "Владение",
        "Обзор",
        "Агентов-участников пока нет.",
    ),
}


class VoteHTTP(OwnershipHTTP):
    def __init__(self, base_url):
        super().__init__(base_url, "project")
        self.reads, self.votes = [], []

    def route(self, route):
        request, target = route.request, urlparse(route.request.url)
        if target.scheme != "http" or target.netloc != self.origin:
            return self.reject(route)
        if request.method == "GET" and target.path in READS and not target.query:
            if request.headers.get("authorization") is not None:
                return self.reject(route)
            self.reads.append({"method": "GET", "path": target.path, "query": "", "anonymous": True})
            return route.fulfill(json=READS[target.path])
        if request.method == "POST" and target.path == BASE + "/vote" and not target.query:
            body = request.post_data_json
            if (
                request.headers.get("authorization") is not None
                or not isinstance(body, dict)
                or set(body) != {"vote"}
                or type(body["vote"]) is not int
                or body["vote"] not in (1, -1)
            ):
                return self.reject(route)
            self.votes.append({"method": "POST", "path": target.path, "body": body, "anonymous": True})
            self.pending.append((route, "vote"))
        elif target.path.startswith("/api/"):
            return self.reject(route)
        else:
            return super().route(route)


class ProjectVoteTests(BrowserFixture, unittest.TestCase):
    options: Namespace

    def open_page(self, locale, width):
        self.locale, self.width, self.errors, self.records, self.focus, self.problems = locale, width, [], [], [], []
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=width < 1280,
            is_mobile=width < 1280,
            service_workers="block",
        )
        context.add_cookies([{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}])
        self.http = VoteHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__voteHeld", lambda: len(self.http.pending))
        self.page.goto(self.options.base_url + f"/projects/{PROJECT_ID}", wait_until="domcontentloaded")
        self.main = self.page.locator("#main-content")
        expect(self.page.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
        self.block = self.main.get_by_text(COPY[locale][0], exact=True).locator("..")
        expect(self.block.get_by_role("button")).to_have_count(2)
        self.settle()
        self.assertEqual(len(self.http.reads), 3)

    def settle(self):
        self.page.evaluate("""async()=>{await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);} """)

    def place(self, root):
        root.scroll_into_view_if_needed()
        root.evaluate(
            "el=>{const h=document.querySelector('header')?.getBoundingClientRect().bottom||0;window.scrollBy(0,el.getBoundingClientRect().top-h-16)}"
        )
        self.settle()

    def button(self, index):
        return self.block.get_by_role("button").nth(index)

    def native(self, target):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.place(target)

    def counts(self):
        return [
            self.button(index).evaluate("el=>el.innerText.replace(/[↑↓\\s\\u00a0\\u202f]/g,'')") for index in (0, 1)
        ]

    def check_counts(self, expected, state):
        actual = self.counts()
        if actual != [str(value) for value in expected]:
            self.problems.append({"state": state, "counts": actual, "expected": list(expected)})

    def check_copy(self, index, state):
        notice = self.block.get_by_text(COPY[self.locale][index], exact=True)
        if notice.count() != 1 or not notice.is_visible():
            self.problems.append({"state": state, "missing_copy": COPY[self.locale][index]})

    def save(self, state):
        self.settle()
        self.place(self.block)
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": self.block.evaluate(METRICS),
            "counts": self.counts(),
            "disabled": [self.button(i).is_disabled() for i in (0, 1)],
            "labels": [self.button(i).get_attribute("aria-label") for i in (0, 1)],
            "focus": list(self.focus),
            "problems": list(self.problems),
            "votes": list(self.http.votes),
            "reads": list(self.http.reads),
            "unknown_http": list(self.http.unexpected),
            "page_errors": list(self.errors),
        }
        self.records.append(record)
        stem = f"project-detail-votes-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        expect(self.block.get_by_text(SENTINEL, exact=False)).to_have_count(0)
        expect(self.block.get_by_role("button")).to_have_count(2)

    def held(self):
        self.page.evaluate("""async()=>{const end=performance.now()+7000;while(await window.__voteHeld()!==1){
            if(performance.now()>end)throw Error('Expected one held anonymous vote');await new Promise(requestAnimationFrame);}}""")

    def start_vote(self, index, expected):
        target = self.button(index)
        self.native(target)
        self.focus.append({"focus": target.evaluate(FOCUS), "target": target.evaluate(TARGET)})
        before = len(self.http.votes)
        self.page.keyboard.press("Enter")
        self.held()
        expect(self.button(0)).to_be_disabled()
        expect(self.button(1)).to_be_disabled()
        self.page.keyboard.press("Enter")
        self.settle()
        self.assertEqual(len(self.http.votes), before + 1)
        self.assertEqual(self.http.votes[-1]["body"], {"vote": 1 if index == 0 else -1})
        self.check_counts(expected, "pending")

    def complete(self, status, counts=(13, 2)):
        self.held()
        route, marker = self.http.pending.pop()
        self.assertEqual(marker, "vote")
        if status == 0:
            route.abort("failed")
        elif status >= 400:
            route.fulfill(status=status, json={"detail": SENTINEL})
        else:
            up, down = counts
            route.fulfill(json={"project_id": PROJECT_ID, "votes_up": up, "votes_down": down, "score": up - down})
        self.settle()
        expect(self.button(0)).to_be_enabled()
        expect(self.button(1)).to_be_enabled()

    def visit_ownership_and_back(self):
        before_reads, before_votes = list(self.http.reads), list(self.http.votes)
        for label in COPY[self.locale][5:7]:
            self.native(self.page.get_by_role("button", name=label, exact=True))
            self.page.keyboard.press("Enter")
            self.settle()
            if label == COPY[self.locale][5]:
                expect(self.main.get_by_text(COPY[self.locale][7], exact=True)).to_be_visible()
        expect(self.block.get_by_role("button")).to_have_count(2)
        self.assertEqual(self.http.reads, before_reads)
        self.assertEqual(self.http.votes, before_votes)
        self.check_counts((13, 2), "ownership-and-back")

    def metric_issues(self, record):
        m = record["metrics"]
        issues = [{"font": item} for item in m["text"] if item["size"] < 12]
        issues.extend(
            {"target": item}
            for item in m["targets"]
            if item["width"] < 44 or item["height"] < 44 or not item["contained"]
        )
        issues.extend({"clip": item} for item in m["clipped"])
        if m["document_width"] > self.width:
            issues.append({"document_width": m["document_width"]})
        if record["labels"] != list(COPY[self.locale][3:5]):
            issues.append({"labels": record["labels"], "expected": list(COPY[self.locale][3:5])})
        return [{"state": record["state"], **issue} for issue in issues]

    def verify(self):
        issues = self.problems + [issue for record in self.records for issue in self.metric_issues(record)]
        for item in self.focus:
            if not all(item["focus"][key] for key in ("active", "visible", "painted")):
                issues.append({"focus": item})
            t = item["target"]
            if t["font"] < 12 or t["width"] < 44 or t["height"] < 44 or not t["contained"] or not t["in_view"]:
                issues.append({"focused_target": t})
        self.assertEqual(issues, [])

    def run_flow(self):
        self.check_counts((12, 2), "initial")
        self.save("initial")
        self.start_vote(0, (12, 2))
        self.save("up-pending")
        self.complete(429)
        self.check_counts((12, 2), "up-429")
        self.check_copy(1, "up-429")
        self.save("up-429")
        self.start_vote(0, (12, 2))
        self.complete(200)
        self.check_counts((13, 2), "up-success")
        self.visit_ownership_and_back()
        self.save("up-success-retained")
        self.start_vote(1, (13, 2))
        self.save("down-pending")
        self.complete(503 if self.locale == "en" else 0)
        self.check_counts((13, 2), "down-error")
        self.check_copy(2, "down-error")
        self.save("down-error")
        self.start_vote(1, (13, 2))
        self.complete(200, (12, 3))
        self.check_counts((12, 3), "down-recovered")
        expect(self.block.get_by_text(COPY[self.locale][2], exact=True)).to_have_count(0)
        self.save("down-recovered")
        self.assertEqual(
            [vote["body"] for vote in self.http.votes], [{"vote": 1}, {"vote": 1}, {"vote": -1}, {"vote": -1}]
        )
        self.assertEqual(len(self.http.reads), 3)
        self.verify()

    def test_anonymous_vote_errors_retry_and_counts_survive_section_switches(self):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        self.run_flow()
                    finally:
                        self.doCleanups()


if __name__ == "__main__":
    ProjectVoteTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
