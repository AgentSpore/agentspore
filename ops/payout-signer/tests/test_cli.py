import json
import multiprocessing
import subprocess
import sys

from payout_signer.signer import sign_request


def cli(*args):
    return subprocess.run(  # noqa: S603 (fixed argv: own interpreter, tmp paths)
        [sys.executable, "-m", "payout_signer", *args], capture_output=True, text=True, check=False
    )


def sign_args(env, request):
    return [
        "sign",
        "--request",
        str(request),
        "--output",
        str(env.root / "o.json"),
        "--config",
        str(env.config),
    ]


def test_cli_stdout_is_only_verdict_and_hides_key(env):
    done = cli(*sign_args(env, env.request(env.message())))
    assert done.returncode == 0
    assert json.loads(done.stdout) == {"verdict": "signed"}
    secret = (env.root / "creds" / "keypair").read_text()
    numbers = json.loads(secret)
    for stream in (done.stdout, done.stderr):
        assert secret not in stream
        assert str(env.signer.pubkey()) not in stream
        assert ", ".join(map(str, numbers[:8])) not in stream


def test_cli_exit_codes_distinct(env):
    env.pause.touch()
    paused = cli(*sign_args(env, env.request(env.message())))
    missing = cli("sign", "--request", "x", "--output", "y", "--config", str(env.root / "none"))
    assert json.loads(paused.stdout) == {"verdict": "paused"}
    assert json.loads(missing.stdout) == {"verdict": "config_error"}
    assert len({paused.returncode, missing.returncode, 0}) == 3


def _worker(barrier, config, request, out):
    barrier.wait()
    sign_request(config, request, out)


def test_concurrent_signers_respect_total_limit(env):
    env.write_config(
        max_amount_total_24h=3000, max_transfers_24h=100, max_amount_per_recipient_24h=10**6
    )
    jobs = []
    barrier = multiprocessing.Barrier(12)
    for i in range(12):
        request = env.request(env.message(1000), f"{i}.json")
        args = (barrier, env.config, request, env.root / f"{i}.out")
        jobs.append(multiprocessing.Process(target=_worker, args=args))
    for job in jobs:
        job.start()
    for job in jobs:
        job.join()
    assert len(list(env.root.glob("*.out"))) == 3
