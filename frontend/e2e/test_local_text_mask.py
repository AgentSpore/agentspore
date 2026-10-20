"""Probe local text masking with exact full-frame controls; never accept contrast."""

import base64
import hashlib
import json
import unittest
from argparse import Namespace

import test_project_cards as cards
from auth_form_fixtures import BrowserFixture, parse_options
from playwright.sync_api import Browser

STATE = """root=>{
 const properties=['outlineStyle','outlineColor','outlineWidth','outlineOffset',
 'borderRadius','borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius',
 'borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth',
 'borderTopColor','borderRightColor','borderBottomColor','borderLeftColor',
 'borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle',
 'boxShadow','background','backgroundColor','backgroundImage','backgroundClip','filter','backdropFilter',
 'opacity','transform','color','webkitTextFillColor','textShadow','mixBlendMode','font'];
 const identity=el=>({tag:el.tagName,id:el.id,index:[...document.body.querySelectorAll('*')].indexOf(el),
 href:el.getAttribute('href'),label:el.getAttribute('aria-label')});
 const elements=[root,...root.querySelectorAll('*')];
 for(let parent=root.parentElement;parent;parent=parent.parentElement)elements.push(parent);
 const text=[],walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
 while(walker.nextNode()){const node=walker.currentNode;if(!node.textContent.trim())continue;
 const range=document.createRange();range.selectNodeContents(node);
 text.push({text:node.textContent,rects:[...range.getClientRects()].map(r=>[r.x,r.y,r.width,r.height])});}
 return {active:identity(document.activeElement),activeFocusVisible:document.activeElement.matches(':focus-visible'),
 scroll:[scrollX,scrollY],viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,text,
 fontsLoaded:document.fonts.status,
 runningFiniteAnimations:document.getAnimations().filter(a=>a.playState!=='finished'&&
 a.effect?.getComputedTiming().iterations!==Infinity).map(a=>a.playState),
 hover:elements.filter(el=>el.matches(':hover')).map(identity),elements:elements.map(el=>{
 const style=getComputedStyle(el),rect=el.getBoundingClientRect();return {identity:identity(el),
 rect:[rect.x,rect.y,rect.width,rect.height],styles:Object.fromEntries(properties.map(key=>[key,style[key]])),
 focused:el===document.activeElement,focusVisible:el.matches(':focus-visible')};})};
}"""
DIFF = """async({first,second})=>{
 const decode=async encoded=>{
 const image=new Image();image.src='data:image/png;base64,'+encoded;await image.decode();
 if(image.width!==innerWidth||image.height!==innerHeight||devicePixelRatio!==1)
 throw Error('CSS/PNG coordinate mismatch');
 const canvas=Object.assign(document.createElement('canvas'),{width:image.width,height:image.height});
 const ctx=canvas.getContext('2d',{colorSpace:'srgb'});ctx.drawImage(image,0,0);
 const data=ctx.getImageData(0,0,image.width,image.height,{colorSpace:'srgb'});
 if(data.colorSpace!=='srgb')throw Error('non-sRGB screenshot');return data.data;};
 const a=await decode(first),b=await decode(second),changed=[];
 for(let offset=0;offset<a.length;offset+=4){
 const before=[...a.slice(offset,offset+4)],after=[...b.slice(offset,offset+4)];
 if(before.every((value,channel)=>value===after[channel]))continue;const pixel=offset/4;
 changed.push({x:pixel%innerWidth,y:Math.floor(pixel/innerWidth),before,after,
 delta:after.map((value,channel)=>value-before[channel])});}
 return {width:innerWidth,height:innerHeight,changedCount:changed.length,changed};
}"""


RECTS = """el=>{
 const result=[],walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
 while(walker.nextNode()){const range=document.createRange();range.selectNodeContents(walker.currentNode);
 for(const r of range.getClientRects()){let b=[Math.max(0,r.left),Math.max(0,r.top),
 Math.min(innerWidth,r.right),Math.min(innerHeight,r.bottom)];
 for(let p=el;p;p=p.parentElement){const s=getComputedStyle(p),r=p.getBoundingClientRect(),
 x=r.left+p.clientLeft,y=r.top+p.clientTop;
 if(/^(hidden|clip|auto|scroll)$/.test(s.overflowX)){b[0]=Math.max(b[0],x);b[2]=Math.min(b[2],x+p.clientWidth);}
 if(/^(hidden|clip|auto|scroll)$/.test(s.overflowY)){b[1]=Math.max(b[1],y);b[3]=Math.min(b[3],y+p.clientHeight);}}
 if(b[0]<b[2]&&b[1]<b[3])result.push(b);}}return result;
}"""
WRAP = """el=>{
 if(!el.childNodes.length||[...el.childNodes].some(n=>n.nodeType!==Node.TEXT_NODE))
 throw Error('Expected only direct vote text');
 const span=document.createElement('span');span.dataset.localTextMask='';
 span.append(...el.childNodes);el.append(span);
}"""


