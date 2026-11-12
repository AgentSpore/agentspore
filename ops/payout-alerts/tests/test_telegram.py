import io
import json
import logging
import traceback
import urllib.error
from email.message import Message

import pytest

from payout_alerts.telegram import SendError, TelegramSender

TOKEN = "123456:SECRET-token_value"


class Resp:
    def __init__(self, body=b'{"ok": true}', status=200):
        self.body, self.status = body, status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"{}"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    return urllib.error.HTTPError(url, code, "x", Message(), io.BytesIO(body))


def sender(opener, prefix=""):
    return TelegramSender(TOKEN, "-100", prefix=prefix, opener=opener)


def test_posts_form_with_prefix():
    seen = {}

    def opener(request, timeout):
        seen["url"], seen["data"], seen["timeout"] = request.full_url, request.data, timeout
        return Resp()

    sender(opener, prefix="[p]").send("alert=a")
    assert seen["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert b"chat_id=-100" in seen["data"] and b"text=%5Bp%5D+alert%3Da" in seen["data"]
    assert seen["timeout"] > 0


def test_429_carries_retry_after():
    body = json.dumps({"ok": False, "parameters": {"retry_after": 7}}).encode()

    def opener(request, timeout):
        raise http_error(429, body)

    with pytest.raises(SendError) as err:
        sender(opener).send("x")
    assert err.value.status == 429 and err.value.retry_after == 7


def test_not_ok_body_is_error():
    with pytest.raises(SendError):
        sender(lambda r, timeout: Resp(b'{"ok": false}')).send("x")


@pytest.mark.parametrize("exc", [http_error(401), urllib.error.URLError("boom"), TimeoutError()])
def test_token_never_in_exception_or_logs(exc, caplog, capsys):
    def opener(request, timeout):
        raise exc

    caplog.set_level(logging.DEBUG)
    with pytest.raises(SendError) as err:
        sender(opener).send("x")
    text = "".join(traceback.format_exception(err.value)) + repr(err.value) + caplog.text
    captured = capsys.readouterr()
    assert "SECRET" not in text + captured.out + captured.err
    assert "SECRET" not in repr(sender(opener))
