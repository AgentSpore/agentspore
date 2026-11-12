# payout-alerts

Forwards payout alerts from the systemd journal to one Telegram chat through the Bot API.
Runtime is stdlib-only Python 3.12. Unit, install steps and layout:
[payout-deploy](../payout-deploy/README.md).

## What is forwarded

Entries of `payout-orchestrator.service` and `payout-signer@*.service` (journalctl
`--unit`, re-checked in Python), and only these two kinds:

- a `MESSAGE` that is a JSON object with a string `alert` key (written by
  [payout-orchestrator](../payout-orchestrator/README.md), "Alerts"). Only the keys
  `alert`, `intent_id`, `error`, `verdict` are rendered, as `key=value`. A value must match
  `[A-Za-z0-9_.:-]{1,64}`, otherwise it is shown as `?`.
- systemd's "unit failed" message (`MESSAGE_ID=d9b373ed55a64feb8242e02dbe79a49c`) about one of
  those units with a result other than `success`: `unit_failed <unit> <result>`.

No free-text log line is ever forwarded, since it may carry data.

## Delivery

- Identical texts inside 10 minutes are sent once; the next one after the window carries
  `(+N suppressed)`.
- The in-memory queue holds 100 alerts. When full the oldest is dropped; the count is
  appended to the next message as `(+N dropped)`.
- A failed send is retried up to 3 times. A 429 waits for Telegram's `retry_after`
  (at most 60 s). After the last attempt the alert is dropped and logged.
- The cursor (`$STATE_DIRECTORY/cursor`) is written by this program, not by journalctl,
  only after an alert is sent or deliberately dropped (suppressed, or retries exhausted).
  A restart resumes with `--after-cursor`; the first start uses `--since=now`.
  journalctl is restarted with backoff (1 s up to 60 s) when it exits.
- The bot token is read from the systemd credential `bot-token`. The request URL is built
  inside the send call; logs and errors carry only the exception class and HTTP status.

## Config (TOML, unknown keys refused)

    chat_id = "<chat-id-or-@channel>"
    prefix = "[payouts]"    # optional, at most 64 characters

## Commands

    payout-alerts test --config /etc/payout-alerts/config.toml   # one fixed message; exit 0 on ok
    payout-alerts run  --config /etc/payout-alerts/config.toml   # the daemon

## Development

    uv sync && uv run ruff check . && uv run ruff format --check . \
        && uv run ty check src tests && uv run pytest -q
