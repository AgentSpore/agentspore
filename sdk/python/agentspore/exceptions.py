"""AgentSpore SDK exceptions."""


class AgentSporeError(Exception):
    """Base exception for AgentSpore SDK."""


class AuthError(AgentSporeError):
    """Authentication failed — invalid or missing API key."""


class NotFoundError(AgentSporeError):
    """Resource not found."""


class APIError(AgentSporeError):
    """Unexpected API error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error {status_code}: {detail}")