def without_indexes(value):
    """Retain identity fields while normalizing indexes shifted by the one wrapper."""
    if isinstance(value, dict):
        return {key: without_indexes(item) for key, item in value.items() if key != "index"}
    if isinstance(value, list):
        return [without_indexes(item) for item in value]
    return value


class LocalTextMaskTests(BrowserFixture, unittest.TestCase):
    """Compose one tracked project fixture for two local paint diagnostics."""

    options: Namespace

    def open_card(self, kind):
        class ProjectFixture(cards.ProjectCardTests):
            browser: Browser
            options: Namespace

        case = ProjectFixture()
        case.browser, case.options = self.browser, self.options
        self.addCleanup(case.doCleanups)
        self.case, self.page = case, case.open_page("en", 1280)
        self.errors = [str(error) for error in self.page.page_errors()]
        self.console = [message.text for message in self.page.console_messages() if message.type == "error"]
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console", lambda message: self.console.append(message.text) if message.type == "error" else None)
        self.card = self.page.locator("main article").first
        self.button = self.card.get_by_role("button").first
        self.focused = self.card.locator("h3 a") if kind == "title" else self.button
        self.original_target = self.card.locator("h3 a span") if kind == "title" else self.button
        self.target, self.kind, self.wrapper = self.original_target, kind, False
        self.page.mouse.move(0, 0)
        for _ in range(80):
            self.page.keyboard.press("Tab")
            if self.focused.evaluate("el=>el===document.activeElement"):
                break
        self.assertTrue(self.focused.evaluate("el=>el===document.activeElement&&el.matches(':focus-visible')"))
        self.card.evaluate("el=>el.scrollIntoView({block:'center',behavior:'instant'})")
        self.assertTrue(
            self.focused.evaluate("""el=>{const r=el.getBoundingClientRect();
            return r.top>=document.querySelector('header').getBoundingClientRect().bottom&&r.bottom<=innerHeight;}""")
        )

    def settle(self):
        self.page.evaluate("""async()=>{await document.fonts.ready;
            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
            await Promise.all(document.getAnimations().filter(a=>
            a.effect?.getComputedTiming().iterations!==Infinity).map(a=>a.finished));}""")

    def canonical(self, state):
        value = json.loads(json.dumps(state))
        index = self.target.evaluate("el=>[...document.body.querySelectorAll('*')].indexOf(el)")
        value["elements"] = [
            item for item in value["elements"] if not self.wrapper or item["identity"]["index"] != index
        ]
        if self.kind == "title":
            for item in value["elements"]:
                if item["identity"]["index"] == index:
                    del item["styles"]["webkitTextFillColor"]
        return without_indexes(value)

    def capture(self, name):
        self.settle()
        state = self.card.evaluate(STATE)
        record = {
            "state": state,
            "canonical": self.canonical(state),
            "rects": self.target.evaluate(RECTS),
            "target_fill": self.target.evaluate("el=>getComputedStyle(el).webkitTextFillColor"),
            "console_errors": list(self.console),
            "page_errors": list(self.errors),
        }
        image = self.page.screenshot(path=self.directory / f"{name}.png")
        record["sha256"] = hashlib.sha256(image).hexdigest()
        (self.directory / f"{name}.json").write_text(json.dumps(record, indent=2))
        self.shots[name] = {**record, "image": image}

    def local_cycle(self):
        self.capture("original")
        style = None
        try:
            if self.kind == "upvote":
                self.button.evaluate(WRAP)
                self.wrapper = True
                self.target = self.button.locator("[data-local-text-mask]")
            else:
                self.target.evaluate("el=>el.setAttribute('data-local-text-mask','')")
            self.capture("original-repeat")
            for key in ("canonical", "rects", "image"):
                self.assertEqual(self.shots["original"][key], self.shots["original-repeat"][key])
            style = self.page.add_style_tag(
                content="[data-local-text-mask]{-webkit-text-fill-color:transparent!important}"
            )
            self.capture("masked")
            self.capture("masked-repeat")
        finally:
            if style is not None:
                style.evaluate("el=>el.remove()")
            if self.wrapper:
                self.target.evaluate("el=>el.replaceWith(...el.childNodes)")
            else:
                self.target.evaluate("el=>el.removeAttribute('data-local-text-mask')")
            self.wrapper, self.target = False, self.original_target
            self.capture("restored")
            self.capture("restored-repeat")

    def compare(self, first, second, images=None, rects=None):
        images = images if images is not None else [self.shots[name]["image"] for name in (first, second)]
        encoded = [base64.b64encode(image).decode() for image in images]
        result = self.page.evaluate(DIFF, {"first": encoded[0], "second": encoded[1]})
        rects = rects if rects is not None else self.shots["original"]["rects"]
        result["outside_range"] = [
            point
            for point in result["changed"]
            if not any(
                int(rect[0] // 1) <= point["x"] < -int(-rect[2] // 1)
                and int(rect[1] // 1) <= point["y"] < -int(-rect[3] // 1)
                for rect in rects
            )
        ]
        name = f"diff-{first}-{second}.json"
        (self.directory / name).write_text(json.dumps(result, indent=2))
        return {
            "artifact": name,
            "first": first,
            "second": second,
            "changed": result["changedCount"],
            "outside": len(result["outside_range"]),
        }

    def calibration(self):
        encoded = self.page.evaluate("""()=>{const c=document.createElement('canvas');
            c.width=innerWidth;c.height=innerHeight;const x=c.getContext('2d',{colorSpace:'srgb'});
            x.fillStyle='#000';x.fillRect(0,0,c.width,c.height);const before=c.toDataURL().split(',')[1];
            const pixel=x.getImageData(0,0,1,1);pixel.data[1]=1;x.putImageData(pixel,0,0);
            return [before,c.toDataURL().split(',')[1]];}""")
        images = [base64.b64decode(image) for image in encoded]
        names = ("calibration-original", "calibration-changed")
        for name, image in zip(names, images, strict=True):
            (self.directory / f"{name}.png").write_bytes(image)
        result = self.compare(*names, images=images, rects=[[100, 100, 200, 200]])
        self.assertEqual((result["changed"], result["outside"]), (1, 1))
        detail = json.loads((self.directory / result["artifact"]).read_text())
        expected = [{"x": 0, "y": 0, "before": [0, 0, 0, 255], "after": [0, 1, 0, 255], "delta": [0, 1, 0, 0]}]
        self.assertEqual(detail["changed"], expected)
        self.assertEqual(detail["outside_range"], expected)

    def report(self):
        pairs = [("original", "masked"), ("original", "restored"), ("masked", "restored")]
        pairs.extend((name, name + "-repeat") for name in ("original", "masked", "restored"))
        diffs = [self.compare(first, second) for first, second in pairs]
        original = self.shots["original"]
        record = {
            "contrast_acceptance": "unknown",
            "glyph_support": "not certified",
            "kind": self.kind,
            "source_identity": "Coordinator must verify compiled build and source manifest.",
            "canonical_equal": {name: shot["canonical"] == original["canonical"] for name, shot in self.shots.items()},
            "rects_equal": {name: shot["rects"] == original["rects"] for name, shot in self.shots.items()},
            "diffs": diffs,
            "console_errors": self.console,
            "page_errors": self.errors,
            "unexpected": self.case.unexpected,
        }
        (self.directory / "result.json").write_text(json.dumps(record, indent=2))
        self.assertTrue(all(record["canonical_equal"].values()))
        self.assertTrue(all(record["rects_equal"].values()))
        self.assertEqual(original["state"]["fontsLoaded"], "loaded")
        self.assertEqual(original["state"]["runningFiniteAnimations"], [])
        styles = [item["styles"] for item in original["state"]["elements"]]
        self.assertTrue(all(style["color"] == style["webkitTextFillColor"] for style in styles))
        self.assertTrue(original["rects"])
        self.assertEqual(self.errors + self.console + self.case.unexpected, [])
        for name, shot in self.shots.items():
            self.assertEqual(
                shot["target_fill"], "rgba(0, 0, 0, 0)" if name.startswith("masked") else original["target_fill"]
            )
        for diff in diffs:
            self.assertEqual(diff["outside"], 0)
            if (diff["first"], diff["second"]) == pairs[0]:
                self.assertGreater(diff["changed"], 0, "Local text treatment produced no visible difference")
            if (diff["first"], diff["second"]) in {pairs[1], *pairs[3:]}:
                self.assertEqual(diff["changed"], 0)

    def test_title_and_upvote_local_cycles(self):
        for kind in ("title", "upvote"):
            with self.subTest(kind=kind):
                self.directory = self.options.screenshots / kind
                self.directory.mkdir(parents=True, exist_ok=True)
                self.shots = {}
                try:
                    self.open_card(kind)
                    if kind == "title":
                        self.calibration()
                    self.local_cycle()
                    self.report()
                finally:
                    self.doCleanups()


if __name__ == "__main__":
    LocalTextMaskTests.options, remaining = parse_options()
    unittest.main(argv=[__file__, *remaining])
