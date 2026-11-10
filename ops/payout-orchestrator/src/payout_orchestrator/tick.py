"""One payout tick. Only `auto-send` broadcasts, and only the stored wire.

Invariants:
- a compile happens only after `attempt-show` says not_found and after the previous unpersisted
  request's last_valid_block_height has passed (journal gate);
- the signer runs only on a request file this orchestrator fetched and journaled;
- resends reuse the stored wire through `auto-send`; nothing is ever re-signed.
Branching is on JSON `verdict` fields; see executor.py for why exit codes are not shared.
"""

import base64
import fcntl
import hashlib
import json
import re
import sys
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from .config import Config
from .executor import Executor, ExecutorError
from .journal import Journal

DELIVERY = "payout_delivery"
CONFIRMED = {"confirmed", "replayed"}
RETRYABLE = {"unresolved", "preflight_rejected"}
DELIVERED = {"sent", "already_processed"}
RAW_UNITS = re.compile(r"(0|[1-9][0-9]{0,19})")


class RunAborted(Exception):
    """Signer configuration failure or an unknown signer status: stop the whole run."""


def read_lvbh(request: Path) -> int | None:
    """The request carries last_valid_block_height as a canonical decimal string; else None."""
    try:
        value = json.loads(request.read_text()).get("last_valid_block_height")
    except (ValueError, AttributeError):
        return None
    return int(value) if isinstance(value, str) and RAW_UNITS.fullmatch(value) else None


