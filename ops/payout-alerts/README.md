# payout-alerts

Forwards payout alerts from the systemd journal to one Telegram chat (Bot API).
Stdlib-only Python 3.12.

## Forwarded

From `payout-orchestrator.service` and `payout-signer@*.service` only:

- a `MESSAGE` that is a JSON object with a string `alert` key; only `alert`, `intent_id`,
  `error`, `verdict` are rendered as `key=value`, values must match `[A-Za-z0-9_.:-]{1,64}`
  else `?`;
- systemd's unit-failed message (`MESSAGE_ID=d9b373ed55a64feb8242e02dbe79a49c`) with a
  result other than `success`: `unit_failed <unit> <result>`.

No free-text log line is forwarded.

## Delivery

- Identical texts within 10 minutes are sent once, then `(+N suppressed)`.
- Queue of 100; oldest dropped when full, shown as `(+N dropped)`.
- 3 attempts per alert; 429 waits `retry_after` (max 60 s); 400/401/403/404 are not retried;
  then the alert is dropped. A dropped alert does not start a dedupe window.
- The cursor (`$STATE_DIRECTORY/cursor`) moves only after a send or a deliberate drop;
  the first non-alert entry also saves a cursor, so a restart does not fall back to
  `--since=now`; later starts use `--after-cursor`. If the journal reader dies, the process exits 1. journalctl restarts with backoff.
- Token: systemd credential `bot-token`; never logged, only exception class and HTTP status.

## Use

    # --config defaults to $CREDENTIALS_DIRECTORY/config (systemd LoadCredential)
    # config.toml: chat_id = "<chat-id>", optional prefix = "[payouts]"
    payout-alerts test --config /etc/payout-alerts/config.toml   # one message, exit 0 on ok
    payout-alerts run  --config /etc/payout-alerts/config.toml   # daemon

Dev: `uv sync && uv run ruff check . && uv run ty check src tests && uv run pytest -q`.
