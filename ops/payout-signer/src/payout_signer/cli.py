"""Command line: fixed JSON on stdout, nothing else, distinct exit codes."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from payout_signer.config import ConfigError
from payout_signer.status import status
from payout_signer.verdict import Verdict


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="payout-signer")
    commands = parser.add_subparsers(dest="command", required=True)
    state = commands.add_parser("status")
    state.add_argument("--config", type=Path, required=True)
    return parser


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _emit(status(args.config))
    except (ConfigError, OSError, sqlite3.Error):
        _emit({"verdict": Verdict.CONFIG_ERROR.label})
        return Verdict.CONFIG_ERROR.exit_code
    return 0
