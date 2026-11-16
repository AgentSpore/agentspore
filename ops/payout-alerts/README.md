# payout-alerts

Forwards payout alerts from the systemd journal to one Telegram chat (Bot API).
Stdlib-only Python 3.12.

## Forwarded

From `payout-orchestrator.service`, `welcome-pending-signal.service`, `agentspore-pg-backup.service`
and `payout-signer@*.service` only:

- a `MESSAGE` that is a JSON object with a string `alert` key; only `alert`, `intent_id`,
  `error`, `verdict` are rendered as `key=value`, values must match `[A-Za-z0-9_.:-]{1,64}`
  else `?`;
- for `alert` = `welcome_pending` only, the keys `pending` and `clean` too, and only as plain
  integers 0..1000000 (anything else, including a string, is `?`); other alerts never pass them;
- from the backup unit only: a JSON `MESSAGE` with `backup` = `fail` becomes
  `alert=backup_fail stage=<stage>`, where `stage` is one of `mkdir`, `lock`, `diskspace`, `dump`,
  `verify`, `rename`, `weekly` (anything else is `?`). `backup` = `ok` lines and every other key
  (sizes, paths) are never forwarded. The watched unit names are fixed in code
  (`parse.WATCHED_UNIT`), not configurable by environment;
- systemd's unit-failed message (`MESSAGE_ID=d9b373ed55a64feb8242e02dbe79a49c`) with a
  result other than `success`: `unit_failed <unit> <result>`. For the backup unit this is sent in addition to the stage alert
  (the texts differ, so dedupe does not merge them); it also covers a run that died before
  writing any line. A failed run therefore sends two messages, and the unit's retries (when it
  is configured with `Restart=on-failure`) repeat them.

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
  so a restart re-reads about that many entries plus the queue. It does not advance this way
  during a delivery outage, nor after an alert is evicted from a full queue until the next alert
  is sent; a restart can then re-read more.
- journalctl runs under `stdbuf -oL` (coreutils): into a pipe it block-buffers, and a live
  alert would wait until about 4 KiB of later entries arrive.
- Token: systemd credential `bot-token`; never logged, only exception class and HTTP status.

## Use

    # --config defaults to $CREDENTIALS_DIRECTORY/config (systemd LoadCredential)
    # config.toml: chat_id = "<chat-id>", optional prefix = "[payouts]"
    payout-alerts test --config /etc/payout-alerts/config.toml   # one message, exit 0 on ok
    payout-alerts run  --config /etc/payout-alerts/config.toml   # daemon

Dev: `uv sync && uv run ruff check . && uv run ty check src tests && uv run pytest -q`.
