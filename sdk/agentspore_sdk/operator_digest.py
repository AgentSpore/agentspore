"""Finite evidence selection and deterministic public operator reports."""

import hashlib
import json
from datetime import UTC, datetime

SOURCES = ("counters", "projects", "github_repos", "github_events", "github_ci")
COUNTERS = {
    "total_agents": "Registered agents (lifetime)",
    "active_agents": "Active agents (24h)",
    "total_projects": "Current projects",
    "total_code_commits": "Recorded commits (lifetime)",
    "total_reviews": "Recorded reviews (lifetime)",
    "projects_deployed": "Projects marked deployed",
    "total_repos_created": "Projects with repositories",
    "total_prs_merged": "Merged PR counter (lifetime)",
}
HOSTS = {
    "https://agentspore.com": "Platform",
    "https://podmemory.agentspore.com": "PodMemory",
    "https://otkrytka.agentspore.com": "Otkrytka",
    "https://pereklichka.agentspore.com": "Pereklichka",
}
RECOMMENDATIONS = {
    "verify_instrumentation": "Verify measurement coverage.",
    "inspect_availability": "Inspect the observed availability issue.",
    "review_ci": "Review the observed CI results.",
    "review_project_activity": "Review project activity in the bounded feed.",
    "request_feedback": "Ask users which improvements would help them.",
}
REASONS = {
    "source_unreachable": "source unreachable",
    "pagination_incomplete": "pagination incomplete",
    "source_shape_invalid": "source shape invalid",
    "source_window_not_verified": "source time window not verified",
    "instrumentation_not_verified": "instrumentation not verified",
    "cohort_coverage_not_verified": "cohort coverage not verified",
}
LIMITATIONS = (
    "Usage and retention are unavailable until measurement coverage is verified. "
    "Raw zero is not proof of no users. Cookie IDs are not verified humans. "
    "Platform, Telegram and Alice usage are not established by web usage data. "
    "Counters marked lifetime are not daily activity; deployed is a catalog state. "
    "HTTP status is a point-in-time observation, not daily uptime. "
    "GitHub events are a partial feed, not a complete commit count."
)


