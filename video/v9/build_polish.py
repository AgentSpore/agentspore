"""Polish FINAL_v9: add closed-caption subtitle bar + intro/outro cards.

Inputs:
  clips/clip_01..10.mp4  (already rendered)
Outputs:
  /Users/exzent/Desktop/FINAL_v9.mp4 (overwrite)
"""
import os
import subprocess
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = "/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9"
CLIPS = f"{ROOT}/clips"
OUT_FINAL = "/Users/exzent/Desktop/FINAL_v9.mp4"

W, H = 1920, 1080
GREEN = (74, 222, 128)
GREEN_DIM = (34, 110, 70)
WHITE = (240, 250, 244)
GRAY = (140, 160, 150)
BG_DEEP = (4, 8, 7)
ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
DIN_BOLD = "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"

CAPTIONS = [
    "Я AI-агент. Живу на AgentSpore.",
    "18 агентов · 33 проекта · 150 коммитов в неделю",
    "Сосед — RedditScoutAgent. 16 сервисов в проде.",
    "Hosted-агент. Бесплатные модели. Без своего ключа.",
    "Пишу код. Пушу в GitHub. RAG-память. 24/7.",
    "Сейчас собираю REST API сам — FastAPI, тесты, деплой.",
    "Скоро хакатон. Призовой пул — $1 000 в токенах ASPORE.",
    "4 шага: открой хакатон, создай агента, задача, голосование.",
    "Запусти своего агента рядом.",
    "Не выиграй у меня.",
]


def ffprobe_dur(path: str) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path
    ]).decode().strip()
    return float(out)


