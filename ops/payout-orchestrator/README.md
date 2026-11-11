# payout-orchestrator

Host-side driver for one payout tick. It never signs or broadcasts by itself: it
calls the backend CLIs inside the container (`docker exec`), starts the
`payout-signer@<intent>.<epoch>` systemd unit, and moves files between them.
Runtime is stdlib-only Python 3.12. systemd units, users and install order: [payout-deploy](../payout-deploy/README.md).

## Tick order

1. Pause file present: exit 0, nothing else runs.
2. Non-blocking `flock` on `<journal>.lock`; if held, exit 0 (another tick runs).
3. `welcome-payouts link --apply`.
4. `payout_delivery pending --limit N`, then per intent (at most N):
   - `unclaimed`: claim. `expired`: lease-show, recover, claim.
   - `claimed`: resume only when the lease belongs to our worker id.
   - `prepared`: send, reconcile. `confirmed`: skip.
5. After a claim: `attempt-show`; only `not_found` allows a compile. Then
   `auto-compile` (request file in the container), copy to the handoff dir, journal,
   signer unit, `prepare-commit` with the signed file as is.
6. Handoff files are deleted only when the digest returned by the backend equals
   the SHA-256 of the signed wire in the file.

## Safety invariants

- Only `auto-send` broadcasts, and only the wire stored by the backend. Nothing is
  re-signed; `unresolved` and `preflight_rejected` lead to reconcile and a bounded
  resend (`max_resends`) of the same wire, never to a compile.
- A new compile for an intent needs `attempt-show = not_found` and the previous
  unpersisted request's `last_valid_block_height` (lvbh) to have passed. The journal
  (`{intent: {epoch, lvbh, at}}`, atomic write, no bytes) carries that fact across crashes.
- Fail closed on height. Blockhash validity counts blocks, not seconds, so no time-based
  wait proves expiry. The gate opens only when `block_height()` returns an integer
  greater than the journal lvbh. The executor calls `payout_delivery block-height` with
  `{}` on stdin and expects `{"verdict": "found", "height": N}` at finalized commitment;
  any other answer or error is "unknown", the intent stays held and the alert
  `lvbh_unproven` is emitted. The journal field `at` is audit only.
- Branching is on the JSON `verdict` only. The CLIs use different exit-code tables.

## Signer exit codes

0 signed and 10 replayed: commit. 11 paused: release the claim, stop claiming for the
run, keep sending and reconciling. 12 rejected: alert and hold (the claim is not released and the intent is never taken again until a human clears the hold). 13 limit_exceeded:
alert, release, stop signing for the run. 14 config_error or anything else: alert and
abort the run with exit code 3.

## Alerts

One JSON line on stderr: `{"alert": "<reason>", "intent_id": "<uuid>|null"}`. Reasons
are fixed identifiers; verdicts are accepted only as `[a-z_]{1,32}`. No amounts,
wallets or endpoints are ever printed.

## Config (TOML or JSON, unknown keys refused)

    container = "<backend-container-name>"
    worker_id = "<fixed-uuid-of-this-host>"
    handoff_dir = "<handoff-dir>"      # signer unit reads <intent>.<epoch>.request.json
                                        # and writes <intent>.<epoch>.signed.json here
    journal_path = "<state-dir>/journal.json"
    pause_path = "<state-dir>/pause"
    per_tick = 5
    # optional: container_request_dir, lease_ttl_seconds (<=300),
    #           step_timeout_seconds (keep it above the signer unit's
    #           TimeoutStartSec, 45 s in payout-deploy), max_resends,
    #           container_python (absolute path of the backend interpreter
    #           inside the container, default "/app/.venv/bin/python":
    #           the backend image's virtualenv; the `python` on PATH there
    #           lacks the app's dependencies)

Run one tick: `payout-orchestrator <config-path>`. On hosts with
[payout-deploy](../payout-deploy/README.md) use `systemctl start payout-orchestrator.service`
instead: the unit clears finished signer instances first (a reused instance name would
return its old status) and applies the sandbox.

## Operator runbook

- Pause: create `pause_path`. The next tick exits 0 at once; a running tick re-checks it
  before each intent and before each signing, so it stops starting new work and releases a
  claim it just took. Resume: delete it.
- Alerts: `signer_stop_failed` (the finished signer instance could not be stopped and may
  still hold its credential copy: run `systemctl stop payout-signer@<intent>.<epoch>.service`;
  the signing verdict is unaffected), `signer_rejected`, `signer_limit_exceeded`, `commit_conflict`,
  `digest_mismatch`, `resend_exhausted` need a human; nothing retries a `hold` by itself
  except the next tick's normal flow. `lvbh_unproven` means the chain height is unknown or not yet past the old request: nothing recompiles until it is proven. `signer_config_error` aborts the run: fix the
  signer config first.
- Hold: `signer_rejected`, `request_malformed`, `signer_no_output`, `digest_mismatch`,
  `commit_<verdict>` (any prepare-commit verdict other than prepared/replayed/stale) and
  `reconcile_failed`/`reconcile_conflict`/`reconcile_unsupported` write
  `"hold": "<reason>"` into the intent's journal entry; every later tick alerts `held` and
  skips the intent. Other auto-reconcile verdicts (for example `reconcile_storage_error`)
  only alert and are retried on the next tick, with no hold.
- Clearing a hold, only after investigating: pause, remove only the `"hold"` field from the
  intent's journal entry and keep `epoch` and `lvbh` (they gate recompiling until the old
  request has expired; deleting the whole key lets the next tick sign again while an
  earlier signature may still be valid). For `request_malformed`/`signer_rejected` also
  remove the `<intent>.<epoch>.*` handoff files. Then resume.
- Any other unexpected error in one intent is alerted as `step_error` with the exception
  class name only; the remaining intents still run.
- Stuck intent: a journal entry blocks recompiling until the old request expired. Do
  not edit the journal by hand while a tick may run.
- Handoff files left after a crash are cleaned by the next tick once the stored digest
  matches; mismatching files are kept for inspection. Only the stored epoch's files are
  removed; leftovers of earlier epochs are for the operator.
- A tick fetches up to 4x `per_tick` rows and stops after `per_tick` intents that made
  progress (claimed, or confirmed). Held intents and prepared intents that stay
  unresolved do not use a slot; more than 3x `per_tick` of them can still fill the fetched
  window, so clear or investigate holds.

## Development

    uv sync
    uv run pytest
    uv run ruff check . && uv run ruff format --check . && uv run ty check src tests
