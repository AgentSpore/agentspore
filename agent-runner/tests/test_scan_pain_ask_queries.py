"""Ask HN searched by phrasing, not just by date.

Sorted by date, Ask HN returns whatever was posted today, and the pain filter
then keeps the same crowded question for days. Measured 2026-08-30: the
top pain was an eight-thousand-star-rival theme three runs running, while
"How do you handle clients who don't pay on time?" (39 points) was never seen.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_tools.scan_pain import ASK_MIN_POINTS, ASK_QUERIES, _hn_ask, collect


def test_queries_target_workflow_questions():
    assert "how do you handle" in ASK_QUERIES
    assert "is there a better way" in ASK_QUERIES


def test_hn_ask_drops_low_signal_stories(monkeypatch):
    payload = {"hits": [
        {"title": "Ask HN: How do you handle clients who don't pay on time?",
         "points": 39, "objectID": "1", "story_text": ""},
        {"title": "Ask HN: nobody upvoted this", "points": 2, "objectID": "2", "story_text": ""},
    ]}
    monkeypatch.setattr("agent_tools.scan_pain._get", lambda url, params=None: payload)

    out = _hn_ask("how do you handle")

    assert [h["title"] for h in out] == [
        "Ask HN: How do you handle clients who don't pay on time?"
    ]
    assert out[0]["src"] == "ask_hn_q"


def test_hn_ask_asks_for_a_recent_window(monkeypatch):
    """Without the window the search returns a decade of stale questions."""
    seen = {}

    def fake_get(url, params=None):
        seen.update(params or {})
        return {"hits": []}

    monkeypatch.setattr("agent_tools.scan_pain._get", fake_get)
    _hn_ask("how do you handle")

    assert seen["tags"] == "ask_hn"
    assert "created_at_i>" in seen["numericFilters"]


@pytest.mark.parametrize("points,kept", [(ASK_MIN_POINTS, True), (ASK_MIN_POINTS - 1, False)])
def test_point_threshold_boundary(monkeypatch, points, kept):
    payload = {"hits": [{"title": "Ask HN: is there a better way to bill retainers?",
                         "points": points, "objectID": "9", "story_text": ""}]}
    monkeypatch.setattr("agent_tools.scan_pain._get", lambda url, params=None: payload)

    assert bool(_hn_ask("is there a better way")) is kept


def test_collect_runs_one_job_per_query(monkeypatch):
    """The phrasings must each hit the API — one shared call would lose two."""
    asked = []

    def fake_hn_ask(query, min_points=ASK_MIN_POINTS):
        asked.append(query)
        return []

    monkeypatch.setattr("agent_tools.scan_pain._hn_ask", fake_hn_ask)
    monkeypatch.setattr("agent_tools.scan_pain._hn", lambda tag, n: [])
    monkeypatch.setattr("agent_tools.scan_pain._lob", lambda: [])
    monkeypatch.setattr("agent_tools.scan_pain._se", lambda site: [])

    collect()

    assert sorted(asked) == sorted(ASK_QUERIES)
