# Welcome reward ledger

2026-11-08 · Inactive ledger core · Owner: AgentSpore maintainers · Review before activation.

The welcome program is disabled. This release creates no applications, funding, risk decisions, bonuses or transfers automatically. `GET /api/v1/users/me/rewards/welcome` returns only the authenticated user's status; it never creates a record. Public response shapes are defined in OpenAPI.

| State | Meaning | Amount |
|---|---|---|
| `disabled` | No funded grant; program disabled | Absent |
| `not_applied` | No application; not a promise | Absent |
| `application` | Conditional request awaiting admission or funding | Absent |
| `frozen` | Reserved grant; checks or explicit unlock outstanding | Preserved |
| `available` | Reserved entitlement with current proof and decision | Preserved; not a deposit or payment |
| `denied` | Rejected application | Absent |

`reason` distinguishes program availability, review, funding and email requirements; `next_action` supplies a safe UI action. Disabled programs, stale decisions and changed email never release a funded grant's backing. No cancellation policy for funded grants exists, so changing them to denied is forbidden. The stored available state alone is insufficient: reads recheck current proof and risk evidence and report frozen when either is invalid.

## Internal contract

The service/repository participate in a caller-owned **READ COMMITTED** transaction. Grant mutations and decision insertion reject other isolation levels. A stable program row serializes reserve calculations; repeatable snapshots could otherwise reuse an old aggregate after waiting for a row lock. Lock order is user, program, grant; any future ownership integration must acquire the agent lock first.

The internal `apply(owner)` uses the durable user registration ID as the source event and checks its creation time against explicit admission dates. It is not connected to registration, OAuth or claim routes in this slice. Historical registrations and ownerless agents do not receive automatic records. A future claim adapter must establish its own verified event contract; ownership changes cannot rewrite historical beneficiaries.

`advance(owner)` first freezes an admitted application against available ledger funding. A subsequent call may unlock it after current-email proof matches the latest decision's proof timestamp. The stable program/owner key prevents tariff-version replay. A trusted canonical beneficiary key is also required and unique; an email, wallet string or account ID alone is not proof of a unique person.

Trusted decision producers are not implemented. Their future writes must supply verified server-side evidence, never a user's approval flag. Decision insertion takes the shared locks and assigns a per-grant revision after acquiring them. The largest revision supersedes previous decisions, regardless of identity allocation or issuer timestamps. Original issued/expiry times remain evidence; future or expired decisions fail closed. Evidence and funding rows are immutable. Opaque references must not contain emails, keys or raw risk reports.

Funding entries accept a whole allocation once, with globally unique allocation ID and evidence reference. Conflicting replay is rejected, not overwritten. This prevents duplication inside this ledger; it does not prove external funds/control or absence of other obligations. Funding/control verification, authorization and a trusted anti-abuse source are required before activation. An enable flag alone cannot manufacture funding.

The single welcome program fixes mint, token program and decimals. Integer raw amounts are snapshotted when funded; asset, amount, owner, source, beneficiary and tariff cannot be reassigned. Financial foreign keys restrict deletion. Existing deposits, contribution points, payout intents and on-chain accounts are untouched.

## Deployment and verification

Apply V115 and its repeatable integrity migration before the backend. Application rollback retains the additive tables and triggers; never delete financial history. Only a disabled program is seeded.

Real PostgreSQL tests cover concurrent reservations/applications, rollback, canonical deduplication, immutable history, rejected direct SQL transitions, decision ordering, unsupported isolation, email change/change-back and current read authorization. No real emails, RPC calls, signing or transfers are part of these tests.
