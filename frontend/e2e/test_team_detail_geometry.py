"""Measure remaining team metadata on actual pages with synthetic public reads."""

import json
import re
import unittest
from argparse import Namespace

from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import expect
from test_public_chat_accessibility import FOCUS
from test_team_detail_reads import (
    AGENT_ID,
    API,
    COPY,
    DETAIL,
    MISSING,
    PROJECT_ID,
    TeamReadHTTP,
)

NAME = "SyntheticReadableTeamName" * 4
HUMAN = "SyntheticReadableHumanName" * 3
AGENT = "SyntheticReadableAgentName" * 3
HANDLE = "synthetic-readable-agent-handle-" * 2
PROJECT_TITLE = "SyntheticReadableLinkedProjectTitle" * 5
DESCRIPTION = "Synthetic full team description " * 8
DATA = {
    "id": API.rsplit("/", 1)[1],
    "name": NAME,
    "creator_name": HUMAN,
    "description": DESCRIPTION,
    "avatar_url": None,
    "created_at": "2026-10-20T09:00:00Z",
    "members": [
        {
            "id": "synthetic-agent-membership",
            "agent_id": AGENT_ID,
            "user_id": None,
            "name": AGENT,
            "handle": HANDLE,
            "role": "owner",
            "member_type": "agent",
            "joined_at": "2026-10-20T09:00:00Z",
        },
        {
            "id": "synthetic-human-membership",
            "agent_id": None,
            "user_id": "00000000-0000-4000-8000-000000000045",
            "name": HUMAN,
            "handle": None,
            "role": "member",
            "member_type": "user",
            "joined_at": "2026-10-19T09:00:00Z",
        },
    ],
    "projects": [
        {
            "id": PROJECT_ID,
            "title": PROJECT_TITLE,
            "agent_name": AGENT,
            "description": "Synthetic project preview " * 8,
            "status": "active",
            "repo_url": None,
            "deploy_url": None,
        }
    ],
}
EMPTY = dict(DATA, projects=[])
METRICS = """main=>{
 const text=[],targets=[],clipped=[],ellipsis=[];
 const breadcrumb=main.querySelector('a[href="/teams"]')?.parentElement;
 const preview=el=>el.closest('a[href^="/projects/"]')?.querySelector('p')===el;
 const excerpt=el=>el===breadcrumb?.lastElementChild;
 const decor=el=>el.closest('svg,[aria-hidden="true"]')||
   (/^[A-Z/|.!]+$/.test(el.textContent.trim())&&el.parentElement?.classList.contains('justify-center'))||
   (el.textContent.trim().length===2&&getComputedStyle(el).textTransform==='uppercase'&&
    ['w-7','h-7','justify-center'].every(c=>el.parentElement?.classList.contains(c)));
 const cssHidden=s=>s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0||s.clipPath==='inset(50%)';
 const clipHidden=s=>{const c=s.clip.match(/-?[0-9.]+px/g)?.map(parseFloat);
   return /^(absolute|fixed)$/.test(s.position)&&c?.length===4&&(c[2]<=c[0]||c[1]<=c[3]);};
 const painted=el=>{for(let n=el;n;n=n.parentElement){const s=getComputedStyle(n);
   if(cssHidden(s)||clipHidden(s))return false;}return el.getClientRects().length>0;};
 const bounds=el=>{const b=[{left:0,right:innerWidth}];
   const card=el.closest('.member-card');if(card){const r=card.getBoundingClientRect();b.push({left:r.left,right:r.right});}
   for(let n=el.parentElement;n;n=n.parentElement)if(/^(hidden|clip|auto|scroll)$/.test(getComputedStyle(n).overflowX)){
     const r=n.getBoundingClientRect(),s=getComputedStyle(n);b.push({left:r.left+parseFloat(s.borderLeftWidth),right:r.right-parseFloat(s.borderRightWidth)});}
   return b;};
 const measureTarget=el=>{if(painted(el)){
   const r=el.getBoundingClientRect();targets.push({name:el.getAttribute('aria-label')||el.textContent.trim(),
     width:r.width,height:r.height,contained:bounds(el).every(b=>r.left>=b.left&&r.right<=b.right)});}};
 const measureRange=(n,el)=>{const range=document.createRange();range.selectNodeContents(n);
   for(const r of range.getClientRects())for(const b of bounds(el))if(r.left<b.left||r.right>b.right)
     clipped.push({text:n.textContent.trim(),left:r.left,right:r.right,bound:b});};
 const measureEllipsis=(n,el)=>{const s=getComputedStyle(el);
   if(s.textOverflow==='ellipsis'&&el.scrollWidth>el.clientWidth)ellipsis.push(n.textContent.trim());};
 const measureText=n=>{const el=n.parentElement;
   if(!n.textContent.trim()||!painted(el)||decor(el)||n.textContent.trim()==='/')return;
   text.push({text:n.textContent.trim(),size:parseFloat(getComputedStyle(el).fontSize)});
   if(preview(el)||excerpt(el))return;
   measureRange(n,el);measureEllipsis(n,el);
 };
 for(const el of main.querySelectorAll('a[href],button'))measureTarget(el);
 const walker=document.createTreeWalker(main,NodeFilter.SHOW_TEXT);
 while(walker.nextNode())measureText(walker.currentNode);
 return {text,targets,clipped,ellipsis,document_width:document.documentElement.scrollWidth};
}"""


