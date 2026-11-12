import multiprocessing
import sqlite3
import stat

from payout_signer.config import load_config
from payout_signer.ledger import WINDOW_SECONDS, Ledger
from payout_signer.limits import Transfer, within_limits


def test_ledger_and_lock_are_private(env):
    with Ledger.locked(env.ledger):
        pass
    for path in (env.ledger, env.ledger.with_name("ledger.sqlite.lock")):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_window_rolls(env):
    with Ledger.locked(env.ledger) as ledger:
        ledger.record("d1", "r", 5, now=1000.0)
        assert ledger.usage(1000.0 + WINDOW_SECONDS - 1).transfers == 1
        assert ledger.usage(1000.0 + WINDOW_SECONDS + 1).transfers == 0


def test_digest_is_unique_and_replay_visible(env):
    with Ledger.locked(env.ledger) as ledger:
        assert not ledger.has("d1")
        ledger.record("d1", "r", 5, now=1.0)
        assert ledger.has("d1")


def test_usage_sums_per_recipient(env):
    with Ledger.locked(env.ledger) as ledger:
        ledger.record("a", "r1", 5, now=1.0)
        ledger.record("b", "r1", 7, now=1.0)
        ledger.record("c", "r2", 3, now=1.0)
        usage = ledger.usage(2.0)
    assert (usage.transfers, usage.total, usage.per_recipient) == (3, 15, {"r1": 12, "r2": 3})


def test_old_ledger_gets_column(env):
    old = sqlite3.connect(env.ledger)
    old.execute(
        "CREATE TABLE signatures (message_digest TEXT PRIMARY KEY, recipient TEXT NOT NULL,"
        " amount TEXT NOT NULL, signed_at INTEGER NOT NULL) WITHOUT ROWID"
    )
    old.execute("INSERT INTO signatures VALUES ('old', 'r', '5', 1000)")
    old.commit()
    old.close()
    for _ in range(2):  # a second open must not try to add the column again
        with Ledger.locked(env.ledger) as ledger:
            assert ledger.usage(1001.0).ata_creations == 0
    with Ledger.locked(env.ledger) as ledger:
        ledger.record("new", "w", 1, now=1002.0, creates_ata=True)
        usage = ledger.usage(1003.0)
    assert (usage.transfers, usage.ata_creations) == (2, 1)


def _worker(barrier, config_path, index):
    config = load_config(config_path)
    barrier.wait()
    with Ledger.locked(config.ledger_path) as ledger:
        transfer = Transfer("r", 1000)
        if within_limits(config, ledger.usage(1.0), transfer):
            ledger.record(f"d{index}", transfer.recipient, transfer.amount, 1.0)


def test_concurrent_writers_never_exceed_total(env):
    env.write_config(
        max_amount_total_24h=3000, max_transfers_24h=100, max_amount_per_recipient_24h=10**6
    )
    barrier = multiprocessing.Barrier(12)
    jobs = [
        multiprocessing.Process(target=_worker, args=(barrier, env.config, i)) for i in range(12)
    ]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join()
    with Ledger.locked(env.ledger) as ledger:
        assert ledger.usage(2.0).total == 3000
