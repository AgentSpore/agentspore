"""Source selection: what the harvester asks the open web for.

No network — only the query construction, which is where a pool ends up all
one flavour.
"""

from __future__ import annotations

from app.services.battle_task_sources import GitHubIssueSource, default_sources


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

    def test_all_three_sources_are_listed(self):
        assert [s.name for s in default_sources(rotation=0)] == [
            "github",
            "stackexchange",
            "hackernews",
        ]
