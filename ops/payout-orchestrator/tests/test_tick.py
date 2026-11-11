import contextlib
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


def stale_then_recompile(b):
    b.commit_script, b.height = ["stale"], 10_000


def expired_foreign_claim(b):
    b.claims[INTENT] = {"worker": OTHER, "epoch": 7, "state": "claimed", "expired": True}


def resend_after_unresolved(b):
    b.send_script, b.reconcile_script = ["unresolved", "sent"], ["unresolved", "confirmed"]


SCENARIOS = {
    "happy": lambda b: None,
    "stale": stale_then_recompile,
    "expired": expired_foreign_claim,
    "resend": resend_after_unresolved,
}


@pytest.fixture(params=SCENARIOS)
def scenario(request, backend):
    SCENARIOS[request.param](backend)
    return request.param


@pytest.fixture
def total_calls(scenario, clock, tmp_path):
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    probe = FakeBackend(probe_dir)
    probe.intents = [INTENT]
    SCENARIOS[scenario](probe)
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
    stored = backend.attempts[INTENT]["public"]["epoch"]
    assert [n for n in handoff_files(config) if n.split(".")[1] == str(stored)] == []


@pytest.mark.parametrize("phase_crash", ["recover", "claim", "prepare-commit", "auto-send"])
def test_crash_on_named_step_recovers(run, backend, config, clock, scenario, phase_crash):
    if phase_crash not in {"claim", "auto-send", "prepare-commit"} and scenario != "expired":
        pytest.skip("step not reached in this scenario")
    backend.hook = lambda name: (_ for _ in ()).throw(Crash) if name == phase_crash else None
    with contextlib.suppress(Crash):
        run()
    backend.hook = None
    for _ in range(4):
        if INTENT in backend.confirmed:
            break
        run()
        backend.expire_all()
        clock.now += config.lease_ttl_seconds + 1
        backend.height = 10_000
    assert INTENT in backend.confirmed
    assert backend.signed_after_attempt == 0


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


def test_signer_rejected_holds_and_is_never_retaken(run, backend, clock, config, capsys):
    backend.sign_exits = [12]
    run()
    assert backend.claims[INTENT]["state"] == "claimed"  # not released
    assert json.loads(config.journal_path.read_text())[INTENT]["hold"] == "signer_rejected"
    assert alerts(capsys) == [{"alert": "signer_rejected", "intent_id": INTENT}]
    for _ in range(3):
        backend.expire_all()
        backend.height = 10_000
        clock.now += config.lease_ttl_seconds + 1
        run()
    assert backend.compiles == 1
    assert len(backend.sign_calls) == 1
    assert {"alert": "held", "intent_id": INTENT} in alerts(capsys)


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


def test_commit_conflict_holds_and_is_never_recompiled(run, backend, config, clock, capsys):
    backend.commit_script = ["conflict"]
    run()
    assert alerts(capsys) == [{"alert": "commit_conflict", "intent_id": INTENT}]
    assert backend.sends == []
    assert json.loads(config.journal_path.read_text())[INTENT]["hold"] == "commit_conflict"
    backend.expire_all()
    backend.height = 10_000
    clock.now += config.lease_ttl_seconds + 1
    run()
    assert backend.compiles == 1
    assert len(backend.sign_calls) == 1


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
    backend.commit_script = ["stale"]
    run()
    journal = json.loads(config.journal_path.read_text())
    assert set(journal[INTENT]) == {"epoch", "lvbh", "at"}
    assert isinstance(journal[INTENT]["lvbh"], int)
    err = capsys.readouterr().err
    assert "wire" not in err and "bh1" not in err


def test_per_tick_bound(run, backend, config):
    backend.intents = [f"{n:08d}-1111-4111-8111-111111111111" for n in range(9)]
    run()
    assert len(backend.confirmed) == config.per_tick


def test_attempt_show_busy_never_compiles(run, backend, capsys):
    backend.show_script = ["busy"]
    run()
    assert backend.compiles == 0
    assert backend.sign_calls == []
    assert {"alert": "attempt_show_busy", "intent_id": INTENT} in alerts(capsys)


def test_sent_then_unresolved_sends_exactly_once(run, backend):
    backend.send_script = ["sent"]
    backend.reconcile_script = ["unresolved"]
    run()
    assert len(backend.sends) == 1
    assert INTENT not in backend.confirmed


def test_height_equal_to_lvbh_is_still_held(run, backend):
    backend.commit_script = ["stale"]
    backend.height = 250
    run()
    run()
    assert backend.compiles == 1


@pytest.mark.parametrize("sent", ["sent", "unresolved"])
@pytest.mark.parametrize("verdict", ["failed", "conflict", "unsupported"])
def test_reconcile_verdict_alerts_and_stops_resending(run, backend, capsys, sent, verdict):
    backend.send_script = [sent if sent != "unresolved" else "preflight_rejected"] * 5
    backend.reconcile_script = [verdict] * 5
    run()
    assert alerts(capsys) == [{"alert": f"reconcile_{verdict}", "intent_id": INTENT}]
    assert len(backend.sends) == 1
    run()
    assert len(backend.sends) == 1
    assert {"alert": "held", "intent_id": INTENT} in alerts(capsys)


@pytest.mark.parametrize("value", [150, "01", "-1", "1" * 21, "", "x", True])
def test_malformed_lvbh_holds_without_signing(run, backend, capsys, value):
    backend.lvbh_override = value
    run()
    assert backend.sign_calls == []
    assert alerts(capsys) == [{"alert": "request_malformed", "intent_id": INTENT}]


