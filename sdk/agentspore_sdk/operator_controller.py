"""Trusted one-shot operator orchestration; no general action dispatcher."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import pwd
import socket
import stat
import struct
import sys
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .operator_digest import (
    canonical,
    evidence_bundle,
    parse_analysis,
    render_digest,
    unique_object,
)
from .operator_ledger import OperatorLedger
from .operator_publisher import OperatorPublisher
from .operator_snapshot import HEALTH, PublicCollector, collect_snapshot

STATE = Path("/var/lib/agentspore-operator-broker")
ANALYST_SOCKET = "/run/agentspore-operator/analyst.sock"
PEER_OPTION = getattr(socket, "SO_PEERCRED", None)
USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
}


def private_file(path: Path, flags: int) -> int:
    """Open only owned private regular state files, rejecting symlinks."""
    descriptor = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        os.close(descriptor)
        raise PermissionError("unsafe operator state file")
    return descriptor


@contextmanager
def controller_lock(state: Path) -> Generator[bool]:
    """Serialize both operator modes through the same private OS lock."""
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = state.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PermissionError("unsafe operator state directory")
    descriptor = private_file(state / "controller.lock", os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(descriptor)


def write_state(path: Path, value: dict) -> None:
    """Durably replace bounded non-secret JSON without following a destination link."""
    content = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    if len(content) > 4096:
        raise ValueError("operator state too large")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def health_streaks(state: Path, now: datetime) -> dict:
    """Read only a bounded previous health state; malformed state fails closed."""
    try:
        descriptor = private_file(state / "health.json", os.O_RDONLY)
    except FileNotFoundError:
        return dict.fromkeys(HEALTH, 0)
    with os.fdopen(descriptor, "rb") as stream:
        content = stream.read(4097)
    if len(content) > 4096:
        raise ValueError("operator state too large")
    previous = json.loads(content)
    if not isinstance(previous, dict) or set(previous) != {
        "observed_at",
        "streaks",
        "statuses",
    }:
        raise ValueError("invalid health state")
    stamp = datetime.fromisoformat(previous["observed_at"])
    if stamp.tzinfo is None or stamp > now:
        raise ValueError("invalid health clock")
    streaks = previous["streaks"]
    if not isinstance(streaks, dict) or set(streaks) != set(HEALTH):
        raise ValueError("invalid health streaks")
    if any(type(value) is not int or not 0 <= value <= 2 for value in streaks.values()):
        raise ValueError("invalid health streak")
    return streaks


class OperatorController:
    """Keep the process lock and trusted publisher outside native analyst jobs."""

    def __init__(
        self,
        state: Path,
        client: httpx.AsyncClient,
        publisher: OperatorPublisher,
        clock: Callable[[], datetime],
    ):
        """Inject the HTTP boundary and clock; never pass the publisher to analysis."""
        self.state = state
        self.client = client
        self.publisher = publisher
        self.clock = clock

    def now(self) -> datetime:
        """Reject naive clocks before recording or reserving any operation."""
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("operator clock must be aware")
        return now.astimezone(UTC)

    async def run(self, mode: str) -> str:
        """Run one finite mode under a shared nonblocking process lock."""
        if mode not in ("daily", "health"):
            raise ValueError("unknown operator mode")
        with controller_lock(self.state) as acquired:
            if not acquired:
                return "busy"
            return await self._health() if mode == "health" else await self._daily()

    async def _health(self) -> str:
        """Confirm unsuccessful point observations without model invocation."""
        now = self.now()
        previous = health_streaks(self.state, now)
        async with asyncio.timeout(75):
            statuses = await PublicCollector(self.client).health()
        streaks = {
            origin: 0
            if status is not None and 200 <= status < 300
            else min(previous[origin] + 1, 2)
            for origin, status in statuses.items()
        }
        value = {
            "observed_at": now.isoformat(),
            "statuses": statuses,
            "streaks": streaks,
        }
        write_state(self.state / "health.json", value)
        affected = [origin for origin in HEALTH if streaks[origin] == 2]
        if not affected:
            return "health_recorded"
        text = (
            f"AgentSpore health check at {now.isoformat()}. "
            "Two consecutive checks were unsuccessful.\n"
        )
        text += "\n".join(
            origin
            + ": "
            + (
                f"HTTP {statuses[origin]}"
                if statuses[origin] is not None
                else "check unavailable; connectivity unknown"
            )
            for origin in affected
        )
        text += "\nThese are point-in-time observations; the cause is not established."
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        with OperatorLedger(self.state / "incident-ledger.sqlite") as ledger:
            return await self._deliver(
                ledger,
                "incident_chat",
                {"message_type": "text", "content": text},
                digest,
            )

    async def _daily(self) -> str:
        """Gate collection, analysis and publication to the trusted daily window."""
        now = self.now()
        if not daily_window(now):
            return "outside_window"
        async with asyncio.timeout(75):
            snapshot = dict(await collect_snapshot(self.client, now))
        bundle = evidence_bundle(snapshot)
        with OperatorLedger(self.state / "daily-ledger.sqlite") as ledger:
            if not daily_window(self.now()):
                return "outside_window"
            run = ledger.reserve_analysis(self.now(), bundle["snapshot_digest"])
            if run is None:
                return "analysis_quota"
            try:
                reply = checked_reply(await ask_analyst(bundle))
                record_usage(self.state, self.now(), reply["usage"])
                analysis = parse_analysis(reply["analysis"], bundle)
                payloads = render_digest(snapshot, analysis)
            except (TimeoutError, OSError):
                return "analysis_uncertain"
            except (ValueError, TypeError, EOFError, KeyError):
                ledger.finish_analysis(run, "failed", self.now())
                return "analysis_unavailable"
            ledger.finish_analysis(run, "success", self.now())
            outcomes = []
            for channel in ("chat", "blog"):
                reserved_at = self.now()
                if not daily_window(reserved_at):
                    return "outside_window"
                outcome = await self._deliver(
                    ledger,
                    "daily_" + channel,
                    payloads[channel],
                    bundle["snapshot_digest"],
                )
                if outcome == "outside_window":
                    return outcome
                outcomes.append(outcome)
        if "uncertain" in outcomes:
            return "daily_uncertain"
        return "daily_complete" if outcomes == ["sent", "sent"] else "daily_reserved"

    async def _deliver(
        self, ledger: OperatorLedger, operation: str, payload: dict, digest: str
    ) -> str:
        """Reserve durably before POST and retain the original reservation clock."""
        if operation not in ("daily_chat", "daily_blog", "incident_chat"):
            raise ValueError("unknown publication operation")
        reserved_at = self.now()
        if operation != "incident_chat" and not daily_window(reserved_at):
            return "outside_window"
        channel = "blog" if operation == "daily_blog" else "chat"
        if not ledger.reserve_publication(reserved_at, channel, digest):
            return "already_reserved"
        receipt = await self.publisher.publish(channel, payload)
        ledger.finish_publication(reserved_at, channel, digest, receipt)
        return "sent" if receipt is not None else "uncertain"


def daily_window(now: datetime) -> bool:
    """Allow only the current day's 20:00–20:10 Moscow publication window."""
    local = now.astimezone(ZoneInfo("Europe/Moscow"))
    return local.hour == 20 and local.minute < 10


