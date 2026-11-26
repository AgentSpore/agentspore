"""CI runner for the browser suite: one production build per mode, files run one after another.

NEXT_PUBLIC_API_URL is baked in at `next build`, so every test file belongs to exactly one build
mode. MODES is the single source of truth; a test_*.py file that is not listed (or excluded)
fails the run, so new files must be classified here.

    python run_ci.py --mode default --out DIR [--shard I/N] [--only test_a,test_b]
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent
FILE_TIMEOUT = 600
FILE_TIMEOUTS = {"pixel-nightly": 2700}
BUILD_TIMEOUT = 900
FAIL_TAIL_LINES = 60
PORTS = {"default": 3970, "unset": 3971, "prodapi": 3972, "showcase": 3973, "pixel": 3974, "pixel-nightly": 3975}

# default: NEXT_PUBLIC_API_URL="" (synthetic API through context.route).
# unset: variable absent from the build environment.
# prodapi: built against https://agentspore.com, and the test also reads the live API.
# showcase: built against the same loopback origin the stand is served on.
MODES = {
    "default": [
    "test_agent_blog",
    "test_agent_detail_states",
    "test_agent_empty",
    "test_agent_metadata",
    "test_agents_list",
    "test_auth_first_screen",
    "test_auth_forms",
    "test_blog_comments",
    "test_blog_geometry",
    "test_blog_reactions",
    "test_blog_reads",
    "test_chat_ownership",
    "test_chat_profile_states",
    "test_copy_i18n",
    "test_dashboard_anon",
    "test_detail_width",
    "test_email_verification",
    "test_header_nav",
    "test_header_states",
    "test_mobile_layout",
    "test_model_usage",
    "test_oauth_callback",
    "test_private_chat_accessibility",
    "test_private_chat_states",
    "test_profile_reads",
    "test_profile_wallet",
    "test_project_action",
    "test_project_cards",
    "test_project_chat_accessibility",
    "test_project_chat_drafts",
    "test_project_chat_errors",
    "test_project_detail_join",
    "test_project_detail_login",
    "test_project_detail_metadata",
    "test_project_detail_reads",
    "test_project_detail_votes",
    "test_project_list",
    "test_project_tabs",
    "test_projects_filters",
    "test_public_chat_accessibility",
    "test_public_chat_history_auth",
    "test_public_chat_mutations",
    "test_public_chat_operation_errors",
    "test_public_chat_states",
    "test_public_chat_transitions",
    "test_seo_basics",
    "test_team_detail_geometry",
    "test_team_detail_reads",
    "test_teams_list",
    "test_toast_progress",
    "test_typography",
    "test_welcome_reward",
    "test_workspace_states",
    ],
    "unset": ["test_home_first_screen"],
    "prodapi": ["test_home_services"],
    "showcase": ["test_showcase_cards", "test_showcase_catalog"],
    # macOS-calibrated pixel diffs: Linux antialiasing paints outside glyph boxes (measured 8 and 28 px).
    "pixel": ["test_local_text_mask"],
    # Needs about 1430 s of its own: nightly only, never on a pull request.
    "pixel-nightly": ["test_project_card_contrast"],
}
EXCLUDED = {
    "test_welcome_journey_stand": "needs a real stand (backend, database, mail capture); skips itself without one",
}


def build_env(mode: str, port: int) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("NEXT_PUBLIC_API_URL", None)
    values = {"default": "", "pixel": "", "pixel-nightly": "", "prodapi": "https://agentspore.com", "showcase": f"http://localhost:{port}"}
    if mode in values:
        env["NEXT_PUBLIC_API_URL"] = values[mode]
    return env


def check_classification() -> None:
    on_disk = {p.stem for p in HERE.glob("test_*.py")}
    listed = [name for names in MODES.values() for name in names]
    problems = [f"unclassified: {n}" for n in sorted(on_disk - set(listed) - set(EXCLUDED))]
    problems += [f"listed but missing: {n}" for n in sorted((set(listed) | set(EXCLUDED)) - on_disk)]
    problems += [f"listed twice: {n}" for n in sorted({n for n in listed if listed.count(n) > 1})]
    if problems:
        sys.exit("e2e classification is out of date in run_ci.py:\n  " + "\n  ".join(problems))


def chromium_path() -> str:
    with sync_playwright() as p:
        return p.chromium.executable_path


def build(env: dict[str, str], log: IO[str]) -> None:
    print("building frontend ...", flush=True)
    done = subprocess.run(["npm", "run", "build"], cwd=FRONTEND, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=BUILD_TIMEOUT)
    if done.returncode:
        sys.exit(f"next build failed (see {log.name})")


def wait_ready(base: str, server: subprocess.Popen) -> None:
    for _ in range(120):
        if server.poll() is not None:
            sys.exit("next start exited early")
        try:
            urlopen(base + "/", timeout=5).close()
            return
        except OSError:
            time.sleep(1)
    sys.exit("next start did not become ready in 120 s")


def stop(server: subprocess.Popen) -> None:
    if server.poll() is None:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)


def run_file(name: str, base: str, executable: str, out: Path, timeout: int) -> bool:
    shots = out / "screenshots" / name
    shots.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, f"{name}.py", "--base-url", base, "--executable", executable, "--screenshots", str(shots)]
    start = time.monotonic()
    with open(out / "logs" / f"{name}.log", "w") as log:
        try:
            ok = subprocess.run(cmd, cwd=HERE, stdout=log, stderr=subprocess.STDOUT, timeout=timeout).returncode == 0
        except subprocess.TimeoutExpired:
            log.write(f"\nTIMEOUT after {timeout}s\n")
            ok = False
    print(f"{'PASS' if ok else 'FAIL'} {name} ({time.monotonic() - start:.0f}s)", flush=True)
    if not ok:
        tail = (out / "logs" / f"{name}.log").read_text(errors="replace").splitlines()[-FAIL_TAIL_LINES:]
        print(f"::group::FAIL {name}", flush=True)
        print("\n".join(tail), flush=True)
        print("::endgroup::", flush=True)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--executable", help="Chromium binary; default is the build bundled with the installed playwright client")
    parser.add_argument("--shard", default="1/1", help="I/N: run every N-th file of the mode, starting at the I-th")
    parser.add_argument("--out", type=Path, required=True, help="log and screenshot directory (outside the build tree)")
    parser.add_argument("--only", help="comma-separated subset of the mode's files")
    args = parser.parse_args()
    check_classification()
    names = MODES[args.mode]
    if args.only:
        names = [n for n in names if n in args.only.split(",")]
    index, total = (int(part) for part in args.shard.split("/"))
    names = sorted(names)[index - 1 :: total]
    port = PORTS[args.mode]
    base = f"http://localhost:{port}"
    out = args.out.resolve()
    (out / "logs").mkdir(parents=True, exist_ok=True)
    env = build_env(args.mode, port)
    with open(out / "logs" / "_build.log", "w") as build_log:
        build(env, build_log)
        server_log = open(out / "logs" / "_server.log", "w")
        server = subprocess.Popen(
            ["npx", "next", "start", "-p", str(port)], cwd=FRONTEND, env=env,
            stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    try:
        wait_ready(base, server)
        executable = args.executable or chromium_path()
        failed = [n for n in names if not run_file(n, base, executable, out, FILE_TIMEOUTS.get(args.mode, FILE_TIMEOUT))]
    finally:
        stop(server)
        server_log.close()
    print(f"{len(names) - len(failed)} passed / {len(failed)} failed: {' '.join(failed) or '-'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
