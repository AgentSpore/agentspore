"""Answer the public-page visit beacon so strict route mocks can stay strict for everything else."""

PATH = "/api/v1/funnel/visit"
FIELDS = {"path", "utm_source", "referrer"}


def is_beacon(request, target):
    """True only for a bodied-JSON POST to the exact beacon path with the known fields."""
    if request.method != "POST" or target.path != PATH or target.query:
        return False
    try:
        body = request.post_data_json
    except ValueError:
        return False
    return isinstance(body, dict) and "path" in body and set(body) <= FIELDS


def answer(route, target):
    """Reply 204 to a well-formed beacon; return False to let the caller reject it."""
    if not is_beacon(route.request, target):
        return False
    route.fulfill(status=204)
    return True
