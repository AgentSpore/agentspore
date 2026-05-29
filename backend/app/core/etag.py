"""ETag / If-Match header utilities — no settings dependency."""

from fastapi import HTTPException


def parse_if_match(if_match: str | None) -> str | None:
    """Extract the sha from an ``If-Match`` header.

    The header value is an opaque sha string (12-char hex from runner).
    Accepts bare sha, quoted sha (``"<sha>"``), or weak-ETag prefix
    (``W/"<sha>"``). Rejects ``*`` (not supported) with 400.

    Returns:
        Raw sha string, or ``None`` when header is absent.
    """
    if not if_match:
        return None
    value = if_match.strip()
    if value == "*":
        raise HTTPException(400, "If-Match: * is not supported")
    # Strip optional W/ prefix and surrounding double-quotes.
    if value.startswith("W/"):
        value = value[2:]
    value = value.strip('"')
    if not value:
        raise HTTPException(400, f"Malformed If-Match header: {if_match}")
    return value
