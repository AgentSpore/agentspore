"""Exercise owner workspace states against an explicit local build with synthetic HTTP."""

import json
import re
import unittest
from urllib.parse import parse_qs, urlparse

import test_auth_forms as auth
import test_battles_accessibility as battles
from auth_form_fixtures import USER, AuthHTTP
from playwright.sync_api import expect

PREFIX = "/api/v1/users/me/"
OVERVIEW, TASKS = PREFIX + "agent-workspace", PREFIX + "agent-tasks"
ACTOR = {"id": "00000000-0000-4000-8000-000000000001", "name": "Synthetic agent", "handle": None}
COUNTS = dict.fromkeys(
    ("awaiting_submission", "submitted", "changes_requested", "accepted", "rejected", "cancelled"),
    0,
)
COPY = {
    "en": {
        "signIn": "Sign in to see your agents",
        "setup": "Connection instructions",
        "loading": "Reading your workspace…",
        "loadingDetail": "Reading the selected result…",
        "empty": "No agents linked to this account yet.",
        "refresh": "Refresh workspace",
        "retry": "Try again",
        "next": "Next page",
        "previous": "Previous page",
        "all": "All",
        "accepted": "Accepted",
        "agents": "Your agents",
        "select": "Choose a task to read its result and decision history.",
        "expand": "Show all 8 received events",
        "collapse": "Show latest 5 events",
        "noTasks": "No tasks yet. Your agents can create or claim tasks through the SDK or MCP.",
        "guide": "Read the SDK guide",
    },
    "ru": {
        "signIn": "Войти и посмотреть агентов",
        "setup": "Как подключить агента",
        "loading": "Загружаем рабочую область…",
        "loadingDetail": "Загружаем выбранный результат…",
        "empty": "К этому аккаунту пока не привязаны агенты.",
        "refresh": "Обновить рабочую область",
        "retry": "Повторить попытку",
        "next": "Следующая страница",
        "previous": "Предыдущая страница",
        "all": "Все",
        "accepted": "Принято",
        "agents": "Ваши агенты",
        "select": "Выберите задание, чтобы увидеть результат и историю решений.",
        "expand": "Показать все полученные события: 8",
        "collapse": "Показать 5 последних событий",
        "noTasks": "Заданий пока нет. Агенты могут создавать или брать задания через SDK или MCP.",
        "guide": "Открыть инструкцию SDK",
    },
}


def task(index):
    """Mirror WorkspaceTask metadata; private results are added only to detail replies."""
    return {
        "id": f"00000000-0000-4000-8000-{index + 100:012d}",
        "title": f"Synthetic task {index}",
        "description": None,
        "project": None,
        "requester": ACTOR,
        "claimant": ACTOR,
        "viewer_roles": ["requester", "claimant"],
        "task_status": "in_progress",
        "review_state": "accepted" if index % 2 else "submitted",
        "review_version": 1,
        "relationship": "internal",
        "created_at": "2026-10-12T10:15:00Z",
        "learning_reservation": {"amount": 30, "status": "reserved"},
    }


def overview(long_values=False):
    """Use accepted integer and 200-character registration-name boundaries."""
    amount = 9007199254740991 if long_values else 70
    budget = dict.fromkeys(("available", "reserved", "transferred", "received", "refunded"), amount)
    return {
        "agents": [
            {
                **ACTOR,
                "name": "A" * 200 if long_values else ACTOR["name"],
                "specialization": "programmer",
                "is_active": True,
                "created_at": None,
                "last_heartbeat": None,
                "budget": {**budget, "initial_grant_applied": True},
            }
        ],
        "review_counts": COUNTS,
    }


def detail(long_values=False):
    """Mirror the safe receipt projection with eight newest-first synthetic events."""
    actor = {**ACTOR, "name": "A" * 200 if long_values else ACTOR["name"]}
    history = [
        {
            "actor": actor,
            "action": "submit",
            "reason": f"Synthetic receipt {version}",
            "state": "submitted",
            "version": version,
            "created_at": "2026-10-12T10:16:00Z",
        }
        for version in range(8, 0, -1)
    ]
    return {**task(0), "result": "<script>synthetic()</script>", "history": history}


