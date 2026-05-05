"""TTS via OpenRouter openai/gpt-audio with streaming.

Reads JSONL of {idx, text} on stdin or args; writes mp3 per scene.
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error

API_KEY = os.environ["OPENROUTER_API_KEY"]
URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = (
    "Read the user's Russian text aloud one time, exactly as written, then stop. "
    "Do not add greetings, do not repeat, do not explain, do not translate, do not improvise. "
    "Voice: confident young male AI-agent narrator, brisk pace (~3.0 words/sec), clear pronunciation."
)


def synth(text: str, out_path: str, fmt: str = "mp3", voice: str = "ash") -> None:
    payload = {
        "model": "openai/gpt-audio",
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": "pcm16"},
        "stream": True,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://agentspore.com",
            "X-Title": "AgentSpore Video v9",
        },
    )
    pcm_chunks: list[bytes] = []
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            buf = b""
            for raw in r:
                buf += raw
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line or not line.startswith(b"data:"):
                        continue
                    data_s = line[5:].strip()
                    if data_s == b"[DONE]":
                        break
                    try:
                        ev = json.loads(data_s)
                    except json.JSONDecodeError:
                        continue
                    ch = ev.get("choices") or []
                    if not ch:
                        continue
                    delta = ch[0].get("delta") or {}
                    audio = delta.get("audio")
                    if audio and audio.get("data"):
                        pcm_chunks.append(base64.b64decode(audio["data"]))
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:2000], file=sys.stderr)
        raise
    if not pcm_chunks:
        raise RuntimeError(f"no audio chunks for: {text[:60]}")
    pcm = b"".join(pcm_chunks)
    raw_path = out_path + ".pcm"
    with open(raw_path, "wb") as f:
        f.write(pcm)
    # gpt-audio returns 24kHz mono pcm16
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-i",
            raw_path,
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            out_path,
        ],
        check=True,
    )
    os.remove(raw_path)
    print("wrote", out_path, len(pcm), "bytes pcm")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--script", required=True, help="JSON file: list of {idx, text}")
    p.add_argument("--outdir", required=True)
    args = p.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    with open(args.script) as f:
        items = json.load(f)
    for it in items:
        idx = it["idx"]
        text = it["text"]
        out = os.path.join(args.outdir, f"vo_{idx:02d}.mp3")
        if os.path.exists(out) and os.path.getsize(out) > 1024:
            print("skip existing", out)
            continue
        synth(text, out)


if __name__ == "__main__":
    main()
