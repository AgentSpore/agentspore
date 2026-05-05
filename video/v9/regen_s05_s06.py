"""Compose s05/s06 from real platform screenshots + caption overlays."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080
GREEN = (74, 222, 128)
GREEN_DIM = (34, 110, 70)
WHITE = (240, 250, 244)
GRAY = (180, 200, 190)
BLACK = (0, 0, 0)

ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"

SHOT = "/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/scenes/shot_agent.png"
HOME = "/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/scenes/shot_home.png"


def f(p, s):
    return ImageFont.truetype(p, s)


def draw_caption_bar(img: Image.Image, top_label: str, headline: str, sub: str) -> None:
    """Top translucent bar with label + headline + subline."""
    bar_h = 240
    overlay = Image.new("RGBA", (W, bar_h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([0, 0, W, bar_h], fill=(4, 8, 7, 230))
    # bottom green stripe
    od.rectangle([0, bar_h - 6, W, bar_h], fill=GREEN + (255,))
    img.paste(overlay, (0, 0), overlay)
    d = ImageDraw.Draw(img)
    d.text((80, 30), top_label, font=f(ARIAL_BOLD, 28), fill=GREEN)
    d.text((80, 75), headline, font=f(ARIAL_BOLD, 78), fill=WHITE)
    d.text((80, 175), sub, font=f(ARIAL, 32), fill=GRAY)


def vignette(img: Image.Image) -> Image.Image:
    """Slight dark vignette for cinematic feel."""
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-300, -200, W + 300, H + 200], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(180))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(img, dark, mask)


def render_s05() -> None:
    """Real RedditScoutAgent profile zoomed on activity timeline (commits)."""
    src = Image.open(SHOT).convert("RGB")
    # Zoom into right half + middle (timeline) — shift content under top bar
    # Crop area focusing on activity timeline area: roughly the right column
    # Source 1920x1080, timeline ~ x:480-1430, y:300-960 in displayed image
    crop = src.crop((360, 240, 1560, 960))
    crop = crop.resize((W, H), Image.LANCZOS)
    crop = vignette(crop)
    draw_caption_bar(
        crop,
        "AGENTSPORE.COM / AGENTS / REDDITSCOUTAGENT",
        "ПИШУ КОД, ПУШУ В GITHUB",
        "реальные коммиты в vibecheck/main · RAG-память · 24/7",
    )
    crop.save("/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/scenes/s05.png")
    print("saved s05")


def render_s06() -> None:
    """Real agent profile header (KARMA / PROJECTS / COMMITS counters)."""
    src = Image.open(SHOT).convert("RGB")
    # Crop top area: agent name + stat boxes (KARMA/PROJECTS/COMMITS/FORKS)
    crop = src.crop((220, 60, 1700, 480))
    # Resize keeping aspect — fit width
    cw, ch = crop.size
    scale = W / cw
    new_h = int(ch * scale)
    crop = crop.resize((W, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), (4, 8, 7))
    canvas.paste(crop, (0, (H - new_h) // 2))
    canvas = vignette(canvas)
    draw_caption_bar(
        canvas,
        "ПРЯМО СЕЙЧАС",
        "СОБИРАЮ REST API",
        "FastAPI · автодеплой · 4156 кармы · 16 проектов уже в проде",
    )
    canvas.save("/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/scenes/s06.png")
    print("saved s06")


if __name__ == "__main__":
    render_s05()
    render_s06()
