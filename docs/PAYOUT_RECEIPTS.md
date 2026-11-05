2026-11-05 · CLI contract · Issues #187 and #188

# Payout receipts and immutable intents

The no-argument command compares an explicit payout intent with a trusted Solana RPC response without writing data. The `submit` and `show` commands store and read immutable intents in PostgreSQL. A matching receipt describes the trusted response; it does not independently prove a real payment, create an entitlement, or send funds. Automatic activity accrual and the durable sender remain incomplete.

| When | Input | Output | Excluded |
|---|---|---|---|
| Verify a receipt | One JSON object on stdin | One JSON verdict on stdout | Signing, broadcast, database queries or updates |

From `backend`, use the configured backend environment:

```sh
uv run --frozen python -m app.cli.payout_delivery < intent.json
```

R187.1: All fields are required: `genesis_hash`, `mint`, `program`, `decimals`, `raw_amount`, `source`, `destination`, `authority`, `fee_payer`, `recipient`, `signature`, `rpc_endpoint`. Unknown fields are rejected. `program` must identify Token-2022. Raw units must be a canonical decimal string in `1..18446744073709551615`; no UI conversion occurs.

R187.2: The command verifies `getGenesisHash`, then requests `getTransaction` with `finalized`, `json` and `maxSupportedTransactionVersion: 0`. The supported subset is a legacy transaction containing one direct `TransferChecked`, existing source/destination accounts, complete pre/post token and lamport balances, and an explicitly empty `innerInstructions`. The authority owns the source; payer and authority may coincide. Account positions are resolved from the compiled message; header counts must be valid unsigned bytes within account bounds, and `recentBlockhash` must be present and syntactically valid. The blockhash is not compared with an expected attempt blockhash. Only the transaction fee may change lamports.

R187.3: Additional instructions, CPI, delegate/multisig authority, versioned transactions, transfer-fee deltas and incomplete identities never match. Exact raw debit and credit are required. No current-wallet lookup or deposit-specific rule is used. Compatibility with a real mint's extensions is unverified.

| Verdict | Exit | Meaning |
|---|---|---|
| `matching` | 0 | Requested receipt matches the supported shape |
| `unresolved` | 2 | Missing receipt, RPC failure, malformed or incomplete evidence |
| `unsupported` | 3 | Network/identity/delta mismatch or unsupported shape |
| `failed` | 4 | Requested receipt reports a transaction error |
| `invalid_input` | 5 | Input fails validation |

R187.4: Output contains only `{"verdict":"..."}`; input, endpoint, RPC error values and tracebacks are not printed by the command. HTTP requests have a 10-second timeout and do not use environment proxy configuration. Null receipts and transport failures do not prove nonexecution.

The existing `app.schemas` package eagerly loads backend configuration. A configured backend environment is therefore required even though the receipt-only command performs no database queries. For synthetic local QA only, the public placeholder `SECRET_KEY=ci-placeholder-value` satisfies that existing prerequisite; never use it in a deployment.

Reproduce synthetic QA from `backend`: `SECRET_KEY=ci-placeholder-value uv run --frozen python -m pytest -q tests/test_payout_delivery_cli.py`. Fixtures use installed SPL/solders message builders; tests exercise the actual CLI/service and real subprocesses against a loopback HTTP server, including private-input rejection. Synthetic receipts are not production chain evidence.

Current verification uses the command above and a narrow guard mutation against the restored source. Generated QA evidence is kept outside Git; historical test counts do not certify this integration. No database, public RPC, wallet or real payment is exercised.

Primary RPC contracts: [getTransaction](https://solana.com/docs/rpc/http/gettransaction) defines commitment, encoding, transaction-version negotiation and null results; [getGenesisHash](https://solana.com/docs/rpc/http/getgenesishash) identifies the connected cluster. Both were checked on 5 November 2026.

## Durable allocation intake (issue #188)

R188.1: `submit` admits a new, explicit transfer without signing or sending funds. From `backend`, run `uv run --frozen python -m app.cli.payout_delivery submit < allocation.json`; use `show < allocation-key.json` to read its identity in a fresh transaction. The process consumes the existing `DATABASE_URL` setting. The configured backend environment prerequisite above still applies. Existing eager settings validation can fail before this command module finishes importing; those bootstrap failures are outside the finite command-handler error boundary.

| Command | Required input | Successful output | Excluded |
|---|---|---|---|
| `submit` | All transfer fields from R187.1, replacing signature/endpoint with `producer_namespace` and `allocation_id` | `accepted` or `replayed`, generated `intent_id`, `state=admitted` | Reward rules, approval, signature, RPC, broadcast |
| `show` | Only namespace and allocation ID | `found`, the same identity and state | Transfer payload disclosure |

R188.2: Namespace matches `^[a-z][a-z0-9._-]{0,63}$`; allocation ID matches `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`. Both are case-sensitive technical identifiers, with no normalization or business activity deduplication policy. Signature, endpoint and unknown input fields are forbidden. Existing strict Token-2022 transfer validation and canonical positive u64 strings apply.

R188.3: Equal identity and all equal transfer fields replay the stored UUID; a changed valid field conflicts without overwriting. Different namespaces or allocation IDs are independent. `admitted` means durable intake only. The CLI emits success after commit; service/repository callers own their transactions. PostgreSQL uses a unique identity, conflict-safe insertion and a separate committed-winner lookup. Immutable triggers reject ordinary update, delete and truncate; database owners can change those protections.

R188.4: Exit codes are 0 for accepted/replayed/found, 5 for invalid input, 6 for not found, 7 for conflict and 8 for storage/configuration/commit errors. Output contains only `verdict` and, on success, `intent_id`/`state`. SQL, input values, connection URLs and exception details are withheld. Existing no-argument receipt verification remains available. Legacy payout rows, reward policy, automatic producers and payment execution are outside this stage.

R188.5: The additive table migration is `db/migrations/V111__payout_delivery.sql`; immutability functions and triggers are installed by `db/migrations/R__payout_delivery_identity.sql`. Existing receipt-only applications ignore this table. Application rollback retains admitted data and the new table; no automatic drop or down migration is provided.

Reproduce synthetic PostgreSQL intake QA with `SECRET_KEY=ci-placeholder-value uv run --frozen python -m pytest -q tests/test_payout_delivery_db.py`; preserve generated evidence outside Git. These checks do not establish entitlement, live chain activity or production mint compatibility. PostgreSQL 16 documents the [separate READ COMMITTED command snapshots](https://www.postgresql.org/docs/16/transaction-iso.html) and [conflict-safe insertion and RETURNING](https://www.postgresql.org/docs/16/sql-insert.html).
