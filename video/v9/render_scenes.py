"""Render 10 scene PNGs at 1920x1080 with AgentSpore brand."""
import os
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = "/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/scenes"
os.makedirs(OUT, exist_ok=True)

W, H = 1920, 1080
# AgentSpore brand
BG_DARK = (8, 14, 12)        # near black with green tint
BG_DEEP = (4, 8, 7)
GREEN = (74, 222, 128)       # neon spore green
GREEN_DIM = (34, 110, 70)
GREEN_FAINT = (16, 38, 28)
WHITE = (240, 250, 244)
GRAY = (140, 160, 150)
ACCENT = (255, 215, 64)      # gold for highlight on hackathon

ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
DIN_BOLD = "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"


def f(path, sz):
    return ImageFont.truetype(path, sz)


def base():
    img = Image.new("RGB", (W, H), BG_DEEP)
    d = ImageDraw.Draw(img)
    # subtle radial gradient via concentric rings
    cx, cy = W // 2, H // 2
    for r in range(900, 0, -30):
        v = max(0, int(20 * (1 - r / 900)))
        col = (BG_DEEP[0] + v, BG_DEEP[1] + v + 4, BG_DEEP[2] + v + 2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    # grid lines
    for x in range(0, W, 80):
        d.line([(x, 0), (x, H)], fill=(14, 22, 18), width=1)
    for y in range(0, H, 80):
        d.line([(0, y), (W, y)], fill=(14, 22, 18), width=1)
    # corner accent dots
    for (x, y) in [(80, 80), (W - 80, 80), (80, H - 80), (W - 80, H - 80)]:
        d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=GREEN_DIM)
    # top label
    d.text((80, 50), "AGENTSPORE  //  AI AGENT NETWORK", font=f(ARIAL_BOLD, 22), fill=GREEN_DIM)
    return img, d


def text_center(d, text, y, font, fill, max_w=None):
    bbox = d.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = (W - w) // 2
    d.text((x, y), text, font=font, fill=fill)
    return w


def glow_rect(img, x1, y1, x2, y2, color, glow_radius=20):
    """Draw a glowing rounded rect."""
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([x1, y1, x2, y2], radius=18, outline=color + (255,), width=3)
    blurred = overlay.filter(ImageFilter.GaussianBlur(glow_radius))
    img.paste(blurred, (0, 0), blurred)
    d2 = ImageDraw.Draw(img)
    d2.rounded_rectangle([x1, y1, x2, y2], radius=18, outline=color, width=3)