def test_unexpected_error_is_isolated_and_names_class_only(run, backend, capsys):
    backend.intents = [INTENT, OTHER]

    failed = []

    def boom(name):
        if name == "claim" and not failed:
            failed.append(name)
            raise OSError("/secret/path")

    backend.hook = boom
    assert run() == 0
    assert OTHER in backend.confirmed
    assert alerts(capsys) == [{"alert": "step_error", "intent_id": INTENT, "error": "OSError"}]


def test_pause_during_run_stops_claiming_and_signing(run, backend, config):
    backend.intents = [INTENT, OTHER]
    backend.hook = lambda name: config.pause_path.touch() if name == "claim" else None
    run()
    assert backend.sign_calls == []
    assert backend.claims[INTENT]["state"] == "free"
    assert OTHER not in backend.claims


def test_recompile_in_same_epoch_never_lowers_lvbh(run, backend, config):
    Journal(config.journal_path).put(INTENT, 1, 999, 1.0)
    backend.height = 1000
    backend.commit_script = ["stale"]
    run()
    assert (Journal(config.journal_path).get(INTENT) or {})["lvbh"] == 999


def test_signer_without_output_holds(run, backend, capsys):
    backend.skip_signed = True
    run()
    assert alerts(capsys) == [{"alert": "signer_no_output", "intent_id": INTENT}]
    assert backend.attempts == {}


def test_digest_mismatch_is_a_persistent_hold(run, backend, config, capsys):
    real = backend._prepare_commit
    backend._prepare_commit = lambda a, b: real(a, b) | {"transaction_digest": "0" * 64}
    run()
    run()
    assert backend.sends == []
    assert {"alert": "held", "intent_id": INTENT} in alerts(capsys)


def test_prepared_digest_mismatch_does_not_send(run, backend, config, capsys):
    backend.attempts[INTENT] = {"wire": "AAAA", "public": {"epoch": 1, "transaction_digest": "x"}}
    (config.handoff_dir / f"{INTENT}.1.signed.json").write_text(
        json.dumps({"signed_transaction_base64": "AAAA"})
    )
    run()
    assert backend.sends == []
    assert {"alert": "digest_mismatch", "intent_id": INTENT} in alerts(capsys)


def test_settle_removes_only_stored_epoch_files(run, backend, config):
    other = config.handoff_dir / f"{INTENT}.9.request.json"
    other.write_text("{}")
    run()
    assert handoff_files(config) == [other.name]


def test_held_intents_do_not_starve_the_rest(run, backend, config):
    held = [f"{n:08d}-1111-4111-8111-111111111111" for n in range(8)]
    for intent in held:
        Journal(config.journal_path).hold(intent, "x", 1.0)
    backend.intents = [*held, INTENT]
    run()
    assert INTENT in backend.confirmed


@pytest.mark.parametrize(
    "verdict", ["storage_error", "configuration_error", "not_found", "invalid_input"]
)
def test_transient_reconcile_verdict_alerts_without_hold_and_retries(
    run, backend, config, capsys, verdict
):
    backend.reconcile_script = [verdict, "confirmed"]
    run()
    assert alerts(capsys) == [{"alert": f"reconcile_{verdict}", "intent_id": INTENT}]
    assert len(backend.sends) == 1
    assert "hold" not in (Journal(config.journal_path).get(INTENT) or {})
    assert INTENT not in backend.confirmed
    run()
    assert INTENT in backend.confirmed
    assert len(backend.sends) == 2


def test_race_branch_digest_mismatch_does_not_send(run, backend, config, capsys):
    backend.attempts[INTENT] = {"wire": "AAAA", "public": {"epoch": 1, "transaction_digest": "x"}}
    backend.phase = lambda intent: "unclaimed"
    (config.handoff_dir / f"{INTENT}.1.signed.json").write_text(
        json.dumps({"signed_transaction_base64": "AAAA"})
    )
    run()
    assert backend.sends == []
    assert {"alert": "digest_mismatch", "intent_id": INTENT} in alerts(capsys)


def test_pause_between_compile_and_sign_releases_without_signing(run, backend, config):
    backend.hook = lambda name: config.pause_path.touch() if name == "fetch" else None
    run()
    assert backend.sign_calls == []
    assert backend.claims[INTENT]["state"] == "free"


def test_stuck_prepared_intents_do_not_consume_per_tick(run, backend, config):
    stuck = [f"{n:08d}-1111-4111-8111-111111111111" for n in range(6)]
    for intent in stuck:
        backend.attempts[intent] = {
            "wire": "AAAA",
            "public": {"epoch": 1, "transaction_digest": "x"},
        }
    backend.intents = [*stuck, INTENT]
    backend.send_script = ["sent"] * 6
    backend.reconcile_script = ["unresolved"] * 6
    run()
    assert INTENT in backend.confirmed


@pytest.mark.parametrize("code", [0, 12, 13, 14, 99])
def test_signer_instance_stopped_right_after_status_read(run, backend, code):
    backend.sign_exits = [code]
    run()
    assert backend.order == ["sign", "stop"]
    assert backend.stops == backend.sign_calls


def test_signer_stop_failure_alerts_and_keeps_flow(run, backend, capsys):
    backend.stop_ok = False
    run()
    assert INTENT in backend.confirmed
    assert {"alert": "signer_stop_failed", "intent_id": INTENT} in alerts(capsys)
