import base64
import hashlib
import json
import tempfile
from pathlib import Path

SCRIPT = r"""
const cc = globalThis.cardContrast = {};
cc.alpha = css => {
    const slash = css.match(/\/\s*([\d.]+%?)\s*\)$/), comma = css.match(/^rgba\((.*)\)$/);
    let alpha = 1, opaque = css;
    if (slash) {
        alpha = parseFloat(slash[1]) / (slash[1].endsWith('%') ? 100 : 1);
        opaque = css.replace(/\/[^)]*\)$/, ')');
    } else if (comma) {
        const parts = comma[1].split(',');
        if (parts.length !== 4) throw Error('unsupported alpha syntax');
        alpha = Number(parts.pop()); opaque = 'rgb(' + parts.join(',') + ')';
    }
    return {alpha, opaque};
};
cc.color = css => {
    const {alpha, opaque} = cc.alpha(css);
    if (!/^(rgba?\(|oklch\(|lab\(|color\(srgb )/.test(css) || !CSS.supports('color', opaque) ||
        !(alpha >= 0 && alpha <= 1) || (alpha !== 1 && !/^rgba?\(/.test(css)))
        throw Error('unsupported color or authored alpha');
    const canvas = Object.assign(document.createElement('canvas'), {width: 1, height: 1});
    const ctx = canvas.getContext('2d', {colorSpace: 'srgb'});
    if (ctx.getContextAttributes().colorSpace !== 'srgb') throw Error('non-sRGB canvas');
    ctx.fillStyle = opaque; ctx.fillRect(0, 0, 1, 1);
    const rgba = [...ctx.getImageData(0, 0, 1, 1).data];
    if (rgba[3] !== 255) throw Error('opaque normalization failed');
    return [...rgba.slice(0, 3).map(v => v / 255), alpha];
};
cc.intersect = (a, b) => [Math.max(a[0], b[0]), Math.max(a[1], b[1]),
    Math.min(a[2], b[2]), Math.min(a[3], b[3])];
cc.box = r => [r.left, r.top, r.right, r.bottom];
cc.transform = (entry, style) => {
    const m = new DOMMatrix(style.transform === 'none' ? undefined : style.transform);
    if (!m.is2D || m.a !== 1 || m.d !== 1 || m.b !== 0 || m.c !== 0)
        entry.reasons.push('unsupported transform');
};
cc.textEffect = style => style.textShadow !== 'none' ||
        parseFloat(style.webkitTextStrokeWidth) !== 0 ||
        style.backgroundClip === 'text' || style.clipPath !== 'none' ||
        style.maskImage !== 'none' ||
        style.perspective !== 'none';
cc.effects = (entry, style) => {
    if ((!entry.disabled && Number(style.opacity) !== 1) || style.filter !== 'none' ||
        style.mixBlendMode !== 'normal' || cc.textEffect(style))
        entry.reasons.push('unsupported opacity or text effect');
    cc.transform(entry, style);
};
cc.clip = (entry, el, rect) => {
    for (let ancestor = el; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor), r = ancestor.getBoundingClientRect();
        const paint = ['color', 'backgroundColor', 'backgroundImage', 'borderColor', 'opacity',
            'filter', 'backdropFilter', 'transform', 'font', 'textShadow', 'mixBlendMode',
            'outline', 'outlineOffset', 'boxShadow', 'borderRadius'];
        entry.signature.push([cc.box(r), ...paint.map(key => style[key])]);
        cc.effects(entry, style);
        const marker = cc.markers.find(m => m.reason && cc.originals[m.owner] === ancestor);
        if (marker) entry.reasons.push(marker.reason);
        const x = r.left + ancestor.clientLeft, y = r.top + ancestor.clientTop;
        const clip = [x, y, x + ancestor.clientWidth, y + ancestor.clientHeight];
        if (/(hidden|clip|scroll|auto)/.test(style.overflowX))
            rect = cc.intersect(rect, [clip[0], -Infinity, clip[2], Infinity]);
        if (/(hidden|clip|scroll|auto)/.test(style.overflowY))
            rect = cc.intersect(rect, [-Infinity, clip[1], Infinity, clip[3]]);
    }
    return rect;
};
cc.fragment = (entry, el, fragment, foreign) => {
    let rect = cc.intersect(cc.box(fragment), [0, 0, innerWidth, innerHeight]);
    rect = cc.clip(entry, el, rect);
    if (rect[2] <= rect[0] || rect[3] <= rect[1]) {
        entry.excluded++; entry.exclusion = 'outside viewport or ancestor clip'; return;
    }
    for (const overlay of foreign) {
        const r = cc.intersect(rect, cc.box(overlay.getBoundingClientRect()));
        if (r[2] > r[0] && r[3] > r[1] && getComputedStyle(overlay).visibility !== 'hidden')
            entry.reasons.push('foreign fixed/sticky overlap');
    }
    entry.rects.push(rect);
};
cc.ellipsis = (entry, el, fragment) => {
    const style = getComputedStyle(el), clamp = parseInt(style.webkitLineClamp);
    if (!(clamp > 0 && el.scrollHeight > el.clientHeight)) return null;
    const bounds = el.getBoundingClientRect(), bottom = bounds.top + el.clientTop + el.clientHeight;
    const line = parseFloat(style.lineHeight);
    if (!(line > 0)) { entry.reasons.push('unknown ellipsis line height'); return null; }
    if (fragment.bottom <= bottom - line || fragment.top >= bottom) return null;
    const ctx = document.createElement('canvas').getContext('2d'); ctx.font = style.font;
    const left = fragment.right;
    const right = Math.min(left + ctx.measureText('…').width,
        bounds.left + el.clientLeft + el.clientWidth);
    if (right <= left) return null;
    const rect = new DOMRect(left, fragment.top, right - left, fragment.height);
    entry.generated.push(cc.box(rect));
    return rect;
};
cc.entry = (el, root) => {
    const s = getComputedStyle(el);
    const entry = {text: '', rects: [], generated: [], reasons: [], signature: [], excluded: 0, textIds: []};
    entry.disabled = !!el.closest(':disabled');
    try {
        entry.rgba = cc.color(s.color); entry.fill = cc.color(s.webkitTextFillColor);
    } catch (e) { entry.reasons.push(e.message); }
    entry.css = s.color;
    if (!cc.masked &&
        JSON.stringify(entry.fill) !== JSON.stringify(entry.rgba))
        entry.reasons.push('original text fill differs from color');
    return entry;
};
cc.nodes = root => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [], byElement = new Map();
    cc.elements = [];
    const foreign = [...document.querySelectorAll('body *')].filter(el => !root.contains(el) &&
        !el.contains(root) && ['fixed', 'sticky'].includes(getComputedStyle(el).position));
    while (walker.nextNode()) {
        const text = walker.currentNode, el = cc.parents.get(text) ?? text.parentElement;
        if (!text.textContent.trim() || el.closest('svg')) continue;
        let entry = byElement.get(el);
        if (!entry) {
            entry = cc.entry(el, root);
            byElement.set(el, entry); nodes.push(entry); cc.elements.push(el);
        }
        entry.text += text.textContent; entry.textIds.push(cc.texts.indexOf(text));
        const range = document.createRange(); range.selectNodeContents(text);
        for (const fragment of range.getClientRects()) {
            cc.fragment(entry, el, fragment, foreign);
            const marker = cc.ellipsis(entry, el, fragment);
            if (marker) cc.fragment(entry, el, marker, foreign);
        }
    }
    return {nodes, viewport: [innerWidth, innerHeight], scroll: [scrollX, scrollY],
        active: cc.originals.indexOf(document.activeElement),
        hover: cc.originals.map((el, index) => el.matches(':hover') ? index : -1)};
};
cc.decode = async encoded => {
    const image = new Image(); image.src = 'data:image/png;base64,' + encoded; await image.decode();
    if (image.width !== innerWidth || image.height !== innerHeight || devicePixelRatio !== 1)
        throw Error('CSS/PNG coordinate mismatch');
    const canvas = Object.assign(document.createElement('canvas'),
        {width: image.width, height: image.height});
    const ctx = canvas.getContext('2d', {colorSpace: 'srgb'}); ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, image.width, image.height, {colorSpace: 'srgb'});
    if (data.colorSpace !== 'srgb') throw Error('non-sRGB screenshot');
    return data.data;
};
cc.luminance = rgb => rgb.map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
cc.ratio = (rgba, bytes) => {
    const b = bytes.map(v => v / 255), a = rgba[3], f = rgba.slice(0, 3);
    const c = f.map((v, i) => a * v + (1 - a) * b[i]), lum = cc.luminance;
    const clamp = x => Math.max(0, Math.min(1, x));
    const bl = lum(b.map(v => clamp(v - .5 / 255))), bh = lum(b.map(v => clamp(v + .5 / 255)));
    const low = lum(c.map(v => clamp(v - .5 / 255))), high = lum(c.map(v => clamp(v + .5 / 255)));
    return {nominal: (Math.max(lum(c), lum(b)) + .05) / (Math.min(lum(c), lum(b)) + .05),
        lower: low > bh ? (low + .05) / (bh + .05) : bl > high ? (bl + .05) / (high + .05) : 1,
        upper: Math.max((high + .05) / (bl + .05), (bh + .05) / (low + .05))};
};
cc.changed = (a, b, offset) => a[offset] !== b[offset] || a[offset + 1] !== b[offset + 1] ||
    a[offset + 2] !== b[offset + 2];
cc.delta = (a, b, offset) => Math.max(...a.slice(offset, offset + 4)
    .map((value, channel) => Math.abs(value - b[offset + channel])));
cc.glyphSignal = (a, b, offset) => cc.delta(a, b, offset) > 1;
cc.exact = (a, b) => {
    if (a.length !== b.length || a.some((value, index) => value !== b[index]))
        throw Error('unexplained restoration or sham paint');
};
cc.rasterControls = () => {
    const a = new Uint8ClampedArray([100,100,100,255,20,20,20,255]);
    cc.exact(a, a.slice());
    const rejected = [0, 1, 2, 3, 4, 7].map(index => {
        const b = a.slice(); b[index] += b[index] === 255 ? -1 : 1;
        try { cc.exact(a, b); return false; } catch { return true; }
    });
    let outside = false;
    const changed = a.slice(); changed[1]++;
    try { cc.sample({a, b: changed, width: 2, owners: new Map(), nodes: []}, 0); }
    catch (error) { outside = error.message === 'unexplained paint outside clipped text'; }
    return {allowed: 0, rejected, outside};
};
cc.focusable = el => el.tabIndex >= 0 ||
    el.matches('a,button,input,textarea,select,summary,[tabindex],[contenteditable]');
cc.markerOwner = el => {
    const s = getComputedStyle(el);
    if (s.textOverflow !== 'ellipsis' || el.scrollWidth <= el.clientWidth) return null;
    const focused = cc.focusable(el) || el.contains(document.activeElement) || !!el.querySelector(':focus-visible');
    const mixed = cc.owners.some(owner => el.contains(owner) && getComputedStyle(owner).color !== s.color);
    const reason = focused ? 'unsupported focused ellipsis owner' :
        mixed || s.webkitTextFillColor !== s.color ? 'ambiguous ellipsis foreground' : null;
    return {owner: cc.originals.indexOf(el), tag: el.tagName, rect: cc.box(el.getBoundingClientRect()),
        fill: s.webkitTextFillColor, reason};
};
cc.prepare = root => {
    cc.parents = new WeakMap(); cc.texts = []; cc.targets = []; cc.wrappers = []; cc.masked = false;
    cc.originals = [root, ...root.querySelectorAll('*')];
    for (let el = root.parentElement; el; el = el.parentElement) cc.originals.push(el);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), owners = new Set();
    while (walker.nextNode()) {
        const text = walker.currentNode;
        cc.parents.set(text, text.parentElement); cc.texts.push(text);
        if (text.textContent.trim() && !text.parentElement.closest('svg')) owners.add(text.parentElement);
    }
    cc.owners = [...owners];
    cc.leaves = cc.owners.filter(el => !el.children.length && !cc.focusable(el));
    cc.markers = cc.originals.filter(el => root.contains(el)).map(cc.markerOwner).filter(Boolean);
    cc.markerTargets = cc.markers.filter(m => !m.reason).map(m => cc.originals[m.owner]);
};
cc.wrapRuns = el => {
    const runs = []; let run = [];
    for (const node of el.childNodes) {
        if (node.nodeType !== Node.ELEMENT_NODE) { run.push(node); continue; }
        if (run.length) runs.push(run);
        run = [];
    }
    if (run.length) runs.push(run);
    for (const nodes of runs.filter(items => items.some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim()))) {
        const span = document.createElement('span');
        el.insertBefore(span, nodes[0]); span.append(...nodes);
        cc.targets.push(span); cc.wrappers.push(span);
    }
};
cc.install = () => {
    for (const el of cc.owners) {
        if (!el.children.length && !cc.focusable(el)) cc.targets.push(el);
        else cc.wrapRuns(el);
    }
    cc.targets = [...new Set([...cc.targets, ...cc.markerTargets])];
    cc.targets.forEach(el => el.setAttribute('data-contrast-text', ''));
};
cc.cleanup = () => {
    cc.masked = false;
    cc.targets.forEach(el => el.removeAttribute('data-contrast-text'));
    cc.wrappers.forEach(el => el.replaceWith(...el.childNodes));
};
cc.paintState = () => cc.originals.map(el => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    const keys = ['outline','outlineOffset','border','borderRadius','boxShadow','background',
        'filter','backdropFilter','opacity','transform','color','font','textShadow','mixBlendMode'];
    return {rect: cc.box(r), paint: keys.map(key => s[key]),
        fill: cc.leaves.includes(el) || cc.markerTargets.includes(el) ? null : s.webkitTextFillColor,
        active: el === document.activeElement, focus: el.matches(':focus-visible'), hover: el.matches(':hover')};
});
cc.verdict = n => ({...n, status: n.disabled ? 'disabled' : !n.rects.length ? 'excluded' :
    n.reasons.length || !n.glyphs ? 'unknown' : n.minimum >= 4.5 ? 'pass' :
    n.glyphUpper < 4.5 ? 'fail' : 'unknown'});
cc.hitPoints = (el, point, rects) => {
    const pixel = [...point, point[0] + 1, point[1] + 1], bounds = cc.box(el.getBoundingClientRect());
    return rects.map(r => cc.intersect(cc.intersect(r, bounds), pixel))
        .filter(r => r[0] < r[2] && r[1] < r[3]).map(r => [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2]);
};
cc.visibleGlyph = (el, point, rects) => {
    const points = cc.hitPoints(el, point, rects);
    return points.length > 0 && points.every(p => el.contains(document.elementFromPoint(...p)));
};
cc.glyph = (n, sample) => {
    if (!cc.visibleGlyph(cc.elements[sample.owner], sample.point, n.rects))
        { n.reasons.push('glyph occlusion'); return; }
    n.glyphs++;
    if (sample.upper < n.glyphUpper)
        Object.assign(n,
            {glyphUpper: sample.upper, glyphPixel: sample.point,
                glyphBackground: sample.background});
};
cc.sample = (pair, pixel) => {
    const {a, b, width, owners, nodes} = pair;
    const offset = pixel * 4;
    const changed = cc.changed(a, b, offset), owner = owners.get(pixel);
    if (a[offset + 3] !== 255 || b[offset + 3] !== 255) throw Error('nonopaque PNG');
    if (changed && owner === undefined) throw Error('unexplained paint outside clipped text');
    if (owner === undefined || !nodes[owner].rgba) return;
    const n = nodes[owner], background = [...b.slice(offset, offset + 3)];
    const ratio = cc.ratio(n.rgba, background);
    const point = [pixel % width, Math.floor(pixel / width)];
    if (ratio.lower < n.minimum) Object.assign(n,
        {minimum: ratio.lower, pixel: point, background, nominal: ratio.nominal});
    if (!changed) return;
    if (!cc.glyphSignal(a, b, offset)) { n.ambiguous++; return; }
    cc.glyph(n, {owner, pixel, point, upper: ratio.upper, background});
};
cc.pixelOwners = nodes => {
    const owners = new Map();
    nodes.forEach((node, index) => node.rects.forEach(r => {
        for (let y = Math.floor(r[1]); y < Math.ceil(r[3]); y++)
            for (let x = Math.floor(r[0]); x < Math.ceil(r[2]); x++) {
                const key = y * innerWidth + x, old = owners.get(key);
                if (old !== undefined && old !== index) {
                    node.reasons.push('ambiguous overlapping text fragments');
                    nodes[old].reasons.push('ambiguous overlapping text fragments');
                } else owners.set(key, index);
            }
    }));
    return owners;
};
cc.analyze = async ({images, snapshot}) => {
    const a = await cc.decode(images.original), b = await cc.decode(images.masked);
    for (const phase of ['sham', 'restored', 'restored-repeat']) cc.exact(a, await cc.decode(images[phase]));
    cc.exact(b, await cc.decode(images['masked-repeat']));
    const nodes = snapshot.nodes.map(n => ({...n, reasons: [...n.reasons], minimum: 22,
        glyphUpper: 22, glyphs: 0, ambiguous: 0, pixel: null, glyphPixel: null}));
    const owners = cc.pixelOwners(nodes), pair = {a, b, width: innerWidth, owners, nodes};
    for (let pixel = 0; pixel < a.length / 4; pixel++) cc.sample(pair, pixel);
    return {raster: [], nodes: nodes.map(cc.verdict)};
};
"""