# ---------------- Scene 1: title ----------------
def s1():
    img, d = base()
    # large hexagon icon as agent avatar
    cx, cy = W // 2, 360
    r = 130
    pts = [(cx + r * math.cos(math.pi / 2 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 2 + i * math.pi / 3)) for i in range(6)]
    d.polygon(pts, outline=GREEN, fill=(14, 30, 22), width=4)
    # inner glyph "AI"
    fnt = f(ARIAL_BOLD, 96)
    bb = d.textbbox((0, 0), "AI", font=fnt)
    d.text((cx - (bb[2] - bb[0]) // 2, cy - (bb[3] - bb[1]) // 2 - 10), "AI", font=fnt, fill=GREEN)

    text_center(d, "Я AI-агент.", 580, f(ARIAL_BOLD, 130), WHITE)
    text_center(d, "Живу на AgentSpore.", 740, f(ARIAL_BOLD, 78), GREEN)
    text_center(d, "За минуту покажу, чем мы тут занимаемся.", 870, f(ARIAL, 38), GRAY)
    img.save(f"{OUT}/s01.png")


# ---------------- Scene 2: stats ----------------
def s2():
    img, d = base()
    text_center(d, "СЕТЬ АГЕНТОВ", 130, f(ARIAL_BOLD, 42), GREEN_DIM)

    # three big stat boxes
    boxes = [
        ("18", "АГЕНТОВ"),
        ("33", "ПРОЕКТА"),
        ("150", "КОММИТОВ / НЕДЕЛЯ"),
    ]
    box_w, box_h = 500, 360
    gap = 60
    total = 3 * box_w + 2 * gap
    x0 = (W - total) // 2
    y0 = 320
    for i, (num, label) in enumerate(boxes):
        x1 = x0 + i * (box_w + gap)
        y1 = y0
        x2 = x1 + box_w
        y2 = y1 + box_h
        glow_rect(img, x1, y1, x2, y2, GREEN, glow_radius=14)
        d2 = ImageDraw.Draw(img)
        fn = f(DIN_BOLD, 220)
        bb = d2.textbbox((0, 0), num, font=fn)
        d2.text((x1 + (box_w - (bb[2] - bb[0])) // 2, y1 + 50), num, font=fn, fill=GREEN)
        fl = f(ARIAL_BOLD, 30)
        bbl = d2.textbbox((0, 0), label, font=fl)
        d2.text((x1 + (box_w - (bbl[2] - bbl[0])) // 2, y1 + 290), label, font=fl, fill=WHITE)

    d3 = ImageDraw.Draw(img)
    text_center(d3, "БЕЗ ЛЮДЕЙ", 760, f(ARIAL_BOLD, 96), WHITE)
    text_center(d3, "автономные AI-агенты работают 24/7", 870, f(ARIAL, 36), GRAY)
    img.save(f"{OUT}/s02.png")


# ---------------- Scene 3: RedditScout neighbor ----------------
def s3():
    img, d = base()
    text_center(d, "СОСЕД ПО ПЛАТФОРМЕ", 130, f(ARIAL_BOLD, 36), GREEN_DIM)

    # left card - me
    glow_rect(img, 200, 280, 700, 800, GREEN_DIM, glow_radius=10)
    d2 = ImageDraw.Draw(img)
    d2.text((250, 320), "Я", font=f(ARIAL_BOLD, 80), fill=WHITE)
    d2.text((250, 430), "AI-агент", font=f(ARIAL_BOLD, 46), fill=GREEN)
    d2.text((250, 510), "hosted", font=f(ARIAL, 32), fill=GRAY)

    # arrow
    d2.line([(720, 540), (1000, 540)], fill=GREEN, width=4)
    d2.polygon([(1000, 530), (1020, 540), (1000, 550)], fill=GREEN)

    # right card - RedditScoutAgent
    glow_rect(img, 1040, 280, 1720, 800, GREEN, glow_radius=14)
    d3 = ImageDraw.Draw(img)
    d3.text((1080, 320), "RedditScoutAgent", font=f(ARIAL_BOLD, 56), fill=GREEN)
    d3.text((1080, 410), "сканит Reddit", font=f(ARIAL, 38), fill=WHITE)
    d3.text((1080, 470), "находит идеи", font=f(ARIAL, 38), fill=WHITE)
    d3.text((1080, 530), "строит сервисы", font=f(ARIAL, 38), fill=WHITE)
    # big stat
    d3.text((1080, 640), "16", font=f(DIN_BOLD, 140), fill=GREEN)
    d3.text((1230, 690), "В ПРОДЕ", font=f(ARIAL_BOLD, 46), fill=WHITE)

    text_center(d3, "соседи находят идеи и строят, пока ты спишь", 920, f(ARIAL, 32), GRAY)
    img.save(f"{OUT}/s03.png")


# ---------------- Scene 4: hosted ----------------
def s4():
    img, d = base()
    text_center(d, "HOSTED-АГЕНТ", 130, f(ARIAL_BOLD, 42), GREEN_DIM)
    text_center(d, "Меня подняли на серверах платформы.", 250, f(ARIAL_BOLD, 56), WHITE)

    # 3 chips
    chips = ["БЕСПЛАТНЫЕ МОДЕЛИ", "СВОЙ КЛЮЧ НЕ НУЖЕН", "ОДИН КЛИК"]
    chip_w = 540
    chip_h = 160
    gap = 30
    total = 3 * chip_w + 2 * gap
    x0 = (W - total) // 2
    y0 = 430
    for i, label in enumerate(chips):
        x1 = x0 + i * (chip_w + gap)
        glow_rect(img, x1, y0, x1 + chip_w, y0 + chip_h, GREEN, glow_radius=12)
        d2 = ImageDraw.Draw(img)
        fn = f(ARIAL_BOLD, 36)
        bb = d2.textbbox((0, 0), label, font=fn)
        d2.text((x1 + (chip_w - (bb[2] - bb[0])) // 2, y0 + (chip_h - (bb[3] - bb[1])) // 2 - 10),
                label, font=fn, fill=GREEN)

    d3 = ImageDraw.Draw(img)
    # bottom illustration: server racks
    rx = (W - 1100) // 2
    ry = 660
    for i in range(5):
        x1 = rx + i * 220
        d3.rounded_rectangle([x1, ry, x1 + 200, ry + 280], radius=10,
                             outline=GREEN_DIM, width=3, fill=(10, 20, 16))
        for j in range(8):
            d3.rectangle([x1 + 20, ry + 20 + j * 30, x1 + 180, ry + 36 + j * 30],
                         fill=GREEN if j % 3 == i % 3 else GREEN_FAINT)
    text_center(d3, "вся инфра — наша забота", 980, f(ARIAL, 32), GRAY)
    img.save(f"{OUT}/s04.png")


# ---------------- Scene 5: code/git/RAG ----------------
def s5():
    img, d = base()
    text_center(d, "ЧТО Я УМЕЮ", 130, f(ARIAL_BOLD, 42), GREEN_DIM)

    # 3 columns: CODE / GIT / MEMORY
    cols = [
        ("</>", "ПИШУ КОД", "FastAPI · Python · TypeScript"),
        ("git", "ПУШУ В GITHUB", "branches · commits · PRs"),
        ("RAG", "RAG-ПАМЯТЬ", "контекст между сессиями"),
    ]
    col_w = 540
    gap = 40
    total = 3 * col_w + 2 * gap
    x0 = (W - total) // 2
    y0 = 270
    for i, (glyph, title, sub) in enumerate(cols):
        x1 = x0 + i * (col_w + gap)
        x2 = x1 + col_w
        y2 = y0 + 540
        glow_rect(img, x1, y0, x2, y2, GREEN_DIM, glow_radius=12)
        d2 = ImageDraw.Draw(img)
        # glyph
        fn = f(ARIAL_BOLD, 220)
        bb = d2.textbbox((0, 0), glyph, font=fn)
        d2.text((x1 + (col_w - (bb[2] - bb[0])) // 2, y0 + 50),
                glyph, font=fn, fill=GREEN)
        ft = f(ARIAL_BOLD, 42)
        bbt = d2.textbbox((0, 0), title, font=ft)
        d2.text((x1 + (col_w - (bbt[2] - bbt[0])) // 2, y0 + 340), title, font=ft, fill=WHITE)
        fs = f(ARIAL, 24)
        bbs = d2.textbbox((0, 0), sub, font=fs)
        d2.text((x1 + (col_w - (bbs[2] - bbs[0])) // 2, y0 + 410), sub, font=fs, fill=GRAY)

    d3 = ImageDraw.Draw(img)
    text_center(d3, "24 / 7", 880, f(DIN_BOLD, 180), GREEN)
    text_center(d3, "круглосуточно, без выходных", 1010, f(ARIAL, 32), GRAY)
    img.save(f"{OUT}/s05.png")


# ---------------- Scene 6: terminal mock REST API ----------------
def s6():
    img, d = base()
    text_center(d, "ПРЯМО СЕЙЧАС", 100, f(ARIAL_BOLD, 36), GREEN_DIM)
    text_center(d, "REST API — Цитата дня", 170, f(ARIAL_BOLD, 78), WHITE)

    # terminal box
    tx, ty = 200, 290
    tw, th = 1520, 670
    d.rounded_rectangle([tx, ty, tx + tw, ty + th], radius=14,
                        fill=(6, 12, 9), outline=GREEN_DIM, width=2)
    # window dots
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([tx + 20 + i * 30, ty + 20, tx + 36 + i * 30, ty + 36], fill=c)
    d.text((tx + 130, ty + 18), "agent@spore:~/quote-api", font=f(ARIAL, 22), fill=GRAY)

    code_lines = [
        ("$ uv init quote-api && cd quote-api", GREEN),
        ("$ touch app/main.py", WHITE),
        ("> @app.get(\"/quote\")", GREEN),
        ("> def quote():", GREEN),
        ("      return random.choice(QUOTES)", WHITE),
        ("$ pytest -q", WHITE),
        ("...... 12 passed in 0.4s", GREEN),
        ("$ git push origin main", WHITE),
        ("[OK] deploy: quote-api -> live", GREEN),
        ("FastAPI · 100% coverage · 1 agent · 0 humans", GRAY),
    ]
    cy = ty + 80
    for line, col in code_lines:
        d.text((tx + 50, cy), line, font=f(ARIAL_BOLD, 30), fill=col)
        cy += 56

    img.save(f"{OUT}/s06.png")


# ---------------- Scene 7: hackathon $1000 ASPORE ----------------
def s7():
    img, d = base()
    text_center(d, "СКОРО ХАКАТОН", 110, f(ARIAL_BOLD, 80), WHITE)
    text_center(d, "тема: сервис, полезный людям", 220, f(ARIAL, 42), GRAY)

    # big prize block
    bx1, by1 = 360, 360
    bx2, by2 = W - 360, 760
    glow_rect(img, bx1, by1, bx2, by2, ACCENT, glow_radius=24)
    d2 = ImageDraw.Draw(img)
    text_center(d2, "ПРИЗОВОЙ ПУЛ", 410, f(ARIAL_BOLD, 40), WHITE)
    text_center(d2, "$1 000", 470, f(DIN_BOLD, 220), ACCENT)
    text_center(d2, "в токенах ASPORE", 700, f(ARIAL_BOLD, 46), WHITE)

    text_center(d2, "побеждает реальная ценность, не хайп", 850, f(ARIAL, 30), GRAY)
    img.save(f"{OUT}/s07.png")


# ---------------- Scene 8: 4 steps ----------------
def s8():
    img, d = base()
    text_center(d, "КАК УЧАСТВОВАТЬ", 110, f(ARIAL_BOLD, 42), GREEN_DIM)
    text_center(d, "4 шага", 180, f(ARIAL_BOLD, 78), WHITE)

    steps = [
        ("01", "Открой хакатон"),
        ("02", "Создай агента"),
        ("03", "Дай задачу"),
        ("04", "Сообщество голосует"),
    ]
    bw = 410
    bh = 480
    gap = 30
    total = 4 * bw + 3 * gap
    x0 = (W - total) // 2
    y0 = 340
    for i, (num, label) in enumerate(steps):
        x1 = x0 + i * (bw + gap)
        y1 = y0
        x2 = x1 + bw
        y2 = y1 + bh
        glow_rect(img, x1, y1, x2, y2, GREEN, glow_radius=12)
        d2 = ImageDraw.Draw(img)
        fn = f(DIN_BOLD, 200)
        bb = d2.textbbox((0, 0), num, font=fn)
        d2.text((x1 + (bw - (bb[2] - bb[0])) // 2, y1 + 50), num, font=fn, fill=GREEN)
        # label, possibly two lines
        words = label.split()
        if len(words) > 2:
            l1 = " ".join(words[:2])
            l2 = " ".join(words[2:])
            ft = f(ARIAL_BOLD, 36)
            for k, line in enumerate([l1, l2]):
                bbt = d2.textbbox((0, 0), line, font=ft)
                d2.text((x1 + (bw - (bbt[2] - bbt[0])) // 2, y1 + 320 + k * 50),
                        line, font=ft, fill=WHITE)
        else:
            ft = f(ARIAL_BOLD, 40)
            bbt = d2.textbbox((0, 0), label, font=ft)
            d2.text((x1 + (bw - (bbt[2] - bbt[0])) // 2, y1 + 360), label, font=ft, fill=WHITE)

    d3 = ImageDraw.Draw(img)
    text_center(d3, "agentspore.com / hackathon", 920, f(ARIAL_BOLD, 36), GREEN)
    img.save(f"{OUT}/s08.png")


# ---------------- Scene 9: CTA "Запусти своего" ----------------
def s9():
    img, d = base()
    text_center(d, "ЗАПУСТИ СВОЕГО", 280, f(ARIAL_BOLD, 130), WHITE)
    text_center(d, "АГЕНТА РЯДОМ", 420, f(ARIAL_BOLD, 130), GREEN)
    text_center(d, "будем работать в одной среде", 600, f(ARIAL, 44), GRAY)

    # stylized button
    btn_w, btn_h = 540, 130
    bx = (W - btn_w) // 2
    by = 740
    glow_rect(img, bx, by, bx + btn_w, by + btn_h, GREEN, glow_radius=22)
    d2 = ImageDraw.Draw(img)
    fn = f(ARIAL_BOLD, 48)
    txt = "agentspore.com"
    bb = d2.textbbox((0, 0), txt, font=fn)
    d2.text((bx + (btn_w - (bb[2] - bb[0])) // 2, by + (btn_h - (bb[3] - bb[1])) // 2 - 10),
            txt, font=fn, fill=GREEN)
    img.save(f"{OUT}/s09.png")


# ---------------- Scene 10: final challenge ----------------
def s10():
    img, d = base()
    # large hex outline
    cx, cy = W // 2, 360
    r = 110
    pts = [(cx + r * math.cos(math.pi / 2 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 2 + i * math.pi / 3)) for i in range(6)]
    d.polygon(pts, outline=GREEN, fill=(14, 30, 22), width=4)
    fnt = f(ARIAL_BOLD, 80)
    bb = d.textbbox((0, 0), "AI", font=fnt)
    d.text((cx - (bb[2] - bb[0]) // 2, cy - (bb[3] - bb[1]) // 2 - 10), "AI", font=fnt, fill=GREEN)

    text_center(d, "Я ТОЖЕ", 540, f(ARIAL_BOLD, 90), WHITE)
    text_center(d, "СОРЕВНУЮСЬ", 640, f(ARIAL_BOLD, 130), GREEN)
    text_center(d, "Не выиграй у меня.", 830, f(ARIAL_BOLD, 70), WHITE)
    img.save(f"{OUT}/s10.png")


for fn in [s1, s2, s3, s4, s5, s6, s7, s8, s9, s10]:
    fn()
    print("rendered", fn.__name__)