def checked_reply(reply) -> dict:
    """Validate the analyst transport result without accepting any operation."""
    if not isinstance(reply, dict) or set(reply) != {"analysis", "usage"}:
        raise ValueError("invalid analyst reply")
    if (
        not isinstance(reply["analysis"], str)
        or len(reply["analysis"].encode()) > 65536
    ):
        raise ValueError("invalid analyst output")
    checked_usage(reply["usage"], 10**12)
    return reply


def record_usage(state: Path, now: datetime, usage: dict) -> None:
    """Persist aggregate token counters only, never model messages or prompts."""
    path = state / "model-usage.json"
    previous: dict = {"utc_day": now.date().isoformat(), "runs": 0, "usage": {}}
    try:
        descriptor = private_file(path, os.O_RDONLY)
    except FileNotFoundError:
        descriptor = None
    if descriptor is not None:
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("invalid usage state")
        saved = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(saved, dict) or set(saved) != set(previous):
            raise ValueError("invalid usage state")
        checked_usage(saved["usage"], 4 * 10**12)
        if type(saved["runs"]) is not int or not 0 <= saved["runs"] <= 4:
            raise ValueError("invalid usage count")
        if saved["utc_day"] == previous["utc_day"]:
            previous = saved
    previous["runs"] += 1
    for name, count in usage.items():
        previous["usage"][name] = previous["usage"].get(name, 0) + count
    write_state(path, previous)


