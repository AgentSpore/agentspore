"""Source selection: what the harvester asks the open web for.

No network — only the query construction, which is where a pool ends up all
one flavour.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.battle_task_sources import (
    GitHubIssueSource,
    StackExchangeSource,
    default_sources,
)


class TestGitHubQueryRotation:
    def test_consecutive_rotations_ask_different_questions(self):
        """Pinning one query is what made every topic a Python bug report."""
        queries = {GitHubIssueSource(query_index=i)._params(5)["q"] for i in range(6)}

        assert len(queries) == 6

    def test_rotation_wraps_instead_of_failing(self):
        """The caller passes a half-hour counter, which grows without bound."""
        first = GitHubIssueSource(query_index=0)._params(5)["q"]
        wrapped = GitHubIssueSource(query_index=len(GitHubIssueSource._QUERIES))._params(5)["q"]

        assert wrapped == first

    def test_the_pool_is_not_one_ecosystem(self):
        langs = {
            GitHubIssueSource(query_index=i)._params(5)["q"]
            for i in range(len(GitHubIssueSource._QUERIES))
        }
        assert any("language:typescript" in q for q in langs)
        assert any("language:go" in q for q in langs)
        assert any("label:security" in q for q in langs)


class TestDefaultSources:
    def test_rotation_reaches_the_github_source(self):
        a = default_sources(rotation=0)[0]._params(5)["q"]
        b = default_sources(rotation=1)[0]._params(5)["q"]

        assert a != b

    def test_the_default_rotation_follows_the_clock(self):
        """The production path takes no argument, so pinning it would be silent.

        Every other test here passes `rotation=` explicitly; mutating the
        default to a constant — the exact pinned-query regression this change
        fixes — left all of them green.
        """
        with patch("app.services.battle_task_sources.time.time", return_value=0.0):
            first = default_sources()[0]._params(5)["q"]
        with patch("app.services.battle_task_sources.time.time", return_value=1800.0):
            second = default_sources()[0]._params(5)["q"]

        assert first != second
        assert first == GitHubIssueSource(query_index=0)._params(5)["q"]
        assert second == GitHubIssueSource(query_index=1)._params(5)["q"]

    def test_all_sources_are_listed(self):
        assert [s.name for s in default_sources(rotation=0)] == [
            "github",
            "stackexchange",
            "stackexchange-writing",
            "hackernews",
        ]


class TestStackExchangeSite:
    def test_default_site_is_stackoverflow(self):
        assert StackExchangeSource()._params(5)["site"] == "stackoverflow"

    def test_site_is_configurable(self):
        source = StackExchangeSource(site="writing", name="stackexchange-writing")
        assert source._params(5)["site"] == "writing"
        assert source.name == "stackexchange-writing"