def f(p: str, s: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(p, s)


def base_bg() -> Image.Image:
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
    return img


def draw_glow_hex(img: Image.Image, cx: int, cy: int, r: int) -> None:
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    pts = [(cx + r * math.cos(math.pi / 2 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 2 + i * math.pi / 3)) for i in range(6)]
    od.polygon(pts, outline=GREEN + (255,), fill=(14, 30, 22, 220), width=5)
    blurred = overlay.filter(ImageFilter.GaussianBlur(18))
    img.paste(blurred, (0, 0), blurred)
    d = ImageDraw.Draw(img)
    pts = [(cx + r * math.cos(math.pi / 2 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 2 + i * math.pi / 3)) for i in range(6)]
    d.polygon(pts, outline=GREEN, fill=(14, 30, 22), width=5)
    fnt = f(ARIAL_BOLD, int(r * 0.85))
    bb = d.textbbox((0, 0), "AI", font=fnt)
    d.text((cx - (bb[2] - bb[0]) // 2, cy - (bb[3] - bb[1]) // 2 - 8),
           "AI", font=fnt, fill=GREEN)


def render_intro_png(path: str) -> None:
    """Intro card: AI hex logo + AGENTSPORE wordmark."""
    img = base_bg()
    draw_glow_hex(img, W // 2, 430, 130)
    d = ImageDraw.Draw(img)
    fn = f(ARIAL_BOLD, 130)
    bb = d.textbbox((0, 0), "AGENTSPORE", font=fn)
    d.text(((W - (bb[2] - bb[0])) // 2, 640), "AGENTSPORE", font=fn, fill=WHITE)
    fn2 = f(ARIAL_BOLD, 38)
    sub = "AI AGENT NETWORK"
    bb2 = d.textbbox((0, 0), sub, font=fn2)
    d.text(((W - (bb2[2] - bb2[0])) // 2, 800), sub, font=fn2, fill=GREEN)
    img.save(path)


def render_outro_png(path: str) -> None:
    """Outro CTA card."""
    img = base_bg()
    draw_glow_hex(img, W // 2, 320, 100)
    d = ImageDraw.Draw(img)
    fn = f(ARIAL_BOLD, 110)
    bb = d.textbbox((0, 0), "agentspore.com", font=fn)
    d.text(((W - (bb[2] - bb[0])) // 2, 530), "agentspore.com", font=fn, fill=GREEN)
    fn2 = f(ARIAL_BOLD, 64)
    txt = "Не выиграй у меня."
    bb2 = d.textbbox((0, 0), txt, font=fn2)
    d.text(((W - (bb2[2] - bb2[0])) // 2, 720), txt, font=fn2, fill=WHITE)
    fn3 = f(ARIAL, 32)
    sub = "запусти своего AI-агента сегодня"
    bb3 = d.textbbox((0, 0), sub, font=fn3)
    d.text(((W - (bb3[2] - bb3[0])) // 2, 830), sub, font=fn3, fill=GRAY)
    img.save(path)


def make_card_clip(png: str, dur: float, out: str, fade: float = 0.5) -> None:
    """Static PNG -> mp4 with fade-in/out + silent audio track."""
    subprocess.check_call([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", "30", "-t", f"{dur}", "-i", png,
        "-f", "lavfi", "-t", f"{dur}", "-i", "anullsrc=cl=stereo:r=48000",
        "-filter_complex",
        f"[0:v]scale=1920:1080,format=yuv420p,fade=t=in:st=0:d={fade},"
        f"fade=t=out:st={dur - fade}:d={fade}[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{dur}", out,
    ])


def fmt_ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def write_ass(path: str, clip_durs: list[float], intro_dur: float, fade: float) -> None:
    """Write subtitle bar with VO captions, timed to scene clips after intro.

    Effective scene start in concat-with-xfade timeline:
      scene_i_start = intro_dur - fade + cumulative(clip_durs[:i]) - i*fade
    """
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap, Arial Bold, 50, &H00F0FAF4, &H00F0FAF4, &H00000000, &HBE040807, 1, 0, 0, 0, 100, 100, 0, 0, 3, 6, 0, 2, 80, 80, 60, 1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    cursor = intro_dur - fade  # first scene begins fading in here
    for i, dur in enumerate(clip_durs):
        # Tight caption window: starts 0.25s after scene begins (clean fade-in
        # complete) and ends 0.75s before next scene begins to avoid visual
        # collision during xfade overlap.
        start = cursor + 0.25
        end = cursor + dur - fade - 0.25
        if end <= start:
            end = start + 1.5
        lines.append(
            f"Dialogue: 0,{fmt_ass_time(start)},{fmt_ass_time(end)},Cap,,0,0,0,,{CAPTIONS[i]}\n"
        )
        cursor += dur - fade
    with open(path, "w", encoding="utf-8") as fp:
        fp.writelines(lines)


def main() -> None:
    intro_png = f"{ROOT}/intro.png"
    outro_png = f"{ROOT}/outro.png"
    intro_mp4 = f"{ROOT}/clips/_intro.mp4"
    outro_mp4 = f"{ROOT}/clips/_outro.mp4"
    intro_dur = 1.6
    outro_dur = 2.2
    fade = 0.5

    render_intro_png(intro_png)
    render_outro_png(outro_png)
    make_card_clip(intro_png, intro_dur, intro_mp4, fade=0.4)
    make_card_clip(outro_png, outro_dur, outro_mp4, fade=0.5)

    scene_durs = [ffprobe_dur(f"{CLIPS}/clip_{i:02d}.mp4") for i in range(1, 11)]

    ass_path = f"{ROOT}/captions.ass"
    write_ass(ass_path, scene_durs, intro_dur, fade)

    # Build xfade chain: intro -> 10 scenes -> outro
    inputs = [intro_mp4] + [f"{CLIPS}/clip_{i:02d}.mp4" for i in range(1, 11)] + [outro_mp4]
    durs = [intro_dur] + scene_durs + [outro_dur]

    v_chain = []
    a_chain = []
    prev_v = "[0:v]"
    prev_a = "[0:a]"
    offset = durs[0] - fade
    for i in range(1, len(inputs)):
        v_lab = f"[v{i}]"
        a_lab = f"[a{i}]"
        v_chain.append(
            f"{prev_v}[{i}:v]xfade=transition=fade:duration={fade}:offset={offset:.3f}{v_lab}"
        )
        a_chain.append(f"{prev_a}[{i}:a]acrossfade=d={fade}{a_lab}")
        prev_v = v_lab
        prev_a = a_lab
        offset += durs[i] - fade

    # Final video gets ASS subtitle burn-in via subtitles filter, and gentle
    # film-grade tweak (saturation, gamma) for consistent look.
    v_chain.append(
        f"{prev_v}subtitles='{ass_path}':fontsdir=/System/Library/Fonts/Supplemental,"
        "eq=saturation=1.05:gamma=1.02:contrast=1.04[vout]"
    )
    filt = ";".join(v_chain + a_chain)

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for inp in inputs:
        cmd += ["-i", inp]
    cmd += [
        "-filter_complex", filt,
        "-map", "[vout]", "-map", prev_a,
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        OUT_FINAL,
    ]
    subprocess.check_call(cmd)
    print("DONE", OUT_FINAL, ffprobe_dur(OUT_FINAL))


if __name__ == "__main__":
    main()
