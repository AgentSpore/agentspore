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
  with no saved cursor, the newest journal entry's cursor is saved once before following
  (`journalctl -n 1`), so restarts resume right after it, never at `--since=now`.
  If the journal reader dies, the process exits 1. journalctl restarts with backoff.
- While the queue is not full, the cursor also advances every 1000 non-alert journal entries,
  so a restart re-reads about that many entries plus the queue. During a delivery outage, or
  while alerts are dropped, it does not advance that way and a restart can re-read more.
- journalctl runs under `stdbuf -oL` (coreutils): into a pipe it block-buffers, and a live
  alert would wait until about 4 KiB of later entries arrive.
- Token: systemd credential `bot-token`; never logged, only exception class and HTTP status.

## Use

    # --config defaults to $CREDENTIALS_DIRECTORY/config (systemd LoadCredential)
    # config.toml: chat_id = "<chat-id>", optional prefix = "[payouts]"
    payout-alerts test --config /etc/payout-alerts/config.toml   # one message, exit 0 on ok
    payout-alerts run  --config /etc/payout-alerts/config.toml   # daemon

Dev: `uv sync && uv run ruff check . && uv run ty check src tests && uv run pytest -q`.
