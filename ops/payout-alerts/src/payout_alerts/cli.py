"""`payout-alerts run|test --config PATH`."""

import argparse
import logging
import os
import signal
import sys
import threading
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
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        config = load_config(args.config)
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


def run(sender: TelegramSender) -> int:
    state_dir = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/payout-alerts"))
    cursor_path = state_dir / "cursor"
    relay = Relay(sender.send, cursor_path)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    reader = threading.Thread(
        target=follow, args=(cursor_path, relay.submit, stop), daemon=True, name="journal"
    )
    reader.start()
    relay.run(stop)
    return 0
