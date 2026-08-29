"""Rival check must widen a long phrase into its terms.

GitHub joins query words with AND, so a five-word phrase matches almost nothing:
measured 2026-08-29, "chaos engineering network fault injection" returned 6 repos
(top 10 stars) while "chaos engineering" alone returned 1480 (top 7861). Without
the widening the checker answered OPEN on a theme owned by chaos-mesh and jepsen,
and the scout agent created a duplicate product on that verdict.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_tools.check_rivals import check, expand, verdict
from agent_tools.check_rivals import github as check_rivals_github
from agent_tools.check_rivals import hackernews as check_rivals_hn


def test_expand_keeps_the_original_phrase_first():
    out = expand(["chaos engineering network fault injection"])
    assert out[0] == "chaos engineering network fault injection"


def test_expand_yields_the_short_terms_a_rival_lives_under():
    out = expand(["chaos engineering network fault injection"])
    assert "chaos engineering" in out
    assert "fault injection" in out


def test_expand_drops_stopwords_that_match_nothing():
    out = expand(["a tool for the network"])
    assert "a tool" not in out
    assert "for the" not in out


def test_expand_does_not_duplicate():
    out = expand(["chaos engineering", "chaos engineering"])
    assert len(out) == len(set(out))


@pytest.mark.parametrize(
    "top_stars,expected",
    [(7861, "CROWDED"), (1000, "CROWDED"), (400, "NICHE"), (100, "NICHE"), (12, "OPEN")],
)
def test_verdict_thresholds(top_stars, expected):
    assert verdict([{"stars": top_stars}]) == expected


def test_verdict_open_on_no_rivals():
    assert verdict([]) == "OPEN"


def test_check_searches_every_expanded_term(monkeypatch):
    """The widening is worthless if check() still queries the raw phrase only."""
    asked = []

    def fake_github(query, min_stars=100):
        asked.append(query)
        if query == "chaos engineering":
            return [{"kind": "repo", "stars": 7861, "name": "chaos-mesh/chaos-mesh",
                     "what": "", "url": ""}], None
        return [], None

    monkeypatch.setattr("agent_tools.check_rivals.github", fake_github)
    monkeypatch.setattr("agent_tools.check_rivals.hackernews", lambda q: ([], None))
    monkeypatch.setattr("agent_tools.check_rivals.time.sleep", lambda s: None)

    result = check(["chaos engineering network fault injection"])

    assert "chaos engineering" in asked
    assert result["verdict"] == "CROWDED"
    assert result["rivals"][0]["name"] == "chaos-mesh/chaos-mesh"


def test_github_drops_a_hit_that_only_shares_the_words(monkeypatch):
    """GitHub matches words anywhere, so a widened pair pulls in unrelated giants.

    Measured 2026-08-30: the pair "local first" returned home-assistant/core
    (90178 stars) and anything-llm (65370) — both merely contain both words.
    """
    payload = {
        "items": [
            {"full_name": "home-assistant/core", "stargazers_count": 90178,
             "description": "Open source home automation that puts local control first",
             "html_url": ""},
            {"full_name": "worstcase/blockade", "stargazers_count": 911,
             "description": "Docker-based utility for testing network failures",
             "html_url": ""},
        ]
    }
    monkeypatch.setattr("agent_tools.check_rivals._get", lambda url, params=None: payload)

    found, err = check_rivals_github("network failures", 100)

    assert err is None
    assert [r["name"] for r in found] == ["worstcase/blockade"]


def test_hackernews_drops_an_essay_that_only_shares_the_words(monkeypatch):
    payload = {
        "hits": [
            {"title": "My Second Year as a Solo Developer", "points": 1066, "objectID": "1"},
            {"title": "Show HN: a solo developer tool for network failure testing",
             "points": 40, "objectID": "2"},
        ]
    }
    monkeypatch.setattr("agent_tools.check_rivals._get", lambda url, params=None: payload)

    found, err = check_rivals_hn("network failure")

    assert err is None
    assert len(found) == 1
    assert "network failure" in found[0]["name"].lower()
