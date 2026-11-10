# payout-signer

Standalone host-side signer for the external-signature payout flow. It turns a
backend signing request (`prepare-message --output`) into the signed envelope that
`prepare-commit` accepts, under hard limits. It has no network access and the
signing key never enters the internet-facing backend container.

## Threat model

- Backend compromise: the attacker can submit any request file, but the signer
  signs only one exact Token-2022 `TransferChecked` from its own associated
  account for the configured mint and decimals, and only within the configured
  amount, recipient, total and count limits over a rolling 24 hours. Anything else
  is refused.
- Host root compromise: the key is readable, so the loss is bounded by the hot
  wallet balance. Keep that balance small.
- The ledger is local state on the host; the signer trusts it. Deleting it resets
  the limits, so it lives in the service-private state directory
  (`StateDirectory=` under `DynamicUser=`), which other users cannot write.
- The per-recipient limit is counted per destination token account, not per owner
  wallet. A compromised backend can spread the total across many token accounts of
  one owner, so only the per-transfer, total and count limits bound it.

## Backend intent requirements

The signer signs only what it can reproduce itself. The backend intent must have
`fee_payer` equal to `authority` equal to the signer's public key, and `source`
equal to the signer's Token-2022 associated token account for the configured mint.
Any other intent is rejected by design.

## Commands

    payout-signer sign --request PATH --output PATH --config PATH
    payout-signer status --config PATH

`sign` prints one JSON verdict and nothing else (no keys, bytes, addresses or
amounts). Exit codes: `signed` 0, `replayed` 10, `paused` 11, `rejected` 12,
`limit_exceeded` 13, `config_error` 14. A repeated request with the same message
digest returns the identical signed transaction and is not counted twice. The
output file is created exclusively with mode 0600; existing paths and symbolic
links are refused. The ledger row is committed and fsynced before the output is
written; an exclusive file lock serialises concurrent signers.

`status` prints numbers only: usage in the last 24 hours against each limit, and
whether the pause file exists.

## Config (TOML, every key required, no defaults, unknown keys refused)

- `keypair_credential`: file name inside `$CREDENTIALS_DIRECTORY` holding the
  Solana JSON-array keypair. The key is never read from env or argv.
- `mint`: the only token mint the signer will move.
- `decimals`: the mint's decimals; a different value in a request is rejected.
- `pause_file`: absolute path; if it exists, nothing is signed, including when it
  appears while a signer waits for the ledger lock.
- `ledger_path`: absolute path of the sqlite ledger (created 0600) of issued
  signatures.
- `max_amount_per_transfer`, `max_amount_per_recipient_24h`,
  `max_amount_total_24h`, `max_transfers_24h`: positive integers in raw base units
  (a count for the last one).

## systemd example

    [Service]
    Type=oneshot
    DynamicUser=yes
    LoadCredential=keypair:<keypair-file>
    StateDirectory=payout-signer
    NoNewPrivileges=yes
    ProtectSystem=strict
    PrivateNetwork=yes
    ExecStart=<install-dir>/payout-signer sign --request <request-file> --output <output-file> --config <config-file>

Set `ledger_path` and `pause_file` under `/var/lib/payout-signer/` (the
`StateDirectory`) and `keypair_credential = "keypair"`. `PrivateNetwork=yes` is
safe because the signer needs no network. The request and output files must be
reachable from the unit (for example under the state directory, or via
`ReadWritePaths=`).

## Pause and resume

Pause: create the file named by `pause_file`. Resume: delete it. Pausing does not
touch the ledger, and replays are refused while paused.

## Development

    uv sync
    uv run pytest
    uv run ruff check . && uv run ty check src
