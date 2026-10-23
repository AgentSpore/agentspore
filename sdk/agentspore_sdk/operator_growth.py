"""Run public product research and one durably reserved prototype publication."""

import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .operator_controller import (
    STATE,
    ask_analyst,
    checked_usage,
    controller_lock,
    private_file,
    record_usage,
    write_state,
)
from .operator_digest import canonical, unique_object
from .operator_growth_contract import parse_growth, service_digest
from .operator_growth_publish import GrowthPublisher
from .operator_growth_sources import collect_growth
from .operator_ledger import OperatorLedger

PENDING = {
    "create_reserved",
    "created",
    "push_reserved",
    "create_uncertain",
    "push_uncertain",
}
COMPLETE = {"research_complete", "prototype_published"}
STATES = (
    PENDING | COMPLETE | {"analysis_reserved", "analysis_failed", "analysis_uncertain"}
)
FIELDS = {
    "state",
    "observed_at",
    "signal_digest",
    "proposal",
    "verification",
    "publication",
}


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("growth_clock_invalid")
    return value.astimezone(UTC)


def _previous(state: Path, now: datetime) -> dict | None:
    try:
        descriptor = private_file(state / "growth.json", os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("growth_state_too_large")
    value = json.loads(content, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("growth_state_invalid")
    if value["state"] not in STATES or not isinstance(value["signal_digest"], str):
        raise ValueError("growth_state_invalid")
    if re.fullmatch(r"[0-9a-f]{64}", value["signal_digest"]) is None:
        raise ValueError("growth_state_invalid")
    stamp = datetime.fromisoformat(value["observed_at"])
    if stamp.tzinfo is None or stamp > now:
        raise ValueError("growth_clock_invalid")
    canonical(value)
    return value


def _signal(bundle: dict) -> str:
    evidence = {
        "facts": bundle["facts"],
        "source_urls": bundle["coverage"]["source_urls"],
        "source_kinds": bundle["coverage"]["source_kinds"],
    }
    return hashlib.sha256(canonical(evidence)).hexdigest()


def _history(bundle: dict, previous: dict | None) -> None:
    if previous is None or previous["proposal"] is None:
        return
    if not isinstance(previous["proposal"], dict):
        raise TypeError("growth_history_invalid")
    ideas = previous["proposal"].get("ideas")
    if not isinstance(ideas, list) or len(ideas) > 5:
        raise ValueError("growth_history_invalid")
    for idea in ideas:
        if (
            not isinstance(idea, dict)
            or set(idea) != {"evidence_ids", "decision", "reason", "target_user"}
            or idea["decision"] not in ("extend", "create", "defer")
            or not isinstance(idea["reason"], str)
            or not isinstance(idea["target_user"], str)
            or not isinstance(idea["evidence_ids"], list)
        ):
            raise ValueError("growth_history_invalid")
    bundle["coverage"]["previous_decisions"] = ideas
    service = previous["proposal"].get("service")
    if previous["state"] == "prototype_published" and isinstance(service, dict):
        title = service.get("title")
        if not isinstance(title, str) or not 3 <= len(title) <= 70:
            raise ValueError("growth_history_invalid")
        bundle["coverage"]["project_titles"].append(title)
    del bundle["snapshot_digest"]
    bundle["snapshot_digest"] = hashlib.sha256(canonical(bundle)).hexdigest()


def _checked_reply(reply: dict, bundle: dict) -> tuple[dict, dict | None]:
    if not isinstance(reply, dict) or set(reply) != {
        "analysis",
        "usage",
        "verification",
    }:
        raise ValueError("growth_reply_invalid")
    checked_usage(reply["usage"], 10**12)
    proposal = parse_growth(reply["analysis"], bundle)
    verification, service = reply["verification"], proposal["service"]
    if service is None:
        if verification is not None:
            raise ValueError("growth_verification_invalid")
    elif (
        not isinstance(verification, dict)
        or set(verification) != {"service_digest", "tests_passed"}
        or verification["service_digest"] != service_digest(service)
        or type(verification["tests_passed"]) is not int
        or not 1 <= verification["tests_passed"] <= 1000000
    ):
        raise ValueError("growth_verification_invalid")
    return proposal, verification


def _summary(proposal: dict, bundle: dict) -> dict:
    candidates = [
        {
            **candidate,
            "source_url": bundle["coverage"]["source_urls"][candidate["evidence_id"]],
            "join_interest": "unknown",
            "contact_sent": False,
        }
        for candidate in proposal["candidates"]
    ]
    service = proposal["service"]
    return {
        "snapshot_digest": proposal["snapshot_digest"],
        "ideas": proposal["ideas"],
        "candidates": candidates,
        "service": None
        if service is None
        else {
            "title": service["title"],
            "description": service["description"],
            "source_ids": service["source_ids"],
            "service_digest": service_digest(service),
            "file_paths": [entry["path"] for entry in service["files"]],
        },
    }


def _save(state: Path, record: dict, outcome: str) -> None:
    record["state"] = outcome
    write_state(state / "growth.json", record, max_bytes=65536)


async def _publish(
    state: Path, publisher: GrowthPublisher, record: dict, service: dict
) -> str:
    if not await publisher.prepare(service):
        _save(state, record, "research_complete")
        return "growth_project_not_created"
    _save(state, record, "create_reserved")
    receipt = await publisher.create(service)
    if receipt is None:
        _save(state, record, "create_uncertain")
        return "growth_create_uncertain"
    record["publication"] = {
        **receipt,
        "deployment": "unavailable:isolation_unavailable",
    }
    _save(state, record, "created")
    _save(state, record, "push_reserved")
    sha = await publisher.push(service)
    if sha is None:
        _save(state, record, "push_uncertain")
        return "growth_push_uncertain"
    record["publication"]["commit_sha"] = sha
    _save(state, record, "prototype_published")
    return "growth_prototype_published"


async def _analyse(
    state: Path, bundle: dict, record: dict, clock: Callable[[], datetime]
) -> dict | None:
    with OperatorLedger(state / "daily-ledger.sqlite") as ledger:
        run = ledger.reserve_analysis(_now(clock), bundle["snapshot_digest"])
        if run is None:
            return None
        _save(state, record, "analysis_reserved")
        try:
            reply = await ask_analyst(bundle)
            proposal, verification = _checked_reply(reply, bundle)
            record_usage(state, _now(clock), reply["usage"])
            record["proposal"] = _summary(proposal, bundle)
            record["verification"] = verification
        except (TimeoutError, OSError):
            _save(state, record, "analysis_uncertain")
            return {"outcome": "growth_analysis_uncertain"}
        except (ValueError, TypeError, EOFError, KeyError):
            ledger.finish_analysis(run, "failed", _now(clock))
            _save(state, record, "analysis_failed")
            return {"outcome": "growth_analysis_unavailable"}
        ledger.finish_analysis(run, "success", _now(clock))
    return proposal


async def run_growth(
    state: Path,
    client: httpx.AsyncClient,
    publisher: GrowthPublisher,
    clock: Callable[[], datetime],
) -> str:
    """Serialize growth with daily reports and preserve uncertain writes across restarts."""
    if daily_report_priority(_now(clock)):
        return "growth_daily_priority"
    with controller_lock(state) as acquired:
        if not acquired:
            return "busy"
        now = _now(clock)
        previous = _previous(state, now)
        if previous is not None and previous["state"] in PENDING:
            return "growth_write_pending"
        async with asyncio.timeout(75):
            bundle = await collect_growth(client, now)
        signal = _signal(bundle)
        if (
            previous is not None
            and previous["state"] in COMPLETE
            and previous["signal_digest"] == signal
        ):
            return "growth_unchanged"
        _history(bundle, previous)
        record = dict.fromkeys(FIELDS)
        record.update(observed_at=now.isoformat(), signal_digest=signal)
        proposal = await _analyse(state, bundle, record, clock)
        if proposal is None or "outcome" in proposal:
            return "growth_analysis_busy" if proposal is None else proposal["outcome"]
        if proposal["service"] is None:
            _save(state, record, "research_complete")
            outcome = "growth_research_complete"
        else:
            outcome = await _publish(state, publisher, record, proposal["service"])
        return outcome


def daily_report_priority(now: datetime) -> bool:
    """Reserve an eight-minute buffer before the daily report's delivery window."""
    local = now.astimezone(ZoneInfo("Europe/Moscow"))
    minute_of_day = local.hour * 60 + local.minute
    return 1192 <= minute_of_day < 1210


async def cli_run() -> str:
    """Read the platform credential only inside the trusted controller process."""
    path = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "platform-api-key"
    with path.open(encoding="ascii") as stream:
        key = stream.read(4097).strip()
    if len(key) > 4096:
        raise ValueError("growth_credential_invalid")
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        return await run_growth(
            STATE, client, GrowthPublisher(client, key), lambda: datetime.now(UTC)
        )


def main() -> None:
    """Run one bounded poll and print only its finite, non-secret outcome."""
    if len(sys.argv) != 1:
        raise SystemExit(2)
    try:
        result = asyncio.run(cli_run())
    except (OSError, ValueError, TypeError, KeyError, TimeoutError):
        result = "growth_controller_unavailable"
    sys.stdout.write(json.dumps({"result": result}) + "\n")


if __name__ == "__main__":
    main()
