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
