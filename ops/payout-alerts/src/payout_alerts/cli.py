"""`payout-alerts run|test --config PATH`."""

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .config import ConfigError, load_config
from .journal import follow
from .relay import Relay
from .telegram import SendError, TelegramSender, read_token

log = logging.getLogger("payout_alerts")
TEST_MESSAGE = "payout-alerts test message"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="payout-alerts")
    parser.add_argument("command", choices=("run", "test"))
    parser.add_argument("--config", type=Path, help="default: $CREDENTIALS_DIRECTORY/config")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        config = load_config(args.config or Path(os.environ["CREDENTIALS_DIRECTORY"]) / "config")
        sender = TelegramSender(read_token(), config.chat_id, config.prefix)
    except (ConfigError, KeyError, OSError) as exc:
        log.error("startup failed: %s", type(exc).__name__)
        return 2
    if args.command == "test":
        try:
            sender.send(TEST_MESSAGE)
        except SendError as exc:
            log.error("test message failed: %s status=%s", exc.kind, exc.status)
            return 1
        return 0
    return run(sender)


def run(sender: TelegramSender, popen: Callable | None = None) -> int:
    """Run the daemon; returns 1 when the journal reader died so systemd restarts the unit."""
    state_dir = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/payout-alerts"))
    cursor_path = state_dir / "cursor"
    stop, failed = threading.Event(), threading.Event()
    relay = Relay(sender.send, cursor_path, sleep=stop.wait)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    def read_journal() -> None:
        try:
            follow(cursor_path, relay.submit, stop, popen)
        except Exception as exc:
            log.error("journal reader failed: %s", type(exc).__name__)
            failed.set()
            stop.set()

    threading.Thread(target=read_journal, daemon=True, name="journal").start()
    relay.run(stop)
    return 1 if failed.is_set() else 0