@contextmanager
def exclusive(path: Path) -> Generator[bool]:
    """Hold a non-blocking flock for the tick; yields False when another tick owns it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


class Tick:
    """One pass over pending intents; holds only per-run state, durable state is the journal."""

    def __init__(
        self,
        config: Config,
        executor: Executor,
        journal: Journal,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = config
        self.ex = executor
        self.journal = journal
        self.clock = clock
        self.signing_stopped = False
        self.touched: set[str] = set()

    def alert(self, reason: str, intent: str | None, error: str | None = None) -> None:
        """One JSON line: fixed reason, intent id and optional exception class name only."""
        body = {"alert": reason, "intent_id": intent} | ({"error": error} if error else {})
        sys.stderr.write(json.dumps(body) + "\n")

    def hold(self, intent: str, reason: str) -> bool:
        """Journal a hold that every later tick respects until an operator clears it."""
        self.journal.hold(intent, reason, self.clock())
        self.alert(reason, intent)
        return False

    def held(self, intent: str) -> bool:
        entry = self.journal.get(intent)
        return bool(entry and entry.get("hold"))

    def run(self) -> int:
        if self.cfg.pause_path.exists():
            return 0
        with exclusive(self.cfg.lock_path) as owned:
            if not owned:
                return 0
            try:
                self._run_locked()
            except RunAborted:
                return 3
        return 0

    def _run_locked(self) -> None:
        try:
            link = self.ex.cli("welcome_payouts", ["link", "--apply"])
            if link["verdict"] != "ok" or link.get("conflict"):
                self.alert(
                    f"link_{link['verdict']}" if link["verdict"] != "ok" else "link_conflict", None
                )
        except ExecutorError:
            self.alert("link_failed", None)
        try:
            limit = min(self.cfg.per_tick * 4, 1000)  # held rows must not starve the rest
            pending = self.ex.cli(DELIVERY, ["pending", "--limit", str(limit)])
        except ExecutorError:
            return self.alert("pending_failed", None)
        if pending["verdict"] != "found":
            return self.alert(f"pending_{pending['verdict']}", None)
        for item in pending["intents"]:
            if len(self.touched) >= self.cfg.per_tick:
                break
            self._guarded(item)

    def _guarded(self, item: dict) -> None:
        if self.cfg.pause_path.exists():
            return
        try:
            intent = str(uuid.UUID(item["intent_id"]))
        except (ValueError, KeyError, TypeError, AttributeError):
            return self.alert("bad_intent_id", None)
        try:
            self.handle(intent, item.get("phase"))
        except RunAborted:
            raise
        except ExecutorError:
            self.alert("step_failed", intent)
        except Exception as exc:  # noqa: BLE001 (one bad intent must not stop the others)
            self.alert("step_error", intent, type(exc).__name__)

    def call(self, args: list[str], body: dict | None = None) -> dict:
        return self.ex.cli(DELIVERY, args, json.dumps(body) if body is not None else "")

    def fence(self, intent: str, epoch: int) -> dict:
        return {"intent_id": intent, "worker_id": self.cfg.worker_id, "epoch": epoch}

    def handle(self, intent: str, phase: str | None) -> None:
        if phase == "confirmed":
            return
        if self.held(intent):
            return self.alert("held", intent)
        if phase == "prepared":
            self.touched.add(intent)
            self.settle(intent, self.call(["attempt-show"], {"intent_id": intent}))
            return None if self.held(intent) else self.deliver(intent)
        if phase == "claimed":
            epoch = self.resume_epoch(intent)
        elif phase in ("unclaimed", "expired"):
            epoch = self.take(intent, expired=phase == "expired")
        else:
            return self.alert("unknown_phase", intent)
        if epoch is not None:
            self.touched.add(intent)
            if self.sign_and_commit(intent, epoch):
                self.deliver(intent)

    def resume_epoch(self, intent: str) -> int | None:
        lease = self.call(["lease-show"], {"intent_id": intent})
        if lease["verdict"] == "found" and lease.get("worker_id") == self.cfg.worker_id:
            return lease["epoch"]
        return None

    def take(self, intent: str, expired: bool) -> int | None:
        if not self.gate_open(intent, None) or self.signing_stopped:
            return None
        if expired:
            lease = self.call(["lease-show"], {"intent_id": intent})
            if lease["verdict"] != "found":
                return None
            recovered = self.call(
                ["recover"], self.fence(intent, lease["epoch"]) | {"worker_id": lease["worker_id"]}
            )
            if recovered["verdict"] != "recovered":
                return None
        claim = {
            "intent_id": intent,
            "worker_id": self.cfg.worker_id,
            "ttl_seconds": self.cfg.lease_ttl_seconds,
        }
        claimed = self.call(["claim"], claim)
        return claimed["epoch"] if claimed["verdict"] == "claimed" else None

    def release(self, intent: str, epoch: int) -> None:
        if self.call(["release"], self.fence(intent, epoch))["verdict"] != "released":
            self.alert("release_failed", intent)

    def gate_open(self, intent: str, epoch: int | None) -> bool:
        """True only when chain height proves the earlier request expired; unknown means held."""
        entry = self.journal.get(intent)
        if entry is None or entry["epoch"] == epoch:
            return True
        height = self.ex.block_height()
        if height is None:
            self.alert("lvbh_unproven", intent)
            return False
        return height > entry["lvbh"]

    def paths(self, intent: str, epoch: int) -> tuple[Path, Path, str]:
        stem = f"{intent}.{epoch}"
        return (
            self.cfg.handoff_dir / f"{stem}.request.json",
            self.cfg.handoff_dir / f"{stem}.signed.json",
            f"{self.cfg.container_request_dir}/{stem}.json",
        )

    def sign_and_commit(self, intent: str, epoch: int) -> bool:
        """Return True once an attempt exists for the intent."""
        shown = self.call(["attempt-show"], {"intent_id": intent})
        if shown["verdict"] == "found":
            self.settle(intent, shown)
            return True
        if shown["verdict"] != "not_found":
            self.alert(f"attempt_show_{shown['verdict']}", intent)
            return False
        request, signed, remote = self.paths(intent, epoch)
        no_signing = self.signing_stopped or self.cfg.pause_path.exists()
        if not self.gate_open(intent, epoch) or (no_signing and not signed.exists()):
            self.release(intent, epoch)
            return False
        if not self.stage_request(intent, epoch, request, remote):
            return False
        if not signed.exists() and not self.sign(intent, epoch):
            return False
        return self.commit(intent, epoch, signed)

    def stage_request(self, intent: str, epoch: int, request: Path, remote: str) -> bool:
        """Have a request file and journal its lvbh (never lowering it) before any signing."""
        if not request.exists() and not self.compile(intent, epoch, request, remote):
            return False
        lvbh = read_lvbh(request)
        if lvbh is None:
            return self.hold(intent, "request_malformed")
        entry = self.journal.get(intent)
        seen = entry["lvbh"] if entry and entry["epoch"] == epoch else 0
        self.journal.put(intent, epoch, max(seen, lvbh), self.clock())
        return True

    def compile(self, intent: str, epoch: int, request: Path, remote: str) -> bool:
        self.ex.drop(remote)
        verdict = self.call(["auto-compile", "--output", remote], self.fence(intent, epoch))[
            "verdict"
        ]
        if verdict != "compiled":
            self.alert(f"compile_{verdict}", intent)
            self.release(intent, epoch)
            return False
        self.ex.fetch(remote, request)
        self.ex.drop(remote)
        return True

    def sign(self, intent: str, epoch: int) -> bool:
        code = self.ex.sign(f"{intent}.{epoch}")
        if code in (0, 10):
            if self.paths(intent, epoch)[1].exists():
                return True
            return self.hold(intent, "signer_no_output")
        if code == 12:
            return self.hold(intent, "signer_rejected")  # claim stays; take() refuses the intent
        if code in (11, 13):
            self.signing_stopped = True
            if code == 13:
                self.alert("signer_limit_exceeded", intent)
            self.release(intent, epoch)
            return False
        self.alert("signer_config_error" if code == 14 else "signer_unexpected_status", intent)
        raise RunAborted

    def commit(self, intent: str, epoch: int, signed: Path) -> bool:
        text = signed.read_text()
        verdict = self.ex.cli(DELIVERY, ["prepare-commit"], text)
        if verdict["verdict"] in ("prepared", "replayed"):
            return self.settle(intent, verdict)
        if verdict["verdict"] == "stale":
            self.release(intent, epoch)  # journal keeps the lvbh gate for the recompile
        else:
            self.alert(f"commit_{verdict['verdict']}", intent)
        return False

    def settle(self, intent: str, stored: dict) -> bool:
        """Remove the stored epoch's handoff files and journal entry once the digest matches.

        A mismatch is a persistent hold. The signed file goes last, so a crash
        mid-cleanup still leaves the evidence to re-check.
        """
        epoch = stored.get("epoch")
        if stored.get("verdict") not in ("prepared", "replayed", "found") or epoch is None:
            return False
        signed = self.paths(intent, epoch)[1]
        if signed.exists():
            wire = base64.b64decode(json.loads(signed.read_text())["signed_transaction_base64"])
            if hashlib.sha256(wire).hexdigest() != stored.get("transaction_digest"):
                return self.hold(intent, "digest_mismatch")
        for leftover in sorted(self.cfg.handoff_dir.glob(f"{intent}.{epoch}.*")):
            leftover.unlink(missing_ok=True)
        self.journal.drop(intent)
        return True

    def deliver(self, intent: str) -> None:
        """Send the stored wire, reconcile, and resend the same wire a bounded number of times."""
        body = {"intent_id": intent}
        for _ in range(1 + self.cfg.max_resends):
            sent = self.call(["auto-send"], body)["verdict"]
            if sent == "already_confirmed":
                return
            if sent not in DELIVERED | RETRYABLE:
                return self.alert(f"send_{sent}", intent)
            reconciled = self.call(["auto-reconcile"], body)["verdict"]
            if reconciled in CONFIRMED:
                return
            if reconciled != "unresolved":
                self.hold(intent, f"reconcile_{reconciled}")
                return
            if sent in DELIVERED:
                return
        self.alert("resend_exhausted", intent)