def page_resource(query):
    """Accept only the reader's bounded, single-valued pagination contract."""
    if set(query) != {"limit", "offset"} or query["limit"] != ["50"]:
        return None
    if len(query["offset"]) != 1 or not re.fullmatch(r"\d{1,5}", query["offset"][0]):
        return None
    offset = int(query["offset"][0])
    return f"page-{offset}" if offset <= 10000 else None


class WorkspaceHTTP(AuthHTTP):
    """Hold only exact authenticated workspace GETs; reject every other API route."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.held = {}

    def accepts_page(self, request, target):
        """Allow the workspace document and its exact framework prefetch query."""
        if target.path in (f"/agents/{ACTOR['id']}", "/docs/connect"):
            query = parse_qs(target.query, keep_blank_values=True)
            return (
                request.method == "GET"
                and set(query) == {"_rsc"}
                and len(query["_rsc"]) == 1
                and request.headers.get("rsc") == "1"
                and request.headers.get("next-router-prefetch") == "1"
            )
        if target.path != "/workspace":
            return super().accepts_page(request, target)
        query = parse_qs(target.query, keep_blank_values=True)
        return request.method == "GET" and (
            not query
            or (
                set(query) == {"_rsc"}
                and len(query["_rsc"]) == 1
                and request.headers.get("rsc") == "1"
                and (
                    request.headers.get("next-router-prefetch") == "1"
                    or request.headers.get("next-router-state-tree") is not None
                )
            )
        )

    def route(self, route):
        """Require loopback origin, bodyless GET, known owner and bounded page parameters."""
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != self.origin or target.scheme != "http":
            self.reject(route)
            return
        if not target.path.startswith("/api/"):
            super().route(route)
            return
        owner = request.headers.get("authorization", "").removeprefix("Bearer ")
        if (
            request.method != "GET"
            or request.post_data
            or owner not in ("synthetic-a", "synthetic-b")
        ):
            self.reject(route)
            return
        resource = None
        if target.path == OVERVIEW and not target.query:
            resource = "overview"
        elif target.path == TASKS:
            resource = page_resource(parse_qs(target.query, keep_blank_values=True))
        elif (
            re.fullmatch(TASKS + r"/[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", target.path)
            and not target.query
        ):
            resource = "detail"
        elif target.path == "/api/v1/auth/me" and not target.query:
            self.requests.append({"owner": owner, "resource": "profile"})
            route.fulfill(json=USER)
            return
        if resource is None or (owner, resource) in self.held:
            self.reject(route)
            return
        self.requests.append({"owner": owner, "resource": resource})
        self.held[(owner, resource)] = route


class WorkspaceStateTests(unittest.TestCase):
    """Exercise actual Workspace, Header and locale UI in all six supported layouts."""

    setUpClass = classmethod(auth.AuthFormTests.setUpClass.__func__)
    tearDownClass = classmethod(auth.AuthFormTests.tearDownClass.__func__)
    assert_action = auth.AuthFormTests.assert_action

    def open_workspace(self, locale, width, mode):
        """Use fresh synthetic storage and block every unrecognized request."""
        context = self.browser.new_context(
            viewport={"width": width, "height": 900}, locale=locale, service_workers="block"
        )
        self.addCleanup(context.close)
        context.add_cookies(
            [{"name": "agentspore_locale", "value": locale, "url": self.options.base_url}]
        )
        context.add_init_script(
            f"""
            const storage = window.localStorage;
            if ({str(mode != "anonymous").lower()}) storage.setItem('access_token', 'synthetic-a');
            window.workspaceStorageDenied = {str(mode == "blocked").lower()};
            Object.defineProperty(window, 'localStorage', {{get() {{
                if (window.workspaceStorageDenied) throw """
            f"""new DOMException('Storage denied', 'SecurityError');
                return storage;
            }}}});
        """
        )
        self.http, self.errors = WorkspaceHTTP(self.options.base_url), []
        self.copy, self.width, self.locale = COPY[locale], width, locale
        self.tag = f"workspace-{locale}-{width}-{mode}"
        context.route("**/*", self.http.route)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.expose_function(
            "workspaceRouteHeld", lambda owner, resource: (owner, resource) in self.http.held
        )
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(self.options.base_url + "/workspace")
        self.main = page.get_by_role("main")
        expect(self.main.get_by_role("heading", level=1)).to_be_visible()
        page.evaluate("document.fonts.ready")
        return page

    def capture(self, page, state):
        """Retain the accepted all-control keyboard, hit-area, overflow and PNG checks."""
        focus = page.evaluate(
            "({tag: document.activeElement.tagName, "
            "name: document.activeElement.textContent, "
            "id: document.activeElement.id})"
        )
        target = self.options.screenshots / f"{self.tag}-{state}-focus.json"
        target.write_text(json.dumps(focus, ensure_ascii=False, indent=2))
        page.evaluate(
            "Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity)"
            ".map(a => a.finished.catch(() => {})))"
        )
        auth.AuthFormTests.capture(self, page, state)
        rects = self.main.evaluate("""main => {
            const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT), result = [];
            while (walker.nextNode()) {
                const node = walker.currentNode;
                if (!node.textContent.trim() || !node.parentElement.checkVisibility()) continue;
                const range = document.createRange(); range.selectNodeContents(node);
                for (const rect of range.getClientRects()) result.push({
                    text: node.textContent, left: rect.left, right: rect.right});
            }
            return result;
        }""")
        target.with_name(f"{self.tag}-{state}-text.json").write_text(json.dumps(rects, indent=2))
        for rect in rects:
            self.assertGreaterEqual(rect["left"], -1, rect)
            self.assertLessEqual(rect["right"], self.width + 1, rect)

    def wait_for_route(self, resource, owner="synthetic-a"):
        """Wait until the exact owner and resource route has been intercepted."""
        self.main.page.evaluate(
            """async ([owner, resource]) => {
                const deadline = performance.now() + 5000;
                while (!(await window.workspaceRouteHeld(owner, resource))) {
                    if (performance.now() >= deadline) {
                        throw new Error(
                            `Timed out waiting for held workspace route: ${owner}/${resource}`
                        );
                    }
                    await new Promise(requestAnimationFrame);
                }
            }""",
            [owner, resource],
        )

    def reply(self, resource, payload, status=200, owner="synthetic-a"):
        """Resolve exactly the intercepted resource without changing another pending read."""
        self.wait_for_route(resource, owner)
        self.http.held.pop((owner, resource)).fulfill(status=status, json=payload)

    def page_reply(self, offset=0, total=2, owner="synthetic-a"):
        """Return a complete deterministic page for the captured owner and total."""
        payload = {
            "items": [task(index) for index in range(offset, min(offset + 50, total))],
            "limit": 50,
            "offset": offset,
            "total": total,
        }
        self.reply(f"page-{offset}", payload, owner=owner)
        return payload

    def loaded(self, page, total=2, owner="synthetic-a"):
        """Resolve overview and the first page, then observe a real task row."""
        self.reply("overview", overview(), owner=owner)
        self.page_reply(total=total, owner=owner)
        expect(
            self.main.get_by_role("button", name=re.compile(r"^Synthetic task 0\b"))
        ).to_be_visible()

    def press(self, page, name, resource):
        """Activate by keyboard and observe the exact next HTTP read."""
        control = self.main.get_by_role("button", name=name, exact=True)
        control.focus()
        with page.expect_request(
            lambda request: (
                request.method == "GET"
                and urlparse(request.url).path
                == (
                    OVERVIEW
                    if resource == "overview"
                    else TASKS
                    if resource.startswith("page-")
                    else TASKS + "/" + task(0)["id"]
                )
            )
        ):
            control.press("Enter")
        return control

    def anonymous_flow(self, page, mode):
        """Anonymous and denied storage keep setup usable without private requests."""
        expect(self.main.get_by_role("link", name=self.copy["signIn"], exact=True)).to_be_visible()
        expect(
            self.main.get_by_role("heading", name=self.copy["setup"], exact=True)
        ).to_be_visible()
        expect(self.main.get_by_role("link", name=self.copy["guide"], exact=True)).to_be_visible()
        self.assertEqual(self.http.requests, [])
        self.capture(page, mode)
        if mode == "blocked":
            page.evaluate(
                "window.workspaceStorageDenied = false; window.dispatchEvent(new Event('storage'))"
            )
            expect(self.main.get_by_text(self.copy["loading"], exact=True)).to_be_visible()
            self.loaded(page)
            self.capture(page, "storage-recovered")

    def privacy_flow(self, page):
        """Storage denial hides owner panels and an aborted detail cannot restore them."""
        self.loaded(page)
        self.press(page, re.compile(r"^Synthetic task 0\b"), "detail")
        expect(self.main.get_by_text(self.copy["loadingDetail"], exact=True)).to_be_visible()
        before = list(self.http.requests)
        page.evaluate(
            "window.workspaceStorageDenied = true; window.dispatchEvent(new Event('storage'))"
        )
        expect(self.main.get_by_role("link", name=self.copy["signIn"], exact=True)).to_be_visible()
        self.reply("detail", detail())
        expect(
            self.main.get_by_role("heading", name=self.copy["agents"], exact=True)
        ).to_have_count(0)
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_have_count(0)
        self.assertEqual(self.http.requests, before)
        self.capture(page, "storage-lost")
        page.evaluate(
            "window.workspaceStorageDenied = false; window.dispatchEvent(new Event('storage'))"
        )
        expect(self.main.get_by_text(self.copy["loading"], exact=True)).to_be_visible()
        self.loaded(page)
        expect(self.main.get_by_text(self.copy["select"], exact=True)).to_be_visible()
        self.capture(page, "storage-recovered-unselected")

    def result_flow(self, page, long_values=False):
        """Read text safely and expand real receipt history without losing locale state."""
        self.press(page, re.compile(r"^Synthetic task 0\b"), "detail")
        self.reply("detail", detail(long_values))
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_be_visible()
        expect(page.locator("#task-detail script")).to_have_count(0)
        expect(self.main.get_by_text("Synthetic receipt 4", exact=True)).to_be_visible()
        expect(self.main.get_by_text("Synthetic receipt 3", exact=True)).to_have_count(0)
        self.capture(page, "long-values" if long_values else "selected-result")
        expand = self.main.get_by_role("button", name=self.copy["expand"], exact=True)
        history_id = expand.get_attribute("aria-controls")
        expand.press("Enter")
        collapse = self.main.get_by_role("button", name=self.copy["collapse"], exact=True)
        expect(collapse).to_have_attribute("aria-expanded", "true")
        expect(collapse).to_have_attribute("aria-controls", history_id)
        expect(self.main.get_by_text("Synthetic receipt 1", exact=True)).to_be_visible()
        self.capture(page, "history-expanded")
        collapse.press("Enter")
        expect(self.main.get_by_text("Synthetic receipt 1", exact=True)).to_have_count(0)
        language = page.get_by_role(
            "button", name="Русский" if self.locale == "en" else "English", exact=True
        ).first
        language.press("Enter")
        expect(page.locator("html")).to_have_attribute(
            "lang", "ru" if self.locale == "en" else "en"
        )
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_be_visible()
        self.assertEqual(len([row for row in self.http.requests if row["resource"] == "detail"]), 1)
        self.capture(page, "locale-selected")

    def error_flow(self, page):
        """Observe loading, empty, errors and scoped retries through all read layers."""
        expect(self.main.get_by_text(self.copy["loading"], exact=True)).to_be_visible()
        self.capture(page, "overview-loading")
        self.reply("overview", {"agents": [], "review_counts": COUNTS})
        self.page_reply(total=0)
        expect(self.main.get_by_text(self.copy["empty"], exact=True)).to_be_visible()
        self.capture(page, "empty-account")
        self.press(page, self.copy["refresh"], "overview")
        self.reply("overview", overview())
        self.page_reply(total=0)
        expect(self.main.get_by_text(self.copy["noTasks"], exact=True)).to_be_visible()
        self.capture(page, "empty-tasks")
        self.press(page, self.copy["refresh"], "overview")
        self.reply("overview", overview())
        self.reply("page-0", {}, 503)
        expect(self.main.get_by_role("alert")).to_be_visible()
        self.capture(page, "list-error")
        self.press(page, self.copy["retry"], "overview")
        self.loaded(page)
        self.press(page, re.compile(r"^Synthetic task 0\b"), "detail")
        self.reply("detail", {}, 503)
        expect(page.locator("#task-detail").get_by_role("alert")).to_be_visible()
        self.capture(page, "detail-error")
        self.press(page, self.copy["retry"], "detail")
        self.reply("detail", detail())
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_be_visible()
        self.press(page, self.copy["refresh"], "overview")
        self.reply("overview", {}, 503)
        self.page_reply()
        expect(self.main.get_by_role("alert")).to_be_visible()
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_have_count(0)
        self.capture(page, "overview-error")
        self.press(page, self.copy["retry"], "overview")
        self.loaded(page)
        self.reply("detail", detail())
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_be_visible()
        self.capture(page, "overview-recovered")

    def pagination_flow(self, page):
        """Keep filter/focus through paging and recover both zero and nonzero clamp offsets."""
        self.loaded(page, total=101)
        accepted = self.main.get_by_role("button", name=self.copy["accepted"], exact=True)
        accepted.press("Enter")
        next_button = self.press(page, self.copy["next"], "page-50")
        heading = page.locator("#tasks-heading")
        expect(heading).to_be_focused()
        self.capture(page, "next-page-loading")
        heading.focus()
        self.page_reply(offset=50, total=101)
        expect(
            self.main.get_by_role("button", name=re.compile(r"^Synthetic task 51\b"))
        ).to_be_visible()
        expect(accepted).to_have_attribute("aria-pressed", "true")
        expect(
            self.main.get_by_role("button", name=re.compile(r"^Synthetic task 50\b"))
        ).to_have_count(0)
        expect(heading).to_be_focused()
        self.press(page, self.copy["next"], "page-100")
        self.page_reply(offset=100, total=101)
        expect(next_button).to_be_disabled()
        focus = page.evaluate(
            "({tag: document.activeElement.tagName, name: document.activeElement.textContent})"
        )
        target = self.options.screenshots / f"{self.tag}-last-page-native-focus"
        target.with_suffix(".json").write_text(json.dumps(focus, ensure_ascii=False, indent=2))
        page.screenshot(path=str(target.with_suffix(".png")))
        self.assertNotEqual(page.evaluate("document.activeElement.tagName"), "BODY")
        expect(heading).to_be_focused()
        self.capture(page, "last-page-filtered")
        self.press(page, self.copy["refresh"], "overview")
        self.reply("overview", overview())
        self.page_reply(offset=100, total=100)
        expect(
            self.main.get_by_text(
                "Tasks 101–100 of 100" if self.locale == "en" else "Задания 101–100 из 100",
                exact=True,
            )
        ).to_have_count(0)
        self.capture(page, "nonzero-clamp-loading")
        self.page_reply(offset=50, total=100)
        expect(accepted).to_have_attribute("aria-pressed", "true")
        expect(
            self.main.get_by_role("button", name=re.compile(r"^Synthetic task 51\b"))
        ).to_be_visible()
        self.capture(page, "nonzero-clamp")
        self.press(page, self.copy["refresh"], "overview")
        self.reply("overview", overview())
        self.page_reply(offset=50, total=50)
        self.page_reply(total=50)
        expect(accepted).to_have_attribute("aria-pressed", "true")
        expect(
            self.main.get_by_role("button", name=re.compile(r"^Synthetic task 1\b"))
        ).to_be_visible()
        self.capture(page, "zero-clamp")

    def owner_flow(self, page):
        """A replacement account starts at page zero without the previous detail request."""
        self.loaded(page, total=101)
        self.press(page, re.compile(r"^Synthetic task 0\b"), "detail")
        self.reply("detail", detail())
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_be_visible()
        page.evaluate(
            "localStorage.setItem('access_token','synthetic-b'); "
            "window.dispatchEvent(new Event('storage'))"
        )
        expect(self.main.get_by_text(self.copy["loading"], exact=True)).to_be_visible()
        self.loaded(page, total=101, owner="synthetic-b")
        expect(self.main.get_by_text(self.copy["select"], exact=True)).to_be_visible()
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_have_count(0)
        reads = [row["resource"] for row in self.http.requests if row["owner"] == "synthetic-b"]
        self.assertEqual(sorted(reads), ["overview", "page-0", "profile"])
        expect(
            self.main.get_by_role("button", name=self.copy["all"], exact=True)
        ).to_have_attribute("aria-pressed", "true")
        self.capture(page, "replacement-owner")
        self.main.get_by_role("button", name=self.copy["accepted"], exact=True).press("Enter")
        self.press(page, self.copy["next"], "page-50")
        self.wait_for_route("page-50", owner="synthetic-b")
        before = len(self.http.requests)
        page.evaluate(
            "localStorage.setItem('access_token','synthetic-a'); "
            "window.dispatchEvent(new Event('storage'))"
        )
        expect(self.main.get_by_text(self.copy["loading"], exact=True)).to_be_visible()
        self.page_reply(offset=50, total=101, owner="synthetic-b")
        self.loaded(page)
        expect(
            self.main.get_by_role("button", name=self.copy["all"], exact=True)
        ).to_have_attribute("aria-pressed", "true")
        self.assertEqual(
            sorted(row["resource"] for row in self.http.requests[before:]),
            ["overview", "page-0", "profile"],
        )
        self.capture(page, "late-owner-page-ignored")

    def unauthorized_flow(self, page, resource):
        """An overview or detail 401 removes panels that were previously readable."""
        self.loaded(page)
        self.press(page, re.compile(r"^Synthetic task 0\b"), "detail")
        if resource == "detail":
            self.reply("detail", {}, 401)
        else:
            self.reply("detail", detail())
            expect(self.main.get_by_text(detail()["result"], exact=True)).to_be_visible()
            self.press(page, self.copy["refresh"], "overview")
            self.reply("overview", {}, 401)
            self.page_reply()
        expect(self.main.get_by_role("link", name=self.copy["signIn"], exact=True)).to_be_visible()
        expect(
            self.main.get_by_role("heading", name=self.copy["agents"], exact=True)
        ).to_have_count(0)
        expect(self.main.get_by_text(detail()["result"], exact=True)).to_have_count(0)
        self.capture(page, f"{resource}-unauthorized")

    def run_flow(self, page, mode):
        """Dispatch independent scenarios without mixing state between contexts."""
        if mode in ("anonymous", "blocked"):
            self.anonymous_flow(page, mode)
        elif mode == "long":
            self.reply("overview", overview(True))
            self.page_reply()
            self.result_flow(page, True)
        elif mode == "result":
            self.loaded(page)
            self.result_flow(page)
        elif mode.endswith("-auth"):
            self.unauthorized_flow(page, mode.removesuffix("-auth"))
        else:
            flows = {
                "privacy": self.privacy_flow,
                "errors": self.error_flow,
                "pagination": self.pagination_flow,
                "owner": self.owner_flow,
            }
            flows[mode](page)

    def test_workspace_states_in_six_layouts(self):
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                for mode in (
                    "anonymous",
                    "blocked",
                    "privacy",
                    "errors",
                    "pagination",
                    "owner",
                    "result",
                    "long",
                    "overview-auth",
                    "detail-auth",
                ):
                    with self.subTest(locale=locale, width=width, mode=mode):
                        page = self.open_workspace(locale, width, mode)
                        try:
                            self.run_flow(page, mode)
                        finally:
                            page.context.close()


if __name__ == "__main__":
    WorkspaceStateTests.options, unittest_args = battles.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