def canonical(value) -> bytes:
    """Serialize finite JSON deterministically for the snapshot digest."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid JSON data") from exc


def checked_sources(snapshot: dict) -> dict:
    """Validate mandatory observation shapes without trusting source annotations."""
    return {name: checked_observation(name, snapshot.get(name)) for name in SOURCES}


def checked_observation(name: str, item) -> dict:
    """Reject impossible completeness and resource shapes at the IPC boundary."""
    if not isinstance(item, dict) or set(item) != {"value", "status", "reason"}:
        raise ValueError("invalid observation")
    checked_coverage(item)
    expected = dict if name == "counters" else list
    if item["value"] is not None and not isinstance(item["value"], expected):
        raise ValueError("invalid observation value")
    limits = {
        "projects": 200,
        "github_repos": 100,
        "github_events": 300,
        "github_ci": 50,
    }
    if isinstance(item["value"], list) and len(item["value"]) > limits[name]:
        raise ValueError("source limit exceeded")
    checked_values(name, item)
    return item


def checked_values(name: str, item: dict) -> None:
    """Reject malformed records instead of reporting complete empty evidence."""
    value = item["value"]
    if value is None:
        return
    if name == "counters":
        checked_counters(value, item["status"] == "complete")
        return
    if name == "github_repos":
        if any(
            not isinstance(row, str) or not row.startswith("AgentSpore/")
            for row in value
        ):
            raise ValueError("invalid repository record")
        return
    required = {
        "projects": {"id"},
        "github_events": {"id", "repo", "type", "created_at"},
        "github_ci": {"repo", "status", "reason", "value"},
    }[name]
    if any(not isinstance(row, dict) or not required.issubset(row) for row in value):
        raise ValueError("invalid feed record")


def evidence_bundle(snapshot: dict) -> dict:
    """Return bounded public facts, bound to the full canonical input snapshot."""
    if not isinstance(snapshot, dict):
        raise TypeError("invalid snapshot")
    raw = canonical(snapshot)
    if len(raw) > 1_048_576 or not isinstance(snapshot.get("health"), dict):
        raise ValueError("invalid snapshot size or health")
    sources = checked_sources(snapshot)
    window = snapshot.get("window")
    if not isinstance(window, dict) or set(window) != {"since", "until"}:
        raise ValueError("invalid window")
    try:
        stamps = [datetime.fromisoformat(window[k]) for k in ("since", "until")]
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid window") from exc
    if any(s.tzinfo is None for s in stamps) or stamps[0] >= stamps[1]:
        raise ValueError("invalid window")
    facts = snapshot_facts(snapshot, sources)
    facts["feed.ci_outcomes"] = ci_outcomes(sources["github_ci"])
    coverage = coverage_summary(sources)
    return {
        "snapshot_digest": hashlib.sha256(raw).hexdigest(),
        "window": {
            k: stamp.astimezone(UTC).isoformat()
            for k, stamp in zip(("since", "until"), stamps, strict=True)
        },
        "facts": facts,
        "coverage": coverage,
    }


def unique_object(pairs: list) -> dict:
    """Reject duplicate JSON keys, including nested recommendation objects."""
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def checked_ids(values, facts: dict) -> None:
    """Accept a bounded unique selection of known evidence IDs."""
    if not isinstance(values, list) or len(values) > 8:
        raise ValueError("invalid evidence selection")
    if any(not isinstance(v, str) or v not in facts for v in values):
        raise ValueError("unknown evidence ID")
    if len(set(values)) != len(values):
        raise ValueError("duplicate evidence ID")


def parse_analysis(text: str, bundle: dict) -> dict:
    """Parse a strict selection schema; model output never supplies public prose."""
    if not isinstance(text, str) or len(text.encode()) > 65536:
        raise ValueError("analysis too large")
    try:
        result = json.loads(text, object_pairs_hook=unique_object)
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid analysis JSON") from exc
    canonical(result)
    fields = {"snapshot_digest", "highlights", "attention", "recommendations"}
    if not isinstance(result, dict) or set(result) != fields:
        raise ValueError("invalid analysis fields")
    if result["snapshot_digest"] != bundle["snapshot_digest"]:
        raise ValueError("snapshot digest mismatch")
    for name in ("highlights", "attention"):
        checked_ids(result[name], bundle["facts"])
    checked_recommendations(result["recommendations"], bundle["facts"])
    return result


def render_digest(snapshot: dict, analysis: dict) -> dict:
    """Render bounded chat/blog payloads using only controller-owned language."""
    bundle = evidence_bundle(snapshot)
    selected = parse_analysis(canonical(analysis).decode(), bundle)
    window = bundle["window"]
    heading = f"AgentSpore review: {window['since']} to {window['until']} (UTC)."
    coverage = (
        "Coverage: "
        + "; ".join(
            f"{k}={v['status']}"
            + (
                " (" + ", ".join(REASONS[r] for r in v["reasons"]) + ")"
                if v["reasons"]
                else ""
            )
            for k, v in bundle["coverage"].items()
        )
        + "."
    )
    links = (
        "Platform: https://agentspore.com\nPublic code: https://github.com/AgentSpore"
    )
    suffix = "\n\n" + coverage + "\n" + LIMITATIONS + "\n" + links
    order = list(
        dict.fromkeys(
            selected["attention"] + selected["highlights"] + list(bundle["facts"])
        )
    )
    chat = heading
    for fact in order:
        line = "\n" + bundle["facts"][fact]
        if len(chat + line + suffix) <= 2000:
            chat += line
    blog = heading + "\n\n" + "\n".join(bundle["facts"].values())
    for item in selected["recommendations"]:
        blog += (
            "\n"
            + RECOMMENDATIONS[item["kind"]]
            + " Evidence: "
            + ", ".join(item["evidence_ids"])
            + "."
        )
    return {
        "chat": {"message_type": "text", "content": chat + suffix},
        "blog": {
            "title": "AgentSpore daily review: " + window["until"][:10],
            "content": blog + suffix,
        },
    }


def snapshot_facts(snapshot: dict, sources: dict) -> dict:
    """Build finite public statements without copying labels or source text."""
    facts = {}
    for origin, label in HOSTS.items():
        value = snapshot["health"].get(origin)
        text = (
            str(value) if type(value) is int and 100 <= value <= 599 else "unavailable"
        )
        facts["health." + label.lower()] = f"{label} HTTP status: {text}."
    for name, label in COUNTERS.items():
        value = (sources["counters"]["value"] or {}).get(name)
        text = (
            str(value) if type(value) is int and 0 <= value <= 10**15 else "unavailable"
        )
        facts["counter." + name] = f"{label}: {text}."
    for name in SOURCES[1:]:
        value = sources[name]["value"]
        count = str(len(value)) if isinstance(value, list) else "unavailable"
        facts["feed." + name] = f"Observed {name.replace('_', ' ')} records: {count}."
    return facts


def checked_recommendations(recommendations, facts: dict) -> None:
    """Accept only finite suggestions linked to existing public evidence."""
    if not isinstance(recommendations, list) or len(recommendations) > 4:
        raise ValueError("invalid recommendations")
    for recommendation in recommendations:
        if not isinstance(recommendation, dict) or set(recommendation) != {
            "kind",
            "evidence_ids",
        }:
            raise ValueError("invalid recommendation fields")
        if (
            not isinstance(recommendation["kind"], str)
            or recommendation["kind"] not in RECOMMENDATIONS
        ):
            raise ValueError("invalid recommendation kind")
        checked_ids(recommendation["evidence_ids"], facts)
        if not recommendation["evidence_ids"]:
            raise ValueError("recommendation needs evidence")


def ci_outcomes(source: dict) -> str:
    """Distinguish successful empty CI feeds from entirely unavailable outcomes."""
    entries = source["value"]
    if entries is None:
        return "Observed CI outcomes: unavailable."
    feeds = [checked_ci_feed(entry) for entry in entries]
    if source["status"] == "complete" and any(
        entry["status"] != "complete" for entry in entries
    ):
        raise ValueError("inconsistent CI parent coverage")
    available = [rows for rows in feeds if rows is not None]
    if not available and (source["status"] != "complete" or feeds):
        return "Observed CI outcomes: unavailable."
    runs = [checked_outcome(row) for rows in available for row in rows]
    failures = sum(
        r.get("conclusion") in ("failure", "timed_out", "startup_failure") for r in runs
    )
    return f"Observed CI runs from available feed entries: {len(runs)}; failures: {failures}."


def checked_ci_feed(entry) -> list | None:
    """Validate nested observations before interpreting missing or empty runs."""
    if not isinstance(entry, dict):
        raise TypeError("invalid CI entry")
    checked_coverage(entry)
    rows = entry["value"]
    if rows is not None and (not isinstance(rows, list) or len(rows) > 20):
        raise ValueError("invalid CI rows")
    return rows


def checked_outcome(row) -> dict:
    """Check the only CI fields used by the public summary."""
    if not (
        isinstance(row, dict)
        and type(row.get("id")) is int
        and isinstance(row.get("status"), str)
        and isinstance(row.get("conclusion"), (str, type(None)))
    ):
        raise TypeError("invalid CI outcome")
    return row


def checked_counters(value: dict, complete: bool) -> None:
    """Require finite known integer counters and full keys for complete coverage."""
    if any(
        k not in COUNTERS or type(v) is not int or not 0 <= v <= 10**15
        for k, v in value.items()
    ):
        raise ValueError("invalid counter")
    if complete and set(value) != set(COUNTERS):
        raise ValueError("missing required counters")


def checked_coverage(item: dict) -> None:
    """Accept only congruent observation states and controlled finite reasons."""
    if not {"status", "reason", "value"}.issubset(item):
        raise ValueError("missing observation fields")
    if item["status"] not in ("complete", "partial", "unavailable"):
        raise ValueError("invalid coverage status")
    reason = item["reason"]
    if reason is not None and (not isinstance(reason, str) or reason not in REASONS):
        raise ValueError("invalid coverage reason")
    if (item["status"] == "complete") != (reason is None):
        raise ValueError("inconsistent coverage reason")
    if (item["status"] == "unavailable") != (item["value"] is None):
        raise ValueError("inconsistent coverage value")


def coverage_summary(sources: dict) -> dict:
    """Retain source reasons without copying arbitrary source prose."""
    result = {}
    for name, item in sources.items():
        reasons = {item["reason"]} - {None}
        if name == "github_ci":
            reasons.update(
                entry["reason"]
                for entry in item["value"] or []
                if entry["reason"] is not None
            )
        result[name] = {"status": item["status"], "reasons": sorted(reasons)}
    return result
