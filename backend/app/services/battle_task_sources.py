"""Open, keyless topic sources for the task harvester.

Each source is read-only and needs no API key — verified live from the
production host (GitHub Search, Stack Exchange, HN Algolia all answer 200
without one). A source returns raw ``{"title", "summary"}`` pairs; it is the
harvester's job, not the source's, to turn those into a battle task — a source
must never hand back more than a title and a short summary, so nothing here
can smuggle a whole issue thread into the drafting prompt as "material".
"""

from __future__ import annotations

import time
from typing import Any

import httpx

_HTTP_TIMEOUT_SECONDS = 10.0
_SUMMARY_MAX_CHARS = 500


class _JsonSearchSource:
    """Shared shape: one GET, a list of hits, each mapped to a title/summary.

    Subclasses supply the endpoint, the query params, and how to reach the
    hit list and the two fields inside it — the request/parse plumbing is
    identical across all three sources, only that mapping differs.
    """

    name: str
    _url: str

    def _params(self, limit: int) -> dict[str, Any]:
        raise NotImplementedError

    def _hits(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _title(self, hit: dict[str, Any]) -> str:
        raise NotImplementedError

    def _summary(self, hit: dict[str, Any]) -> str:
        raise NotImplementedError

    def _accepts(self, hit: dict[str, Any]) -> bool:
        return True

    async def fetch_topics(self, limit: int) -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(self._url, params=self._params(limit))
        response.raise_for_status()
        return [
            {
                "title": self._title(hit)[:300],
                "summary": self._summary(hit)[:_SUMMARY_MAX_CHARS],
            }
            for hit in self._hits(response.json())
            if self._title(hit) and self._accepts(hit)
        ]


class GitHubIssueSource(_JsonSearchSource):
    """Recently-closed GitHub issues with a bug label — a real, solved problem."""

    name = "github"
    _url = "https://api.github.com/search/issues"
    # One ecosystem produced one flavour of task: pinning language:python made
    # every harvested topic a Python bug report. Rotating the query widens the
    # pool without widening the request budget — still one call per pass.
    _QUERIES = (
        "is:issue is:closed label:bug language:python",
        "is:issue is:closed label:bug language:typescript",
        "is:issue is:closed label:bug language:go",
        "is:issue is:closed label:bug language:rust",
        "is:issue is:closed label:performance",
        "is:issue is:closed label:security",
    )

    def __init__(self, query_index: int = 0) -> None:
        self._query = self._QUERIES[query_index % len(self._QUERIES)]

    def _params(self, limit: int) -> dict[str, Any]:
        return {"q": self._query, "sort": "updated", "per_page": limit}

    def _hits(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        return body.get("items", [])

    def _title(self, hit: dict[str, Any]) -> str:
        return str(hit.get("title", ""))

    def _summary(self, hit: dict[str, Any]) -> str:
        return str(hit.get("body") or "")


class StackExchangeSource(_JsonSearchSource):
    """Recent, answered questions from a Stack Exchange site.

    Defaults to Stack Overflow; the same client works for any other Stack
    Exchange site (``writing``, ``ux``, ``datascience``, ...) by passing
    ``site`` — the questions endpoint and response shape are identical
    across the network, only the site slug differs.
    """

    _url = "https://api.stackexchange.com/2.3/questions"

    def __init__(self, site: str = "stackoverflow", name: str = "stackexchange") -> None:
        self._site = site
        self.name = name

    def _params(self, limit: int) -> dict[str, Any]:
        return {
            "site": self._site,
            "order": "desc",
            "sort": "activity",
            "filter": "!nNPvSNe7Gv",  # includes .body
            "pagesize": limit,
        }

    def _hits(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        return body.get("items", [])

    def _title(self, hit: dict[str, Any]) -> str:
        return str(hit.get("title", ""))

    def _summary(self, hit: dict[str, Any]) -> str:
        return str(hit.get("body") or "")

    def _accepts(self, hit: dict[str, Any]) -> bool:
        return bool(hit.get("is_answered"))


class HackerNewsSource(_JsonSearchSource):
    """Recent front-page stories via the Algolia search API."""

    name = "hackernews"
    _url = "https://hn.algolia.com/api/v1/search"

    def _params(self, limit: int) -> dict[str, Any]:
        return {"tags": "story", "hitsPerPage": limit}

    def _hits(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        return body.get("hits", [])

    def _title(self, hit: dict[str, Any]) -> str:
        return str(hit.get("title", ""))

    def _summary(self, hit: dict[str, Any]) -> str:
        return str(hit.get("story_text") or "")


def default_sources(rotation: int | None = None) -> list[_JsonSearchSource]:
    """The harvester's out-of-the-box source list.

    A dead source is logged and skipped by the caller (TaskHarvesterService),
    so listing all three costs nothing when one is unreachable — it just
    yields fewer topics that pass.

    ``rotation`` picks which GitHub query this pass runs. It defaults to the
    current half-hour, which is the harvester's own interval, so consecutive
    passes sweep different ecosystems instead of re-reading one. Passing it
    explicitly keeps the choice testable.
    """
    if rotation is None:
        rotation = int(time.time() // 1800)
    return [
        GitHubIssueSource(query_index=rotation),
        StackExchangeSource(),
        # writing.stackexchange.com: non-programming, keyless, same client as
        # the line above — verified live (200, answered questions) before
        # adding. Widens the pool past code without a new source class.
        StackExchangeSource(site="writing", name="stackexchange-writing"),
        HackerNewsSource(),
    ]
