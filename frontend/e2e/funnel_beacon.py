"""Answer the public-page visit beacon so strict route mocks can stay strict for everything else."""

import json

PATH = "/api/v1/funnel/visit"
FIELDS = {"path", "utm_source", "referrer", "automated"}
# Backend VisitBeacon limits (backend/app/schemas/funnel.py).
MAX_PATH = 200
MAX_UTM = 64


def _valid(body):
    if not isinstance(body, dict) or set(body) - FIELDS:
        return False
    if any(isinstance(v, bool) != (k == "automated") or not isinstance(v, (str, bool)) for k, v in body.items()):
        return False
    path = body.get("path")
    return path is not None and 0 < len(path) <= MAX_PATH and len(body.get("utm_source", "")) <= MAX_UTM


def is_beacon(request, target):
    """True only for a JSON POST to the exact beacon path whose body the backend would accept."""
    if request.method != "POST" or target.path != PATH or target.query:
        return False
    if request.headers.get("content-type") != "application/json":
        return False
    try:
        body = json.loads(request.post_data or "")
    except (ValueError, TypeError):
        return False
    return _valid(body)


def answer(route, target):
    """Reply 204 to a well-formed beacon; return False to let the caller reject it."""
    if not is_beacon(route.request, target):
        return False
    route.fulfill(status=204)
    return True