class TeamGeometryTests(BrowserFixture, unittest.TestCase):
    """Collect all six reached states before asserting remaining geometry."""

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
        self.http = TeamReadHTTP(self.options.base_url)
        self.addCleanup(self.http.close, context)
        context.route("**/*", self.http.route)
        context.add_init_script("""window.__teamStreams=[];window.EventSource=class {
            constructor(url){window.__teamStreams.push(String(url));throw Error('Retired team stream');}};""")
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.expose_function("__heldTeamGeometry", lambda: len(self.http.pending))
        self.main = self.page.locator("#main-content")
        self.page.goto(self.options.base_url + DETAIL, wait_until="domcontentloaded")
        self.held()

    def held(self):
        self.page.evaluate("""async()=>{const end=performance.now()+7000;
            while(await window.__heldTeamGeometry()!==1){if(performance.now()>end)
            throw Error('Expected one held team GET');await new Promise(requestAnimationFrame);}}""")

    def settle(self):
        self.page.evaluate("""async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);
            await Promise.all(document.getAnimations().filter(a=>a instanceof CSSAnimation&&
            Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a=>a.finished.catch(()=>{})));
            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}""")

    def complete(self, data, status=200):
        self.held()
        self.http.pending.pop().fulfill(status=status, json=data)
        self.settle()

    def place(self, target):
        target.scroll_into_view_if_needed()
        target.evaluate("""el=>{const h=document.querySelector('header')?.getBoundingClientRect().bottom||0;
            window.scrollBy(0,el.getBoundingClientRect().top-h-16)}""")
        self.settle()

    def native(self, target, activate=False):
        for _ in range(self.page.locator("a[href],button,input,textarea,summary").count() * 2 + 4):
            self.page.keyboard.press("Tab")
            if target.evaluate("el=>el===document.activeElement"):
                break
        expect(target).to_be_focused()
        self.place(target)
        self.focus.append(target.evaluate(FOCUS))
        if activate:
            self.page.keyboard.press("Enter")
            self.settle()

    def section(self, projects):
        index = 6 if projects else 5
        label = re.sub(r"\(1\)$", "", COPY[self.locale][index]).strip()
        return self.main.get_by_role("button", name=re.compile("^" + re.escape(label) + r"\s*\("))

    def save(self, state, target):
        self.settle()
        self.place(target)
        record = {
            "locale": self.locale,
            "width": self.width,
            "state": state,
            "metrics": self.main.evaluate(METRICS),
            "focus": list(self.focus),
            "calls": list(self.http.calls),
            "page_errors": list(self.errors),
            "unknown_http": list(self.http.unexpected),
            "stream_attempts": self.page.evaluate("window.__teamStreams"),
        }
        self.records.append(record)
        stem = f"team-geometry-{self.locale}-{self.width}-{state}"
        self.page.screenshot(path=self.options.screenshots / f"{stem}.png", animations="disabled")
        (self.options.screenshots / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.assertEqual(self.http.unexpected, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(record["stream_attempts"], [])

    def members(self):
        heading = self.main.get_by_role("heading", level=1, name=NAME, exact=True)
        expect(heading).to_be_visible()
        expect(self.main.get_by_text(DESCRIPTION, exact=True)).to_be_visible()
        expect(self.main.get_by_text("@" + HANDLE, exact=True)).to_be_visible()
        owner = self.main.get_by_role("link", name=re.compile(AGENT))
        expect(owner).to_have_attribute("href", f"/agents/{AGENT_ID}")
        for href in ("/", "/teams"):
            self.native(self.main.locator(f'a[href="{href}"]'))
        self.native(owner)
        self.save("members-ready", heading)

    def local_sections(self):
        count = self.http.calls.count(API)
        self.native(self.section(True), activate=True)
        project = self.main.get_by_role("link", name=re.compile(PROJECT_TITLE))
        expect(project).to_have_attribute("href", f"/projects/{PROJECT_ID}")
        expect(project.get_by_text(AGENT, exact=False)).to_be_visible()
        self.native(project)
        self.save("projects-ready", project)
        self.native(self.section(False), activate=True)
        owner = self.main.get_by_role("link", name=re.compile(AGENT))
        expect(owner).to_be_visible()
        self.native(owner)
        self.save("members-restored", owner)
        self.assertEqual(self.http.calls.count(API), count)
        expect(self.main.get_by_role("button")).to_have_count(2)
        expect(self.main.get_by_role("textbox")).to_have_count(0)

    def entry_states(self):
        self.page.reload(wait_until="domcontentloaded")
        self.held()
        self.complete(EMPTY)
        self.native(self.section(True), activate=True)
        empty = self.main.get_by_text(
            "No projects linked" if self.locale == "en" else "Нет связанных проектов", exact=True
        )
        expect(empty).to_be_visible()
        self.save("projects-empty", empty)
        self.page.goto(self.options.base_url + MISSING, wait_until="domcontentloaded")
        self.held()
        self.complete({"detail": "Synthetic missing team"}, status=404)
        missing = self.main.get_by_text(COPY[self.locale][3], exact=True)
        expect(missing).to_be_visible()
        back = self.main.get_by_role("link", name=COPY[self.locale][4], exact=True)
        expect(back).to_have_attribute("href", "/teams")
        self.native(back)
        self.save("not-found-back-focused", back)
        self.assertEqual(self.http.calls.count(API), 2)

    def metric_issues(self, record):
        metrics = record["metrics"]
        issues = [{"state": record["state"], "font": item} for item in metrics["text"] if item["size"] < 12]
        issues.extend({"state": record["state"], "clip": item} for item in metrics["clipped"])
        issues.extend({"state": record["state"], "ellipsis": item} for item in metrics["ellipsis"])
        for item in metrics["targets"]:
            if item["width"] < 44 or item["height"] < 44 or not item["contained"]:
                issues.append({"state": record["state"], "target": item})
        if metrics["document_width"] > self.width:
            issues.append({"state": record["state"], "document_width": metrics["document_width"]})
        return issues

    def verify(self):
        self.assertEqual(len(self.records), 6)
        issues = [issue for record in self.records for issue in self.metric_issues(record)]
        issues.extend(
            {"focus": item} for item in self.focus if not all(item[k] for k in ("active", "visible", "painted"))
        )
        self.assertEqual(issues, [])

    def test_remaining_team_geometry(self):
        for locale in COPY:
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    try:
                        self.open_page(locale, width)
                        loading = self.main.get_by_text(COPY[locale][0], exact=True)
                        expect(loading).to_be_visible()
                        self.save("loading", loading)
                        self.complete(DATA)
                        self.members()
                        self.local_sections()
                        self.entry_states()
                        self.verify()
                    finally:
                        self.doCleanups()


if __name__ == "__main__":
    TeamGeometryTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
