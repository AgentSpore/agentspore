"""Quick TTS test via OpenRouter openai/gpt-audio."""
import os
import sys
import base64
import json
import urllib.request

API_KEY = os.environ["OPENROUTER_API_KEY"]
URL = "https://openrouter.ai/api/v1/chat/completions"

text = "Я AI-агент. Живу на AgentSpore."

payload = {
    "model": "openai/gpt-audio",
    "modalities": ["text", "audio"],
    "audio": {"voice": "ash", "format": "mp3"},
    "messages": [
        {
            "role": "system",
            "content": "You are a Russian male voice narrator. Read the user's text exactly as given, in Russian, with confident energetic delivery.",
        },
        {"role": "user", "content": text},
    ],
}
req = urllib.request.Request(
    URL,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://agentspore.com",
        "X-Title": "AgentSpore Video v9",
    },
)
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:2000])
    sys.exit(1)
print(json.dumps({k: v for k, v in data.items() if k != "choices"}, indent=2)[:500])
ch = data["choices"][0]["message"]
audio = ch.get("audio")
if not audio:
    print("NO AUDIO; full message:", json.dumps(ch, indent=2)[:1000])
    sys.exit(1)
b64 = audio["data"]
out = "/tmp/tts_test.mp3"
with open(out, "wb") as f:
    f.write(base64.b64decode(b64))
print("written", out, "transcript:", audio.get("transcript"))
