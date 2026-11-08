"""Stable refusal vocabulary shared by every pre-build step."""


class DeployRejectedError(Exception):
    """A deployment is refused; `reason_code` is stored on the row, `detail` is safe to log."""

    def __init__(self, reason_code: str, detail: str = ""):
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


class LeaseLostError(Exception):
    """The fence expired or moved on while a build was running."""
