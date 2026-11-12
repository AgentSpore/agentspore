2026-11-06 · Stages #187–#189 installed; #190 integration in progress · AgentSpore maintainers

# Payout receipts, immutable intents and leases

The no-argument command compares an explicit payout intent with a trusted Solana RPC response without writing data. The `submit` and `show` commands store and read immutable intents in PostgreSQL. Claim commands coordinate exact intent UUIDs with retained epochs; they do not send payments. A matching receipt describes the trusted response; it does not independently prove a real payment, create an entitlement, or send funds. Automatic activity accrual and the durable sender remain incomplete.

| When | Input | Output | Excluded |
|---|---|---|---|
| Verify a receipt | One JSON object on stdin | One JSON verdict on stdout | Signing, broadcast, database queries or updates |

From `backend`, use the configured backend environment:

```sh
uv run --frozen python -m app.cli.payout_delivery < intent.json
```

R187.1: All fields are required: `genesis_hash`, `mint`, `program`, `decimals`, `raw_amount`, `source`, `destination`, `authority`, `fee_payer`, `recipient`, `signature`, `rpc_endpoint`. Unknown fields are rejected. `program` must identify Token-2022. Raw units must be a canonical decimal string in `1..18446744073709551615`; no UI conversion occurs.

R187.2: The command verifies `getGenesisHash`, then requests `getTransaction` with `finalized`, `json` and `maxSupportedTransactionVersion: 0`. The supported subset is a legacy transaction containing one direct `TransferChecked`, existing source/destination accounts, complete pre/post token and lamport balances, and an explicitly empty `innerInstructions`. The authority owns the source; payer and authority may coincide. Account positions are resolved from the compiled message; header counts must be valid unsigned bytes within account bounds, and `recentBlockhash` must be present and syntactically valid. The blockhash is not compared with an expected attempt blockhash. Only the transaction fee may change lamports. This is shape A; the two-instruction shape B, which first creates the recipient's account, is accepted under the rules in [PAYOUT_RECIPIENT_ATA.md](PAYOUT_RECIPIENT_ATA.md#receipt-reconciliation-backendappservicespayout_delivery_servicepy).

R187.3: Outside shape B, additional instructions, CPI, delegate/multisig authority, versioned transactions, transfer-fee deltas and incomplete identities never match. Exact raw debit and credit are required. No current-wallet lookup or deposit-specific rule is used. Compatibility with a real mint's extensions is unverified.

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

## Exact-intent leases (issue #189)

R189.1: A privileged backend operator supplies a committed intent UUID and a worker UUID. Worker identity coordinates processing; it does not authenticate a signer. The existing CLI adds these JSON commands without changing receipt verification or immutable admission.

| Command | Required JSON fields | Successful verdict |
|---|---|---|
| `claim` | `intent_id`, `worker_id`, `ttl_seconds` | `claimed` |
| `renew` | `intent_id`, `worker_id`, `epoch`, `ttl_seconds` | `renewed` |
| `release` | `intent_id`, `worker_id`, `epoch` | `released` |
| `recover` | `intent_id`, `worker_id`, `epoch` | `recovered` |
| `lease-show` | `intent_id` | `found` |

R189.2: UUIDs are required; epoch is a strict integer from 1 to 9223372036854775807, TTL from 1 to 300 seconds. TTL is an operational lease duration, not reward cadence. Unknown fields, bools and floats are rejected. Success exits 0 and returns only `verdict`, `intent_id`, `state`, `epoch`, `worker_id` and UTC ISO8601 `expires_at`; ready rows have null worker/expiry. Use `lease-show` after a lost claim response to recover the persisted fence.

R189.3: Initial claim is epoch 1; a new claim from ready increments it, including for the same worker. Claimed rows are unavailable even when expired. Renew/release require the exact live fence; recover requires the exact expired fence. Renew never shortens or revives a lease. The server clock governs expiry. Mutators lock the exact immutable parent before the child; independent intents do not share a slot. Ordinary invalid transitions and deletion/truncation are rejected; database owners can alter these protections.