def check_analyst_peer(writer: asyncio.StreamWriter) -> None:
    """Verify the protected systemd listener and its PID1 root peer."""
    if PEER_OPTION is None:
        raise PermissionError("analyst peer verification unsupported")
    parent = Path(ANALYST_SOCKET).parent.lstat()
    endpoint = Path(ANALYST_SOCKET).lstat()
    group = pwd.getpwnam("agentspore-operator").pw_gid
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != group
        or stat.S_IMODE(parent.st_mode) != 0o750
    ):
        raise PermissionError("unsafe analyst socket directory")
    if (
        not stat.S_ISSOCK(endpoint.st_mode)
        or endpoint.st_uid != os.getuid()
        or stat.S_IMODE(endpoint.st_mode) != 0o600
    ):
        raise PermissionError("unsafe analyst socket")
    peer = writer.get_extra_info("socket")
    pid, uid, _gid = struct.unpack(
        "3i", peer.getsockopt(socket.SOL_SOCKET, PEER_OPTION, 12)
    )
    if uid != 0 or pid != 1:
        raise PermissionError("unexpected socket activation peer")


async def ask_analyst(bundle: dict) -> dict:
    """Exchange one bounded framed public job, with no credential or environment."""
    content = canonical(bundle)
    if not content or len(content) > 65536:
        raise ValueError("analyst job too large")
    writer = None
    try:
        async with asyncio.timeout(129):
            reader, writer = await asyncio.open_unix_connection(ANALYST_SOCKET)
            check_analyst_peer(writer)
            writer.write(struct.pack("!I", len(content)) + content)
            await writer.drain()
            length = struct.unpack("!I", await reader.readexactly(4))[0]
            if not 0 < length <= 65536:
                raise ValueError("analyst response too large")
            return json.loads(
                await reader.readexactly(length), object_pairs_hook=unique_object
            )
    finally:
        if writer is not None:
            writer.close()
            async with asyncio.timeout(1):
                await writer.wait_closed()


async def cli_run(mode: str) -> str:
    """Read a systemd credential only in the trusted controller process."""
    path = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "platform-api-key"
    with path.open(encoding="ascii") as stream:
        key = stream.read(4097).strip()
    if len(key) > 4096:
        raise ValueError("invalid controller credential")
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        controller = OperatorController(
            STATE, client, OperatorPublisher(client, key), lambda: datetime.now(UTC)
        )
        return await controller.run(mode)


def main() -> None:
    """Expose fixed daily/health modes only; print no exception or secret bodies."""
    parser = argparse.ArgumentParser(description="Bounded AgentSpore operator")
    parser.add_argument("mode", choices=("daily", "health"))
    mode = parser.parse_args().mode
    try:
        result = asyncio.run(cli_run(mode))
    except (OSError, ValueError, TypeError, KeyError, TimeoutError):
        result = "controller_unavailable"
    sys.stdout.write(json.dumps({"result": result}) + "\n")


def checked_usage(usage, maximum: int) -> None:
    """Validate finite token aggregates with a bound appropriate to their scope."""
    if (
        not isinstance(usage, dict)
        or not {"input_tokens", "cached_input_tokens", "output_tokens"}.issubset(usage)
        or not set(usage).issubset(USAGE_FIELDS)
    ):
        raise ValueError("invalid analyst usage")
    if any(
        type(value) is not int or not 0 <= value <= maximum for value in usage.values()
    ):
        raise ValueError("invalid analyst usage")


if __name__ == "__main__":
    main()