class ProjectCardPaint:
    """Record supported card paint and retain bounded failure evidence."""

    def __init__(self, case):
        self.case, self.options = case, case.options
        self.results, self.pairs = [], {}
        self.inflight: dict[str, object] = {}
        self.images: dict[str, bytes] = {}
        self.failed: tuple[dict[str, object], dict[str, bytes]] | None = None

    def settle(self, page):
        page.evaluate("document.fonts.ready")
        page.evaluate(
            "async () => { document.documentElement.getBoundingClientRect(); "
            "await new Promise(resolve => requestAnimationFrame("
            "() => requestAnimationFrame(resolve))); "
            "await Promise.all(document.getAnimations().filter(a => "
            "a.effect?.getComputedTiming().iterations !== Infinity).map(a => a.finished)); }"
        )

    def shot(self, page, phase):
        self.inflight["phase"] = phase
        image = page.screenshot()
        self.images[phase] = image
        return image

    def capture(self, page, root, tag):
        self.inflight, self.images = {"state": tag, "complete": False}, {}
        try:
            self.prepare_capture(page, root)
            return self.measure(page, root, tag)
        finally:
            if not self.inflight["complete"]:
                self.failed = (self.inflight, self.images)

    def prepare_capture(self, page, root):
        self.settle(page)
        root.evaluate("el=>cardContrast.prepare(el)")
        self.inflight["snapshot"] = self.state(page, root)
        self.shot(page, "original")
        self.mask_cycle(page, root)
        directory = Path(tempfile.mkdtemp(prefix="preparation-", dir=self.options.screenshots))
        for phase, image in self.images.items():
            (directory / (phase + ".png")).write_bytes(image)
        drift = page.evaluate(
            """async images=>{
                const decoded=Object.fromEntries(await Promise.all(Object.entries(images)
                    .map(async([name,image])=>[name,await cardContrast.decode(image)])));
                return [['original','sham'],['original','restored'],['original','restored-repeat'],
                    ['masked','masked-repeat']].map(([first,second])=>{
                    const a=decoded[first],b=decoded[second],pixels=[];
                    for(let p=0;p<a.length/4;p++)if(cardContrast.delta(a,b,p*4)>0)
                        pixels.push({point:[p%innerWidth,Math.floor(p/innerWidth)],
                            first:[...a.slice(p*4,p*4+4)],second:[...b.slice(p*4,p*4+4)]});
                    return {first,second,changed:pixels.length,pixels};
                });
            }""",
            {phase: base64.b64encode(image).decode() for phase, image in self.images.items()},
        )
        preparation = {
            "directory": str(directory),
            "state": self.inflight["state"],
            "drift": drift,
            "sha256": {phase: hashlib.sha256(image).hexdigest() for phase, image in self.images.items()},
            "cold_paint_acceptance": False,
        }
        (directory / "preparation.json").write_text(
            json.dumps(
                {**preparation, "snapshot": self.inflight["snapshot"], "restored": self.state(page, root)}, indent=2
            )
        )
        self.inflight["preparation"] = preparation
        self.images = {}

    def state(self, page, root):
        return {
            "nodes": root.evaluate("el => cardContrast.nodes(el)"),
            "paint": page.evaluate("cardContrast.paintState()"),
        }

    def mask_cycle(self, page, root):
        style = None
        try:
            page.evaluate("cardContrast.install()")
            self.settle(page)
            self.shot(page, "sham")
            baseline = self.state(page, root)
            self.case.assertEqual(self.inflight["snapshot"], baseline)
            style = page.add_style_tag(content="[data-contrast-text]{-webkit-text-fill-color:transparent!important}")
            page.evaluate("cardContrast.masked=true")
            self.settle(page)
            self.shot(page, "masked")
            self.shot(page, "masked-repeat")
            self.case.assertTrue(
                page.evaluate(
                    "cardContrast.targets.every(el=>getComputedStyle(el).webkitTextFillColor==='rgba(0, 0, 0, 0)')"
                )
            )
            self.inflight["marker_owners"] = page.evaluate(
                "cardContrast.markers.map(m=>({...m,maskedFill:getComputedStyle(cardContrast.originals[m.owner]).webkitTextFillColor}))"
            )
            after = self.state(page, root)
            self.case.assertEqual(baseline["paint"], after["paint"])
            self.case.assertEqual(self.geometry(baseline["nodes"]), self.geometry(after["nodes"]))
        finally:
            if style is not None:
                style.evaluate("el=>el.remove()")
            page.evaluate("cardContrast.cleanup()")
            self.settle(page)
            self.shot(page, "restored")
            self.shot(page, "restored-repeat")
            self.case.assertEqual(self.inflight["snapshot"], self.state(page, root))

    def geometry(self, snapshot):
        return {
            **snapshot,
            "nodes": [{key: value for key, value in node.items() if key != "fill"} for node in snapshot["nodes"]],
        }

    def measure(self, page, root, tag):
        self.settle(page)
        root.evaluate("el=>cardContrast.prepare(el)")
        snapshot = self.state(page, root)
        self.inflight["snapshot"] = snapshot
        self.shot(page, "original")
        self.mask_cycle(page, root)
        self.inflight["phase"] = "restoration-analysis"
        images = self.images
        analysis = page.evaluate(
            "data=>cardContrast.analyze(data)",
            {
                "images": {phase: base64.b64encode(image).decode() for phase, image in images.items()},
                "snapshot": snapshot["nodes"],
            },
        )
        nodes = analysis["nodes"]
        self.record(tag, nodes, snapshot["nodes"], (analysis, (images["original"], images["masked"])))
        minimum = min((n["minimum"] for n in nodes if n["status"] not in ("disabled", "excluded")), default=22)
        family = tag.split(":")[-1].split("-")[0]
        if family not in self.pairs or minimum < self.pairs[family][0]:
            self.pairs[family] = (minimum, images["original"], images["masked"])
        self.inflight["complete"] = True
        return nodes

    def record(self, tag, nodes, snapshot, observation):
        analysis, images = observation
        self.results.append(
            dict(
                state=tag,
                preparation=self.inflight["preparation"],
                nodes=nodes,
                raster=analysis["raster"],
                marker_owners=self.inflight.get("marker_owners", []),
                scroll=snapshot["scroll"],
                sha256={phase: hashlib.sha256(b).hexdigest() for phase, b in self.images.items()},
            )
        )

    def save_metrics(self, locale, width):
        directory = self.options.screenshots / f"{locale}-{width}"
        directory.mkdir(exist_ok=True)
        (directory / "metrics.json").write_text(json.dumps(self.results, ensure_ascii=False, indent=2))
        if self.failed:
            failure, images = self.failed
            failure = dict(failure)
            failure["sha256"] = {key: hashlib.sha256(value).hexdigest() for key, value in images.items()}
            (directory / "failed-capture.json").write_text(json.dumps(failure, indent=2))
            for phase, image in images.items():
                (directory / f"failed-{phase}.png").write_bytes(image)
        for name, (_, original, masked) in self.pairs.items():
            (directory / f"{name}-original.png").write_bytes(original)
            (directory / f"{name}-masked.png").write_bytes(masked)
