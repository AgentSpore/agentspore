"""Fixed verdict vocabulary and its distinct exit codes."""

from enum import Enum


class Verdict(Enum):
    SIGNED = ("signed", 0)
    REPLAYED = ("replayed", 10)
    PAUSED = ("paused", 11)
    REJECTED = ("rejected", 12)
    LIMIT_EXCEEDED = ("limit_exceeded", 13)
    CONFIG_ERROR = ("config_error", 14)

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def exit_code(self) -> int:
        return self.value[1]
