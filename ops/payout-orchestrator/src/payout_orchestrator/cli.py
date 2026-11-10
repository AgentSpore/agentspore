"""Entry point: `payout-orchestrator CONFIG_PATH` runs one tick and exits."""

import sys
from pathlib import Path

from .config import load_config
from .executor import DockerExecutor
from .journal import Journal
from .tick import Tick


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("usage: payout-orchestrator CONFIG_PATH\n")
        return 2
    config = load_config(Path(args[0]))
    config.journal_path.parent.mkdir(parents=True, exist_ok=True)
    executor = DockerExecutor(config.container, config.step_timeout_seconds)
    return Tick(config, executor, Journal(config.journal_path)).run()
