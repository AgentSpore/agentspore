"""Сбор болей разработчиков из источников, доступных с серверного адреса.

Только стандартная библиотека: в песочнице агента httpx НЕТ (замер 2026-08-28).
Reddit исключён намеренно — top.json отдаёт 403, .rss отдаёт 429 с этого хоста.
"""
import json
import re
import time
import html
import urllib.request
import urllib.parse
import gzip
import io

UA = "AgentSpore/1.0 (+https://agentspore.com)"
TIMEOUT = 25

PAIN = re.compile(r"(struggl|frustrat|tedious|manual(ly)?|wish there|no good way|"
                  r"waste[sd]? (time|hours)|can'?t find|is there (a|any)|painful|nightmare|"
                  r"too (slow|expensive|complex)|how (do|can|to) .{0,30}(automat|track|monitor|"
                  r"detect|sync|migrat|debug|deploy|test|scale)|best way to|anyone else|"
                  r"why is .{0,25} so |alternative to)", re.I)
NOISE = re.compile(r"(should i (leave|quit|join|study)|what should i learn|which books|"
                   r"career|salary|interview|hiring|laid off|burnout|am i too old|who is hiring)", re.I)


def _get(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return json.loads(raw.decode("utf-8", "replace"))


def _hn(tag, n):
    d = _get("https://hn.algolia.com/api/v1/search_by_date",
             {"tags": tag, "hitsPerPage": n})
    return [{"src": tag, "title": h["title"], "score": h.get("points") or 0,
             "url": "https://news.ycombinator.com/item?id=" + h["objectID"],
             "text": (h.get("story_text") or "")[:500]}
            for h in d.get("hits", []) if h.get("title")]


def _se(site, n=60):
    """Свежие вопросы, не 'лучшие за всё время' — боль должна быть сегодняшней."""
    d = _get("https://api.stackexchange.com/2.3/questions",
             {"order": "desc", "sort": "creation", "site": site, "pagesize": n})
    return [{"src": "se-" + site, "title": html.unescape(q["title"]),
             "score": max(q.get("score") or 0, 0) + (q.get("view_count", 0) // 100),
             "url": q.get("link", ""), "text": ""}
            for q in d.get("items", [])]


def _lob(n=25):
    d = _get("https://lobste.rs/hottest.json")
    return [{"src": "lobsters", "title": s["title"], "score": s.get("score") or 0,
             "url": s.get("short_id_url", ""), "text": ""} for s in d[:n]]


SITES = ["softwareengineering", "devops", "dba", "serverfault", "webmasters"]


# Ask HN sorted by date gives whatever was posted today; these three phrasings
# find the questions where someone describes a workflow they pay for in time or
# money. Measured 2026-08-30: 15 such questions in the last 180 days with 10+
# points ("How do you handle clients who don't pay on time?", 39 points) versus
# a top-of-week list that returned the same crowded question three days running.
ASK_QUERIES = ("how do you handle", "what do you use for", "is there a better way")
ASK_MIN_POINTS = 10
ASK_WINDOW_DAYS = 180


def _hn_ask(query, min_points=ASK_MIN_POINTS):
    cutoff = int(time.time()) - ASK_WINDOW_DAYS * 86400
    d = _get("https://hn.algolia.com/api/v1/search",
             {"query": query, "tags": "ask_hn", "hitsPerPage": 50,
              "numericFilters": "created_at_i>" + str(cutoff)})
    return [{"src": "ask_hn_q", "title": h["title"], "score": h.get("points") or 0,
             "url": "https://news.ycombinator.com/item?id=" + h["objectID"],
             "text": (h.get("story_text") or "")[:500]}
            for h in d.get("hits", [])
            if h.get("title") and (h.get("points") or 0) >= min_points]


def collect():
    items, errors = [], []
    jobs = [("ask_hn", lambda: _hn("ask_hn", 80)), ("lobsters", _lob)]
    jobs += [("ask_hn_q:" + q, (lambda q: lambda: _hn_ask(q))(q)) for q in ASK_QUERIES]
    jobs += [("se-" + s, (lambda s: lambda: _se(s))(s)) for s in SITES]
    for name, fn in jobs:
        try:
            items += fn()
        except Exception as exc:
            errors.append(name + ": " + type(exc).__name__)
    return items, errors


def rank(items):
    out = [i for i in items
           if not NOISE.search(i["title"]) and PAIN.search(i["title"] + " " + i["text"])]
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


if __name__ == "__main__":
    items, errors = collect()
    pains = rank(items)
    by_src = {}
    for i in pains:
        by_src[i["src"]] = by_src.get(i["src"], 0) + 1
    print(json.dumps({"total": len(items), "pains": len(pains), "errors": errors,
                      "by_src": by_src,
                      "top": [{k: v for k, v in i.items() if k != "text"} for i in pains[:10]]},
                     ensure_ascii=False, indent=1))
