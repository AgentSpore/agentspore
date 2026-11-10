import fcntl
import hashlib
import json

import pytest

from payout_orchestrator.config import Config
from payout_orchestrator.journal import Journal
from payout_orchestrator.tick import Tick

from .fakes import INTENT, OTHER, WORKER, Crash, FakeBackend, FakeClock


@pytest.fixture
def config(tmp_path):
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    return Config(
        container="backend-placeholder",
        worker_id=WORKER,
        handoff_dir=handoff,
        journal_path=tmp_path / "state" / "journal.json",
        pause_path=tmp_path / "state" / "pause",
        per_tick=5,
        max_resends=2,
    )


@pytest.fixture
def backend(config):
    fake = FakeBackend(config.handoff_dir)
    fake.intents = [INTENT]
    return fake


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def run(config, backend, clock):
    config.journal_path.parent.mkdir(exist_ok=True)

    def go() -> int:
        return Tick(config, backend, Journal(config.journal_path), clock).run()

    return go


def handoff_files(config):
    return sorted(p.name for p in config.handoff_dir.iterdir())


def alerts(capsys):
    return [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]


def test_happy_path_confirms_and_cleans(run, backend, config):
    assert run() == 0
    assert backend.confirmed == {INTENT}
    assert len(backend.sign_calls) == 1
    assert handoff_files(config) == []
    assert not backend.container_files
    assert json.loads(config.journal_path.read_text() or "{}") == {}


def test_pause_skips_everything(run, backend, config):
    config.pause_path.touch()
    assert run() == 0
    assert backend.calls == []


@pytest.fixture
def total_calls(config, clock, tmp_path):
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    probe = FakeBackend(probe_dir)
    probe.intents = [INTENT]
    cfg = Config(
        container="x",
        worker_id=WORKER,
        handoff_dir=probe_dir,
        journal_path=probe_dir / "j" / "journal.json",
        pause_path=probe_dir / "pause",
        per_tick=5,
    )
    cfg.journal_path.parent.mkdir()
    Tick(cfg, probe, Journal(cfg.journal_path), clock).run()
    return len(probe.calls)


@pytest.mark.parametrize("after", [False, True])
@pytest.mark.parametrize("index", range(30))
def test_crash_at_every_boundary_recovers(run, backend, config, clock, total_calls, index, after):
    if index >= total_calls:
        pytest.skip("beyond the last step")
    backend.crash_at, backend.crash_after = index, after
    with pytest.raises(Crash):
        run()
    backend.crash_at = None
    for _ in range(4):
        if INTENT in backend.confirmed:
            break
        run()
        backend.expire_all()
        clock.now += config.lease_ttl_seconds + 1
        backend.height = 10_000
    assert INTENT in backend.confirmed
    assert len(set(backend.sends)) <= 1
    assert backend.signed_after_attempt == 0
    assert handoff_files(config) == []


def test_signer_never_runs_when_attempt_exists(run, backend, config):
    backend.attempts[INTENT] = {"wire": "AAAA", "public": {"epoch": 1, "transaction_digest": "x"}}
    run()
    assert backend.sign_calls == []
    assert backend.compiles == 0


def test_attempt_show_guards_claimed_race(run, backend):
    backend.attempts[INTENT] = {"wire": "AAAA", "public": {"epoch": 1, "transaction_digest": "x"}}
    backend.phase = lambda intent: "unclaimed"
    run()
    assert backend.sign_calls == []
    assert backend.compiles == 0


def test_unresolved_never_compiles_and_resends_same_wire(run, backend, capsys):
    run_once = backend
    backend.send_script = ["unresolved"] * 5
    backend.reconcile_script = ["unresolved"] * 5
    run()
    assert run_once.compiles == 1
    assert len(backend.sends) == 3  # 1 + max_resends
    assert len(set(backend.sends)) == 1
    assert backend.sign_calls == [backend.sign_calls[0]]
    assert INTENT not in backend.confirmed
    run()
    assert backend.compiles == 1
    assert len(set(backend.sends)) == 1


def test_resend_after_preflight_rejected_then_confirmed(run, backend):
    backend.send_script = ["preflight_rejected", "sent"]
    backend.reconcile_script = ["unresolved", "confirmed"]
    run()
    assert INTENT in backend.confirmed
    assert len(backend.sends) == 2
    assert len(set(backend.sends)) == 1


@pytest.mark.parametrize("code", [0, 10])
def test_signer_success_codes_commit(run, backend, code):
    backend.sign_exits = [code]
    run()
    assert INTENT in backend.attempts


