"""In-memory stand-in for the backend container CLIs and the signer unit; no Docker, no network."""

import base64
import hashlib
import json
from pathlib import Path

from payout_orchestrator.executor import ExecutorError

WORKER = "00000000-0000-4000-8000-000000000001"
INTENT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
GENESIS = "G" * 43
CONTEXT_KEYS = (
    "intent_id",
    "worker_id",
    "epoch",
    "genesis_hash",
    "last_valid_block_height",
    "format_version",
)


class Crash(BaseException):  # process death: step guards must not catch it
    """Simulated process death at a step boundary."""


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class FakeBackend:
    """Implements the Executor protocol over a tiny model of claims, attempts and confirmations."""

    def __init__(self, handoff: Path) -> None:
        self.handoff = handoff
        self.intents: list[str] = []
        self.claims: dict[str, dict] = {}
        self.attempts: dict[str, dict] = {}
        self.confirmed: set[str] = set()
        self.epoch = 0
        self.calls: list[str] = []
        self.compiles = 0
        self.sign_calls: list[str] = []
        self.signed_after_attempt = 0
        self.sends: list[str] = []
        self.sign_exits: list[int] = []
        self.send_script: list[str] = []
        self.reconcile_script: list[str] = []
        self.commit_script: list[str] = []
        self.height: int | None = None
        self.chain_height = 100
        self.crash_at: int | None = None
        self.crash_after = False
        self.container_files: dict[str, dict] = {}
        self.link_result: dict = {"verdict": "ok", "conflict": 0, "intents": []}
        self.hook = None
        self.lvbh_override: object = None
        self.show_script: list[str] = []
        self.skip_signed = False
        self.order: list[str] = []
        self.stops: list[str] = []
        self.stop_ok = True

    def _step(self, name: str, effect):
        self.calls.append(name)
        index = len(self.calls) - 1
        if self.hook:
            self.hook(name)
        if self.crash_at == index and not self.crash_after:
            raise Crash
        result = effect()
        if self.crash_at == index and self.crash_after:
            raise Crash
        return result

    def expire_all(self) -> None:
        for claim in self.claims.values():
            claim["expired"] = True

    def phase(self, intent: str) -> str:
        claim = self.claims.get(intent)
        if intent in self.confirmed:
            return "confirmed"
        if intent in self.attempts:
            return "prepared"
        if claim and claim["state"] == "claimed":
            return "expired" if claim["expired"] else "claimed"
        return "unclaimed"

    def cli(self, module: str, args: list[str], stdin: str = "") -> dict:
        body = json.loads(stdin) if stdin else {}
        handler = getattr(self, "_" + args[0].replace("-", "_"))
        return self._step(args[0], lambda: handler(args, body))

    def _link(self, args, body):
        return self.link_result

    def _pending(self, args, body):
        limit = int(args[2])
        rows = [{"intent_id": i, "phase": self.phase(i)} for i in self.intents][:limit]
        return {"verdict": "found", "intents": rows}

    def _claim(self, args, body):
        i = body["intent_id"]
        claim = self.claims.get(i)
        if claim and claim["state"] == "claimed" and not claim["expired"]:
            return {"verdict": "unavailable"}
        self.epoch += 1
        self.claims[i] = {
            "worker": body["worker_id"],
            "epoch": self.epoch,
            "state": "claimed",
            "expired": False,
        }
        return {"verdict": "claimed", "epoch": self.epoch, "worker_id": body["worker_id"]}

    def _lease_show(self, args, body):
        claim = self.claims.get(body["intent_id"])
        if not claim:
            return {"verdict": "not_found"}
        return {"verdict": "found", "epoch": claim["epoch"], "worker_id": claim["worker"]}

    def _fenced(self, body) -> dict | None:
        claim = self.claims.get(body["intent_id"])
        live = claim and claim["state"] == "claimed" and claim["epoch"] == body["epoch"]
        return claim if live and claim["worker"] == body["worker_id"] else None

    def _release(self, args, body):
        claim = self._fenced(body)
        if claim is None:
            return {"verdict": "stale"}
        claim["state"] = "free"
        return {"verdict": "released"}

    def _recover(self, args, body):
        claim = self._fenced(body)
        if claim is None or not claim["expired"]:
            return {"verdict": "stale"}
        claim["state"] = "free"
        return {"verdict": "recovered"}

    def _attempt_show(self, args, body):
        if self.show_script:
            return {"verdict": self.show_script.pop(0)}
        attempt = self.attempts.get(body["intent_id"])
        if not attempt:
            return {"verdict": "not_found"}
        return {"verdict": "found", **attempt["public"]}

    def lvbh(self) -> object:
        return str(self.chain_height + 150) if self.lvbh_override is None else self.lvbh_override

    def _auto_compile(self, args, body):
        if self._fenced(body) is None:
            return {"verdict": "stale"}
        self.compiles += 1
        self.container_files[args[2]] = {
            "intent_id": body["intent_id"],
            "worker_id": body["worker_id"],
            "epoch": body["epoch"],
            "genesis_hash": GENESIS,
            "last_valid_block_height": self.lvbh(),
            "recent_blockhash": f"bh{self.compiles}",
            "format_version": 1,
            "unsigned_message_base64": base64.b64encode(b"message").decode(),
            "message_digest": hashlib.sha256(b"message").hexdigest(),
        }
        return {"verdict": "compiled", "epoch": body["epoch"]}

    def _prepare_commit(self, args, body):
        if self.commit_script:
            return {"verdict": self.commit_script.pop(0)}
        if self._fenced(body) is None:
            return {"verdict": "stale"}
        wire = body["signed_transaction_base64"]
        digest = hashlib.sha256(base64.b64decode(wire)).hexdigest()
        public = {"epoch": body["epoch"], "transaction_digest": digest}
        previous = self.attempts.get(body["intent_id"])
        if previous:
            same = previous["wire"] == wire
            return {"verdict": "replayed" if same else "conflict", **public}
        self.attempts[body["intent_id"]] = {"wire": wire, "public": public}
        return {"verdict": "prepared", **public}

    def _auto_send(self, args, body):
        if body["intent_id"] in self.confirmed:
            return {"verdict": "already_confirmed"}
        self.sends.append(self.attempts[body["intent_id"]]["wire"])
        return {"verdict": self.send_script.pop(0) if self.send_script else "sent"}

    def _auto_reconcile(self, args, body):
        verdict = self.reconcile_script.pop(0) if self.reconcile_script else "confirmed"
        if verdict == "confirmed":
            self.confirmed.add(body["intent_id"])
        return {"verdict": verdict}

    def fetch(self, container_path: str, host_path: Path) -> None:
        def effect() -> None:
            if container_path not in self.container_files:
                raise ExecutorError("missing")
            host_path.write_text(json.dumps(self.container_files[container_path]))

        self._step("fetch", effect)

    def drop(self, container_path: str) -> None:
        self._step("drop", lambda: self.container_files.pop(container_path, None))

    def sign(self, instance: str) -> int:
        def effect() -> int:
            self.sign_calls.append(instance)
            self.order.append("sign")
            if instance.rsplit(".", 1)[0] in self.attempts:
                self.signed_after_attempt += 1
            code = self.sign_exits.pop(0) if self.sign_exits else 0
            if code in (0, 10) and not self.skip_signed:
                raw = (self.handoff / f"{instance}.request.json").read_bytes()
                wire = b"wire:" + hashlib.sha256(raw).digest()
                request = json.loads(raw)
                envelope = {k: request[k] for k in CONTEXT_KEYS}
                envelope["signed_transaction_base64"] = base64.b64encode(wire).decode()
                (self.handoff / f"{instance}.signed.json").write_text(json.dumps(envelope))
            return code

        return self._step("sign", effect)

    def stop(self, instance: str) -> bool:
        self.order.append("stop")
        self.stops.append(instance)
        return self.stop_ok

    def block_height(self) -> int | None:
        return self.height
