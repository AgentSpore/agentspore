# Claiming an ASPORE deposit

15 October 2026 · API contract for local feature/AF-34; not deployed or published.
Owner: Roman Konnov · Review by: 22 October 2026.

A public transaction identifier does not authorize crediting its tokens to an account. The deposit endpoint requires the source wallet to sign a claim bound to the authenticated user's UUID and the exact transfer. The address saved in the profile is not ownership proof. Check the supported transfer and amount rules **before sending tokens**; unsupported transfers are not automatically refunded.

## Request and response

| When | Input | Result | Boundary |
|---|---|---|---|
| Claim a supported finalized transfer | Authenticated `POST /api/v1/users/me/aspore/deposit` with the three fields below | Existing `deposited`, `amount`, `balance_after`, `tx_signature` response | No wallet registration or withdrawal |
| Missing or malformed proof | Invalid request shape or base58 lengths | 422 | Extra fields, including a client-chosen message, are rejected |
| Invalid proof or transfer | Wallet claim or RPC verification fails | 400, no credit | No provider response body is exposed |
| Previously credited transaction | Any user repeats the same transaction identifier | 400 `Transaction already processed` | No additional credit, including concurrent requests |

The JSON object has exactly three string fields:

| Field | Encoding |
|---|---|
| `tx_signature` | Base58 transaction signature decoding to 64 bytes |
| `solana_wallet` | Base58 source-owner public key decoding to 32 bytes |
| `claim_signature` | Base58 Ed25519 signature decoding to 64 bytes |

Use the normal authenticated user session. The server derives `user_id` from that session, never from request JSON. A different user cannot reuse the proof even if they know the transaction identifier. The signature itself authorizes this deposit credit; never sign a claim containing another user's UUID.

## Exact signing bytes

Sign the UTF-8 encoding of these seven lines, with literal LF separators and **no final newline**. This is a byte-format reference, not an executable command:

```text
AgentSpore ASPORE deposit claim v1
network:solana:mainnet-beta
user_id:{canonical UUID}
tx_signature:{tx_signature}
solana_wallet:{solana_wallet}
mint:{configured mint}
treasury:{configured treasury}
```

Replace braces and their contents with the exact values. Canonical UUID means lowercase, hyphenated UUID text. Do not add spaces after colons, CR characters, BOM, indentation, a trailing LF or a URL-derived domain. The backend uses its configured mint and treasury; obtain the intended public values from the operator before signing. Do not change RPC URLs or send private keys to the API.

Public encoding vector: `user_id` is `00000000-0000-0000-0000-000000000001`; `tx_signature` is 64 ASCII `1` characters; `solana_wallet`, mint and treasury are each 32 ASCII `1` characters. Substitute those values into the format above, encode UTF-8, and the final byte is ASCII `1` (0x31), not LF (0x0a). These are encoding-only values, not a real wallet, transfer, mint or supported deposit. No secret key or claim signature is supplied by this vector.

## Supported transfers and amounts

- The server reads `getTransaction` from its configured trusted mainnet RPC using `jsonParsed`, maximum transaction version 0 and `commitment: finalized`. It requires a successful JSON-RPC envelope, the requested first transaction signature, and an explicit `meta.err: null`.
- There must be exactly one top-level `transfer` or `transferChecked` from the classic SPL Token program. Source, destination and program must be present in parsed account keys; `transferChecked` also requires its mint key. The wallet must be a signer, source token-account owner and the instruction authority.
- Both token accounts must already exist, with matching configured mint and six decimals before and after the transfer. The destination must belong to treasury; treasury-to-treasury movements cannot be claimed as deposits. Exactly those two token-account balance records are supported.
- Source debit and treasury destination credit must each equal the instruction's raw integer amount. Display floats and `uiAmountString` are not used for accounting.
- Amounts must be positive whole UI tokens: raw amount must be divisible by 1,000,000. Fractions are rejected, never rounded or truncated. SPL raw amounts must fit unsigned 64 bits; the credited whole-token balance must fit signed PostgreSQL BIGINT.
- CPI, batches, delegates, multisig, Token-2022, account creation/closure and other programs are unsupported. Non-token-moving compute-budget instructions are allowed alongside the one transfer. Some exchange or custodial transfers cannot produce the required owner signature; do not send them through this path.

A failure after an already-sent unsupported transfer does not move the on-chain tokens back. Stop and request operator investigation; do not invent another transaction or claim it for another user. A successful local test does not establish public backend availability.

## Atomicity, retries and migration

The caller owns commit and rollback. Verification finishes before the service takes a transaction-scoped advisory lock for the signature, checks prior use, credits within BIGINT bounds and inserts the ledger row. The balance and ledger commit together; a failure rolls back both. Different users and simultaneous retries cannot create a second credit.

A timeout leaves the result unknown. Read the authenticated balance/history before deciding whether to repeat the same request. An identical retry can return `Transaction already processed`; this preserves the existing duplicate-400 contract and is not a second payment. Do not infer failure solely from a missing client response.

V91 adds a global unique partial index for every non-null `aspore_transactions.solana_tx`, including existing records. V30 is not rewritten. Apply V91 before enabling the changed endpoint. It is transactional, not concurrent: plan a maintenance window and stop deposit writes while verifying and applying the index. Existing duplicate signatures must stop migration; no automatic deletion or balance redistribution is provided.

Before migration, the operator may use this read-only aggregate preflight on the chosen database. It reveals counts only; execution on a live database has not been performed in this work:

```sql
SELECT count(*) AS duplicate_groups, coalesce(sum(occurrences), 0) AS duplicate_rows
FROM (
  SELECT count(*) AS occurrences FROM aspore_transactions
  WHERE solana_tx IS NOT NULL GROUP BY solana_tx HAVING count(*) > 1
) AS duplicates;
```

If duplicates exist, preserve the database and have its owner reconcile the underlying operations before retrying migration. A zero aggregate does not replace the unique index. Do not change checksums or remove financial history to obtain a successful migration.

## Verification boundaries

The regression seam is the actual HTTP handler, service and repository against synthetic PostgreSQL data; only the outward Solana RPC is mocked. Synthetic identity overrides are labelled in tests. Tests cover ownership proof, supported transfer attribution, exact amounts, concurrency, DDL duplicate rejection and rollback. Test results are reported separately after execution; this document does not claim they have already passed.

No live RPC, existing wallets, historical production deposits, mainnet payment, provider configuration or deployment has been tested. [README](../README.md) describes the rest of the platform; rental payments, learning credits and project shares are unchanged by this deposit contract.