def test_signer_paused_releases_and_still_delivers(run, backend, capsys):
    backend.intents = [OTHER, INTENT]
    backend.attempts[OTHER] = {"wire": "AAAA", "public": {"epoch": 1, "transaction_digest": "x"}}
    backend.sign_exits = [11]
    backend.intents = [INTENT, OTHER]
    run()
    assert backend.claims[INTENT]["state"] == "free"
    assert INTENT not in backend.attempts
    assert OTHER in backend.confirmed
    assert len(backend.sign_calls) == 1


def test_signer_rejected_alerts_and_releases(run, backend, capsys):
    backend.sign_exits = [12]
    run()
    assert backend.claims[INTENT]["state"] == "free"
    assert INTENT not in backend.attempts
    assert alerts(capsys) == [{"alert": "signer_rejected", "intent_id": INTENT}]


def test_signer_limit_stops_signing_for_run(run, backend):
    backend.intents = [INTENT, OTHER]
    backend.sign_exits = [13]
    run()
    assert len(backend.sign_calls) == 1
    assert backend.claims[INTENT]["state"] == "free"
    assert OTHER not in backend.attempts


@pytest.mark.parametrize("code", [14, 99])
def test_signer_config_error_aborts_run(run, backend, code):
    backend.sign_exits = [code]
    assert run() != 0


def test_commit_conflict_holds_and_alerts(run, backend, capsys):
    backend.commit_script = ["conflict"]
    run()
    assert alerts(capsys) == [{"alert": "commit_conflict", "intent_id": INTENT}]
    assert backend.sends == []


def test_stale_waits_for_lvbh_by_height(run, backend):
    backend.commit_script = ["stale"]
    backend.height = 100
    run()
    assert backend.compiles == 1
    run()
    assert backend.compiles == 1  # lvbh (250) not passed
    assert len(backend.sign_calls) == 1
    backend.height = 251
    run()
    assert backend.compiles == 2
    assert INTENT in backend.confirmed
    assert backend.sign_calls[0] != backend.sign_calls[1]


def test_unknown_height_holds_and_never_recompiles(run, backend, clock, config, capsys):
    backend.commit_script = ["stale"]
    run()
    capsys.readouterr()
    clock.now += 10 * config.lease_ttl_seconds
    backend.height = None
    run()
    run()
    assert backend.compiles == 1
    assert len(backend.sign_calls) == 1
    assert {"alert": "lvbh_unproven", "intent_id": INTENT} in alerts(capsys)


def test_claimed_by_other_worker_is_skipped(run, backend):
    backend.claims[INTENT] = {"worker": OTHER, "epoch": 7, "state": "claimed", "expired": False}
    run()
    assert backend.compiles == 0


def test_expired_is_recovered_then_claimed(run, backend):
    backend.claims[INTENT] = {"worker": OTHER, "epoch": 7, "state": "claimed", "expired": True}
    run()
    assert INTENT in backend.confirmed
    assert backend.calls.index("recover") < backend.calls.index("claim")


def test_files_kept_on_digest_mismatch(run, backend, config, capsys):
    real = backend._prepare_commit

    def wrong(args, body):
        out = real(args, body)
        out["transaction_digest"] = hashlib.sha256(b"other").hexdigest()
        return out

    backend._prepare_commit = wrong
    run()
    assert handoff_files(config)
    assert {"alert": "digest_mismatch", "intent_id": INTENT} in alerts(capsys)


def test_concurrent_tick_is_refused_by_flock(run, backend, config):
    config.journal_path.parent.mkdir(exist_ok=True)
    with open(config.journal_path.with_suffix(".lock"), "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert run() == 0
    assert backend.calls == []


def test_lock_is_held_during_tick(run, backend, config):
    seen = []

    def probe(name):
        with open(config.journal_path.with_suffix(".lock"), "w") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append("free")
            except BlockingIOError:
                seen.append("held")

    backend.hook = probe
    run()
    assert set(seen) == {"held"}


def test_alerts_and_journal_hold_no_secrets(run, backend, config, capsys):
    backend.sign_exits = [12]
    run()
    err = capsys.readouterr().err
    assert "wire" not in err and "bh1" not in err
    backend.sign_exits = []
    backend.commit_script = ["stale"]
    run()
    journal = json.loads(config.journal_path.read_text())
    assert set(journal[INTENT]) == {"epoch", "lvbh", "at"}


def test_per_tick_bound(run, backend, config):
    backend.intents = [f"{n:08d}-1111-4111-8111-111111111111" for n in range(9)]
    run()
    assert len(backend.confirmed) == config.per_tick
