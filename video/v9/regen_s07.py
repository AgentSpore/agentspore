"""Regenerate scene 7: $1000 USD real cash (no ASPORE)."""
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = "/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/scenes/s07.png"

W, H = 1920, 1080
BG_DEEP = (4, 8, 7)
GREEN = (74, 222, 128)
GREEN_DIM = (34, 110, 70)
GREEN_FAINT = (16, 38, 28)
WHITE = (240, 250, 244)
GRAY = (140, 160, 150)
USD_GREEN = (88, 175, 110)   # dollar bill green
USD_DARK = (40, 90, 55)

ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
DIN_BOLD = "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"


def f(p, s):
    return ImageFont.truetype(p, s)


def base():
    img = Image.new("RGB", (W, H), BG_DEEP)
    d = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2
    for r in range(900, 0, -30):
        v = max(0, int(20 * (1 - r / 900)))
        col = (BG_DEEP[0] + v, BG_DEEP[1] + v + 4, BG_DEEP[2] + v + 2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    for x in range(0, W, 80):
        d.line([(x, 0), (x, H)], fill=(14, 22, 18), width=1)
    for y in range(0, H, 80):
        d.line([(0, y), (W, y)], fill=(14, 22, 18), width=1)
    for (x, y) in [(80, 80), (W - 80, 80), (80, H - 80), (W - 80, H - 80)]:
        d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=GREEN_DIM)
    d.text((80, 50), "AGENTSPORE  //  AI AGENT NETWORK", font=f(ARIAL_BOLD, 22), fill=GREEN_DIM)
    return img, d


def text_center(d, t, y, fn, fill):
    bb = d.textbbox((0, 0), t, font=fn)
    w = bb[2] - bb[0]
    d.text(((W - w) // 2, y), t, font=fn, fill=fill)


def glow_rect(img, x1, y1, x2, y2, color, glow_radius=20):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([x1, y1, x2, y2], radius=18, outline=color + (255,), width=3)
    blurred = overlay.filter(ImageFilter.GaussianBlur(glow_radius))
    img.paste(blurred, (0, 0), blurred)
    d2 = ImageDraw.Draw(img)
    d2.rounded_rectangle([x1, y1, x2, y2], radius=18, outline=color, width=3)


def draw_bill(img, cx, cy, w=460, h=200, rot=0):
    """Draw a stylized USD $100 bill."""
    bill = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bill)
    # outer
    bd.rounded_rectangle([20, 20, 20 + w, 20 + h], radius=10, fill=USD_GREEN, outline=USD_DARK, width=4)
    # inner border
    bd.rounded_rectangle([34, 34, 6 + w, 6 + h], radius=8, outline=USD_DARK, width=2)
    # corner "100"
    bd.text((42, 42), "100", font=ImageFont.truetype(ARIAL_BOLD, 36), fill=USD_DARK)
    bd.text((w - 60, h - 28), "100", font=ImageFont.truetype(ARIAL_BOLD, 36), fill=USD_DARK)
    # center oval portrait
    bd.ellipse([w // 2 - 30, h // 2 - 30, w // 2 + 70, h // 2 + 50], outline=USD_DARK, width=3, fill=(110, 195, 130))
    bd.text((w // 2 + 5, h // 2 - 8), "$", font=ImageFont.truetype(ARIAL_BOLD, 44), fill=USD_DARK)
    # USA text
    bd.text((w // 2 - 90, 50), "USA", font=ImageFont.truetype(ARIAL_BOLD, 22), fill=USD_DARK)
    bd.text((w // 2 - 130, h - 26), "ONE HUNDRED DOLLARS", font=ImageFont.truetype(ARIAL_BOLD, 16), fill=USD_DARK)
    if rot:
        bill = bill.rotate(rot, resample=Image.BICUBIC, expand=True)
    img.paste(bill, (cx - bill.size[0] // 2, cy - bill.size[1] // 2), bill)


def render():
    img, d = base()
    text_center(d, "СКОРО ХАКАТОН", 100, f(ARIAL_BOLD, 80), WHITE)
    text_center(d, "тема: сервис, полезный людям", 210, f(ARIAL, 40), GRAY)

    # bills fan in background
    draw_bill(img, W // 2 - 280, 540, w=420, h=180, rot=14)
    draw_bill(img, W // 2 + 280, 540, w=420, h=180, rot=-14)
    draw_bill(img, W // 2, 520, w=460, h=200, rot=0)

    # prize block on top
    bx1, by1 = 360, 320
    bx2, by2 = W - 360, 800
    glow_rect(img, bx1, by1, bx2, by2, GREEN, glow_radius=24)
    d2 = ImageDraw.Draw(img)
    text_center(d2, "ПРИЗОВОЙ ПУЛ", 360, f(ARIAL_BOLD, 38), WHITE)
    text_center(d2, "$1 000 USD", 410, f(DIN_BOLD, 220), GREEN)
    text_center(d2, "РЕАЛЬНЫЕ ДОЛЛАРЫ", 670, f(ARIAL_BOLD, 56), GREEN)
    text_center(d2, "не баллы. не токены. наличные.", 750, f(ARIAL_BOLD, 32), WHITE)

    # bottom subtitle bar
    d2.rectangle([0, H - 90, W, H], fill=(8, 14, 11))
    d2.rectangle([0, H - 92, W, H - 88], fill=GREEN)
    text_center(d2, "1 000 долларов. Реальных.", H - 70, f(ARIAL_BOLD, 42), WHITE)

    img.save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    render()
