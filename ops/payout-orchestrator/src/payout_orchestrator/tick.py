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


class RunAborted(Exception):
    """Signer configuration failure or an unknown signer status: stop the whole run."""


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

    def alert(self, reason: str, intent: str | None) -> None:
        """One JSON line: fixed reason and intent id only, never amounts, wallets or endpoints."""
        sys.stderr.write(json.dumps({"alert": reason, "intent_id": intent}) + "\n")

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
            pending = self.ex.cli(DELIVERY, ["pending", "--limit", str(self.cfg.per_tick)])
        except ExecutorError:
            return self.alert("pending_failed", None)
        if pending["verdict"] != "found":
            return self.alert(f"pending_{pending['verdict']}", None)
        for item in pending["intents"][: self.cfg.per_tick]:
            self._guarded(item["intent_id"], item["phase"])

    def _guarded(self, intent: str, phase: str) -> None:
        try:
            intent = str(uuid.UUID(intent))
            self.handle(intent, phase)
        except ValueError:
            self.alert("bad_intent_id", None)
        except ExecutorError:
            self.alert("step_failed", intent)

    def call(self, args: list[str], body: dict | None = None) -> dict:
        return self.ex.cli(DELIVERY, args, json.dumps(body) if body is not None else "")

    def fence(self, intent: str, epoch: int) -> dict:
        return {"intent_id": intent, "worker_id": self.cfg.worker_id, "epoch": epoch}

    def handle(self, intent: str, phase: str) -> None:
        if phase == "confirmed":
            return
        if phase == "prepared":
            self.settle(intent, self.call(["attempt-show"], {"intent_id": intent}))
            return self.deliver(intent)
        if phase == "claimed":
            epoch = self.resume_epoch(intent)
        elif phase in ("unclaimed", "expired"):
            epoch = self.take(intent, expired=phase == "expired")
        else:
            return self.alert("unknown_phase", intent)
        if epoch is not None and self.sign_and_commit(intent, epoch):
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
        if not self.gate_open(intent, epoch) or (self.signing_stopped and not signed.exists()):
            self.release(intent, epoch)
            return False
        if not request.exists() and not self.compile(intent, epoch, request, remote):
            return False
        entry = self.journal.get(intent)
        if entry is None or entry["epoch"] != epoch:
            lvbh = json.loads(request.read_text())["last_valid_block_height"]
            self.journal.put(intent, epoch, lvbh, self.clock())
        if not signed.exists() and not self.sign(intent, epoch):
            return False
        return self.commit(intent, epoch, signed)

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
            return True
        if code in (11, 12, 13):
            self.signing_stopped = code != 12
            if code != 11:
                self.alert("signer_rejected" if code == 12 else "signer_limit_exceeded", intent)
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
        """Remove handoff files and the journal entry once the stored digest matches the file.

        The signed file goes last, so a crash mid-cleanup still leaves the evidence to re-check.
        """
        epoch = stored.get("epoch")
        if stored.get("verdict") not in ("prepared", "replayed", "found") or epoch is None:
            return False
        signed = self.paths(intent, epoch)[1]
        if signed.exists():
            wire = base64.b64decode(json.loads(signed.read_text())["signed_transaction_base64"])
            if hashlib.sha256(wire).hexdigest() != stored.get("transaction_digest"):
                self.alert("digest_mismatch", intent)
                return False
        for leftover in sorted(self.cfg.handoff_dir.glob(f"{intent}.*")):
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
            if reconciled in CONFIRMED or sent in DELIVERED:
                return
        self.alert("resend_exhausted", intent)
