import pytest

from payout_alerts import cli
from payout_alerts.config import ConfigError, load_config


def write(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def test_config_ok_and_bad_refused(tmp_path):
    assert load_config(write(tmp_path, 'chat_id = "-100"\nprefix = "[p]"\n')).prefix == "[p]"
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, 'chat_id = "-100"\nextra = 1\n'))
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, 'chat_id = "a b"\n'))


def run_test_command(tmp_path, monkeypatch, sender_cls):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    (tmp_path / "bot-token").write_text("1:T\n")
    monkeypatch.setattr(cli, "TelegramSender", sender_cls)
    return cli.main(["test", "--config", str(write(tmp_path, 'chat_id = "-100"\n'))])


def test_test_command_exit_zero_on_ok(tmp_path, monkeypatch):
    sent = []

    class Ok:
        def __init__(self, *args, **kwargs):
            pass

        def send(self, text):
            sent.append(text)

    assert run_test_command(tmp_path, monkeypatch, Ok) == 0
    assert len(sent) == 1


def test_test_command_nonzero_on_failure(tmp_path, monkeypatch):
    class Bad:
        def __init__(self, *args, **kwargs):
            pass

        def send(self, text):
            raise cli.SendError(401, None, "HTTPError")

    assert run_test_command(tmp_path, monkeypatch, Bad) == 1
