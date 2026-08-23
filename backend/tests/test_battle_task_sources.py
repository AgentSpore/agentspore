"""Source selection: what the harvester asks the open web for.

No network — only the query construction, which is where a pool ends up all
one flavour.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.battle_task_sources import (
    _NON_PROGRAMMING_SE_SITES,
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

    def test_non_programming_site_rotates_with_the_same_counter(self):
        """A site missing from this list is a site the harvester never queries.

        This is the wiring check: `StackExchangeSource(site=...)` existing as
        a class is not the same as `default_sources()` handing one out. If a
        non-programming site is ever dropped from `_NON_PROGRAMMING_SE_SITES`
        or from the returned list, this reddens — a class no caller reaches
        cannot widen anything.
        """
        sites_seen = {
            default_sources(rotation=i)[2]._params(5)["site"]
            for i in range(len(_NON_PROGRAMMING_SE_SITES))
        }
        assert sites_seen == set(_NON_PROGRAMMING_SE_SITES)

    def test_non_programming_source_name_reflects_its_site(self):
        """A shared `name` for every site would merge them in logs and stats."""
        names = {default_sources(rotation=i)[2].name for i in range(len(_NON_PROGRAMMING_SE_SITES))}
        assert names == {f"stackexchange-{site}" for site in _NON_PROGRAMMING_SE_SITES}

    def test_programming_and_non_programming_stackexchange_names_differ(self):
        sources = default_sources(rotation=0)
        stackoverflow_source, non_programming_source = sources[1], sources[2]
        assert stackoverflow_source.name != non_programming_source.name


class TestStackExchangeSite:
    def test_default_site_is_stackoverflow(self):
        assert StackExchangeSource()._params(5)["site"] == "stackoverflow"

    def test_site_is_configurable(self):
        source = StackExchangeSource(site="writing", name="stackexchange-writing")
        assert source._params(5)["site"] == "writing"
        assert source.name == "stackexchange-writing"
