2026-11-05 · Read-only CLI contract · Issue #187

# Payout receipt verification

This read-only command compares an explicit payout intent with a trusted Solana RPC response. A matching result describes that response; it does not independently prove a real payment, create an entitlement, or send funds. Automatic activity accrual and the durable sender remain incomplete.

| When | Input | Output | Excluded |
|---|---|---|---|
| Run once | One JSON object on stdin | One JSON verdict on stdout | Signing, broadcast, database queries or updates |

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

The existing `app.schemas` package eagerly loads backend configuration. A configured backend environment is therefore required even though this command performs no database queries. For synthetic local QA only, the public placeholder `SECRET_KEY=ci-placeholder-value` satisfies that existing prerequisite; never use it in a deployment.

Reproduce synthetic QA from `backend`: `SECRET_KEY=ci-placeholder-value uv run --frozen python -m pytest -q tests/test_payout_delivery_cli.py`. Fixtures use installed SPL/solders message builders; tests exercise the actual CLI/service and real subprocesses against a loopback HTTP server, including private-input rejection. Synthetic receipts are not production chain evidence.

Current verification uses the command above and a narrow guard mutation against the restored source. Generated QA evidence is kept outside Git; historical test counts do not certify this integration. No database, public RPC, wallet or real payment is exercised.

Primary RPC contracts: [getTransaction](https://solana.com/docs/rpc/http/gettransaction) defines commitment, encoding, transaction-version negotiation and null results; [getGenesisHash](https://solana.com/docs/rpc/http/getgenesishash) identifies the connected cluster. Both were checked on 5 November 2026.