R189.4: Each operation installs transaction-local lock_timeout=250ms and statement_timeout=1000ms. These bound individual database waits/statements, not connection setup or total process duration. Success is emitted after commit. Error output contains only a verdict: invalid_input=5, not_found=6, storage_error=8, busy=9, stale=10, unavailable=11. Existing conflict=7 remains. Service/repository callers own rollback; the configured backend bootstrap prerequisite still applies.

R189.5: The lease-only stage adds no signed attempt or payment confirmation. The following #190 stage atomically retains an offline signed attempt before any future broadcast. Current release/recover filter only claimed. Backlog selection, signing configuration, sender, reconciliation and automatic rewards remain incomplete. Synthetic verification evidence stays outside Git; it does not establish any real payout or signed-attempt guarantee.

R189.6: The additive claim migration is `db/migrations/V112__payout_delivery_claims.sql`; the installed V111 intent table and its repeatable guards are retained. Application rollback retains the child relation and its monotonic generations. PostgreSQL16 defines [the advancing server clock](https://www.postgresql.org/docs/16/functions-datetime.html) and [row-lock NOWAIT behavior](https://www.postgresql.org/docs/16/sql-select.html); relation-lock waits additionally use the stated local timeouts.

## Offline message preparation and signed admission (issue #190)

R190.1: This explicitly offline milestone compiles an exact legacy Token-2022 TransferChecked message, accepts a complete externally signed envelope, and commits its durable prepared barrier. It does not complete the original configured sign-only provider stage. No actual provider, custody configuration, network lookup, broadcast or automatic reward policy is introduced.

| Command | Input | Ordinary success |
|---|---|---|
| `prepare-message --output PATH` | Exact fence, genesis_hash, recent_blockhash, last_valid_block_height | `compiled`, identity/fence and message_digest |
| `prepare-commit` | format_version=1, exact fence, genesis_hash, last_valid_block_height, signed_transaction_base64 | `prepared` or `replayed`, identity/fence and transaction_digest |
| `attempt-show` | intent_id | `found`, the persisted prepared identity/fence and transaction_digest |

R190.2: The exact fence is intent_id, worker_id and strict positive BIGINT epoch. Height is a canonical unsigned decimal string from 0 through 18446744073709551615. Format version must be integer 1, never bool/float. Unknown fields are forbidden. The existing JSON size bound, configured-backend prerequisite and command exit codes remain. Invalid/unsupported or parent-mismatched candidates return invalid_input=5; only a valid different retained identity returns conflict=7.

R190.3: Compile reads the real immutable intent and live claimed fence without changing either. Its signing request contains the original context/fence, format_version, unsigned_message_base64 and SHA256 message_digest. The explicitly selected artifact is created exclusively with mode 0600; existing paths and symbolic links are never overwritten. Account-bearing message bytes travel through that private file, never normal stdout/stderr. A compile result does not guarantee that its lease remains live through external signing.

R190.4: Admission requires canonical base64, a 1..1232-byte native legacy transaction, native sanitization and verification of every Ed25519 signature. Exact message bytes are rebuilt with installed SPL/solders from the parent and embedded blockhash. Extra instructions/accounts, nonlegacy versions, partial signatures or changed transfer fields cannot pass. Recipient ownership, account programs/extensions, funds, and signing authority are not proven by these bytes. Genesis and height remain caller assertions, not authenticated network/lifetime evidence.

R190.5: The parent-first bounded lock precedes full parent binding and existing-attempt comparison. A new attempt and the exact live claimed-to-prepared transition commit together. Deferred foreign-key and pair guards reject either incomplete half and mismatched fences, including direct SQL. Prepared retains the old expiry only as provenance; it has no outgoing transition. Old claim/release/recover/renew operations cannot reset it after expiry. Attempt identity, complete bytes, derived first signature/blockhash and asserted height are immutable; first signature is conservatively globally unique.

R190.6: Exact retained-envelope replay remains available after expiry and never creates a replacement. After an uncertain acknowledgement, retry the exact envelope or use attempt-show. Normal output excludes signed/unsigned bytes, first signature, accounts, height, paths, SQL and exception details. Success follows commit; busy/storage errors roll back both halves. A future controlled broadcaster must load the committed original bytes and signature; a timeout, uncertain expiry or null receipt does not establish nonpayment or authorize re-signing.

Offline preparation requires independent current-source verification. No configured signing provider or broadcast is included.

## Attempt reconciliation and stored confirmation (issue #182)

R182.1: `attempt-reconcile` checks one persisted prepared attempt against a finalized on-chain receipt and stores the confirmation. `confirmation-show` reads it back. Neither signs, sends, credits a reward or touches balances or the legacy payout tables; a confirmation is a delivery fact, not an entitlement.

| Command | Input (stdin JSON) | Ordinary success |
|---|---|---|
| `attempt-reconcile` | intent_id, trusted_genesis_hash, rpc_endpoint | `confirmed` or `replayed`: intent_id, state, transaction_digest, finalized_slot, verifier_version |
| `confirmation-show` | intent_id | `found`, the same stored fact; no RPC call |

R182.2: The caller supplies no amount, account, recipient, signature or bytes; unknown fields are rejected. These come from the persisted intent and attempt, so without an attempt (`not_found`) no RPC call is made. `rpc_endpoint` must be https (plain http only for loopback hosts) and must carry no credentials, query or fragment. The stored bytes are re-verified natively before any request.

R182.3: Verdicts and exit codes: confirmed/replayed/found=0, unresolved=2, unsupported=3, failed=4, invalid_input=5, not_found=6, conflict=7, storage_error=8, busy=9. Only a finalized receipt with no error status, complete matching token balances, program, owners, raw deltas, fee-only lamports (shape B: fee plus the rent of the created account), the exact compiled message and all signatures equal to the stored wire is stored. `failed`, `unsupported`, `unresolved` and a null receipt store nothing and never authorize a replacement. A wrong trusted network is `unsupported`.

R182.4: Network requests run outside any database transaction. A short transaction then takes the parent lock (NOWAIT, bounded timeouts), re-reads the attempt and inserts once. Identical evidence reports `replayed`; different evidence reports `conflict` and never overwrites. A later null, error or failing re-check is reported as that fresh verdict and leaves the stored confirmation untouched. After a lost acknowledgement use `confirmation-show` or repeat the command. Output excludes bytes, signatures, accounts, endpoints and RPC bodies.

R182.5: Table `payout_delivery_confirmations` (V117) rejects UPDATE, DELETE and TRUNCATE and checks the u64 slot, version and digest shape. These guards do not prove that the RPC was honest or that the verifier ran; the writer and the RPC endpoint are a trusted application boundary.

R182.6: Live-use blocker: the trusted network hash and the RPC endpoint must come from approved server configuration, not from operator or caller JSON, since two values from one caller prove no trust. Until that source exists, only fixtures and synthetic data are verified; no live RPC or funds have been exercised.

R182.7: `attempt-send` broadcasts the exact committed signed transaction of a prepared attempt. Input (stdin JSON) is the same as `attempt-reconcile`: intent_id, trusted_genesis_hash, rpc_endpoint, with the same endpoint rule and no unknown fields. It never builds, alters or re-signs a transaction, and it never writes to the database or marks anything paid; only `attempt-reconcile` stores a confirmation.

| Verdict | Exit | Meaning |
|---|---|---|
| `sent` | 0 | RPC accepted the stored wire and returned the stored first signature |
| `already_processed` | 0 | RPC reports the signature was already processed; success-equivalent, confirm with `attempt-reconcile` |
| `unresolved` | 2 | Timeout, transport failure, unknown blockhash, malformed or any ambiguous reply; nothing is proven |
| `unsupported` | 3 | Trusted or reported network differs from the intent's, the mint fails the pre-send check, or the returned signature differs from the stored one |
| `preflight_rejected` | 13 | The RPC rejected the transaction in preflight. It proves neither execution nor non-execution, never authorizes re-signing or a replacement transaction, and differs from reconcile's `failed` (a finalized on-chain error); next step is `attempt-reconcile` |
| `invalid_input` / `not_found` / `storage_error` / `busy` | 5 / 6 / 8 / 9 | As in R182.3; stored bytes that fail native re-verification are `storage_error` |
| `already_confirmed` | 12 | A confirmation already exists; no RPC call is made |

The persisted attempt is read in a short transaction that is closed before any request. The stored bytes are re-verified natively, then `getGenesisHash` must equal the trusted hash, which must equal the intent's; a mismatch sends nothing. Before sending, `getAccountInfo` on the mint (`jsonParsed`, `finalized`) must show an account owned by the Token-2022 program, of type mint, with the intent's decimals and only the extensions `metadataPointer` and `tokenMetadata`. Any other extension (transfer fee, transfer hook, confidential transfer, non-transferable, pausable or unknown) gives `unsupported` and nothing is sent; a malformed or missing answer gives `unresolved`. Destination-account checks are out of scope; a missing destination surfaces in preflight. `sendTransaction` carries the stored wire as base64 with `skipPreflight=false`, `preflightCommitment=finalized` and `maxRetries=0`. Requests do not use environment proxy settings. Output is only the verdict; bytes, signature, accounts, endpoint and RPC error text are never printed. Any verdict other than `already_confirmed` leaves a later retry of the same stored bytes allowed; `preflight_rejected` and `unresolved` never authorize a replacement transaction.

## Server-configured automation commands (issue #182)

The `auto-*` commands take no network data from the caller. The RPC endpoint, trusted genesis hash, signer public key and source token account come from a private JSON file whose path is the setting `PAYOUT_NETWORK_CONFIG_PATH`. An empty setting, a missing file, an unknown key, a file that is not a regular file, or a file readable by group or other (mode `& 0o077`) gives `configuration_error` (exit 14), and no value from the file is ever printed. Keys (no others accepted): `rpc_endpoint` (same https rule as R182.2), `trusted_genesis_hash`, `signer_pubkey`, `source_token_account`, all required, and the optional boolean `create_recipient_ata` (default `false`). With it on, `auto-compile` first checks the signer's SOL (`insufficient_sol`, exit 15) and builds the two-instruction shape that creates the recipient's account; the receipt check above and `attempt-reconcile` accept that shape under the rules in [PAYOUT_RECIPIENT_ATA.md](PAYOUT_RECIPIENT_ATA.md), whatever the flag.

| Command | Input | Behaviour |
|---|---|---|
| `auto-compile --output PATH` | stdin JSON: intent_id, worker_id, epoch | The persisted transfer must use the configured genesis, signer (as authority and fee payer) and source account, else `unsupported`. The live `getGenesisHash` must equal the trusted hash, then a finalized `getLatestBlockhash` is fetched and the existing compile runs. The signing request is created exclusively with mode 0600; an existing path is refused (`invalid_input`). A stale fence is `stale` and writes no file. |
| `auto-send` | stdin JSON: intent_id | Same as `attempt-send`, network from the file. |
| `auto-reconcile` | stdin JSON: intent_id | Same as `attempt-reconcile`, network from the file. |
| `block-height` | stdin JSON: `{}` (any key is `invalid_input`) | The live `getGenesisHash` must equal the trusted hash (else `unsupported`), then `getBlockHeight` is read with finalized commitment and printed as `{"verdict":"found","height":N}`. A height that is not an integer in the unsigned 64-bit range, or any transport or RPC failure, is `unresolved`. The height is a necessary, not a sufficient, condition for a new signature: a caller may compile and sign a new request only when `attempt-show` is `not_found` (no attempt was persisted, so no wire was ever broadcast, because only `auto-send` broadcasts and only persisted bytes) **and** this finalized height exceeds the previous unpersisted request's `last_valid_block_height`. A persisted attempt is never re-signed (R182.3, R190.6). The configured RPC is a trusted party for this height; a dishonest node can only affect requests that were never broadcast. The endpoint is never printed. |
| `pending --limit N` | none (N from 1 to 1000) | `{"verdict": "found", "intents": [{"intent_id", "phase"}]}`; phase is `unclaimed`, `claimed`, `expired` (a claimed lease past its expiry, no attempt yet), `prepared` or `confirmed`. Confirmed intents are listed last. No amounts or wallets. |

Existing commands and exit codes are unchanged. Output contains only verdicts, identifiers, phases and, for `block-height`, the finalized height.
