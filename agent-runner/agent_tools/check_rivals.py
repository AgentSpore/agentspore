"""Проверка идеи на существующих конкурентов.

Только стандартная библиотека: в песочнице агента httpx НЕТ (замер 2026-08-28).
Возвращает вердикт CROWDED / NICHE / OPEN плюс найденных соперников с доказательством.
"""
import json
import re
import sys
import urllib.request
import urllib.parse
import gzip
import io
import time

UA = "AgentSpore/1.0 (+https://agentspore.com)"
TIMEOUT = 25


def _get(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return json.loads(raw.decode("utf-8", "replace"))


_STOP = {"the", "a", "an", "for", "and", "or", "of", "to", "in", "on", "with", "tool", "app"}


def expand(queries):
    """Одна длинная фраза находит только точное совпадение: GitHub соединяет слова
    через И. 'chaos engineering network fault injection' даёт 6 репозиториев (топ 10
    звёзд), а 'chaos engineering' — 1480 (топ 7861). Поэтому к каждой фразе
    добавляем её пары соседних слов: зрелый аналог живёт под коротким именем.
    """
    out = []
    for q in queries:
        if q not in out:
            out.append(q)
        words = [w for w in re.findall(r"[a-zA-Z0-9+#.]+", q.lower()) if w not in _STOP]
        for i in range(len(words) - 1):
            pair = words[i] + " " + words[i + 1]
            if pair != q.lower() and pair not in out:
                out.append(pair)
    return out


def github(query, min_stars=100):
    """Открытые аналоги. Звёзды — грубая, но честная мера зрелости."""
    try:
        d = _get("https://api.github.com/search/repositories",
                 {"q": query, "sort": "stars", "per_page": 8})
    except Exception as exc:
        return [], "github: " + type(exc).__name__
    out = []
    phrase = " ".join(w for w in re.findall(r"[a-zA-Z0-9+#.]+", query.lower()) if w not in _STOP)
    for r in d.get("items", []):
        if (r.get("stargazers_count") or 0) < min_stars:
            continue
        # GitHub matches the words anywhere, so a widened pair like "local first"
        # drags in giants that merely contain both (home-assistant/core, anything-llm).
        # Require the phrase itself in the repo's own name or description.
        text = (r["full_name"] + " " + (r.get("description") or "")).lower()
        if phrase and phrase not in text:
            continue
        out.append({"kind": "repo", "stars": r["stargazers_count"], "name": r["full_name"],
                    "what": (r.get("description") or "")[:90],
                    "url": r.get("html_url", "")})
    return out, None


def hackernews(query):
    """Платные и закрытые продукты всплывают в Show HN, где открытого кода нет."""
    try:
        d = _get("https://hn.algolia.com/api/v1/search",
                 {"query": query, "hitsPerPage": 8, "tags": "story"})
    except Exception as exc:
        return [], "hn: " + type(exc).__name__
    out = []
    phrase = " ".join(w for w in re.findall(r"[a-zA-Z0-9+#.]+", query.lower()) if w not in _STOP)
    for h in d.get("hits", []):
        title = h.get("title") or ""
        if (h.get("points") or 0) < 20:
            continue
        # Algolia ranks by relevance, not by containment: a widened pair matches
        # essays that merely share the words. Require the phrase in the title.
        if phrase and phrase not in title.lower():
            continue
        out.append({"kind": "launch", "stars": h.get("points") or 0, "name": title[:70],
                    "what": "", "url": "https://news.ycombinator.com/item?id=" + h["objectID"]})
    return out, None


def verdict(rivals):
    """CROWDED — есть зрелый аналог, брать идею нельзя без явного отличия.
    NICHE — аналоги есть, но мелкие: нужен внятный угол.
    OPEN — заметных аналогов не нашлось."""
    if not rivals:
        return "OPEN"
    top = max(r["stars"] for r in rivals)
    if top >= 1000:
        return "CROWDED"
    if top >= 100:
        return "NICHE"
    return "OPEN"


def check(queries, min_stars=100):
    rivals, errors = [], []
    for q in expand(queries):
        for fn in (github, hackernews):
            found, err = fn(q) if fn is hackernews else fn(q, min_stars)
            rivals += found
            if err:
                errors.append(err)
            time.sleep(1)
    seen, uniq = set(), []
    for r in sorted(rivals, key=lambda x: -x["stars"]):
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        uniq.append(r)
    return {"verdict": verdict(uniq), "rivals": uniq[:6], "errors": errors,
            "queries": expand(queries)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: check_rivals.py 'query one' ['query two' ...]"}))
        raise SystemExit(1)
    print(json.dumps(check(sys.argv[1:]), ensure_ascii=False, indent=1))
