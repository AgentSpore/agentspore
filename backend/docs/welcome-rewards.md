# Welcome reward ledger

2026-11-08 · Inactive ledger core · Owner: AgentSpore maintainers · Review before activation.

The welcome program is disabled. While disabled, onboarding creates no applications. Enabling the program permits eligible applications, but never automatically creates funding, risk decisions, bonuses or transfers. `GET /api/v1/users/me/rewards/welcome` returns only the authenticated user's status; it never creates a record. Public response shapes are defined in OpenAPI.

| State | Meaning | Amount |
|---|---|---|
| `disabled` | No funded grant; program disabled | Absent |
| `not_applied` | No application; not a promise | Absent |
| `application` | Conditional request awaiting admission or funding | Absent |
| `frozen` | Reserved grant; checks or explicit unlock outstanding | Preserved |
| `available` | Reserved entitlement with current proof and decision | Preserved; not a deposit or payment |
| `denied` | Rejected application | Absent |

`reason` distinguishes program availability, review, funding and email requirements. Missing current email proof requires email confirmation; a valid current proof that differs from the trusted decision requires risk review, not another confirmation; `next_action` supplies a safe UI action. Disabled programs, stale decisions and changed email never release a funded grant's backing. No cancellation policy for funded grants exists, so changing them to denied is forbidden. The stored available state alone is insufficient: reads recheck current proof and risk evidence and report frozen when either is invalid.

## Internal contract

The service/repository participate in a caller-owned **READ COMMITTED** transaction. Grant mutations and decision insertion reject other isolation levels. A stable program row serializes reserve calculations; repeatable snapshots could otherwise reuse an old aggregate after waiting for a row lock. Lock order is user, program, grant; any future ownership integration must acquire the agent lock first.

The internal `apply(owner)` uses the durable user registration ID as the source event and checks its creation time against explicit admission dates. New password registrations, newly created OAuth users and confirmed ownership claims attempt this application once per owner. Existing OAuth sign-ins do not apply. Claim retries preserve the same user ID source event and original user creation date; creating or claiming another agent cannot bypass admission dates or award another grant. Historical users outside those dates and ownerless agents receive no applications. Ownership changes cannot rewrite historical beneficiaries.

Optional onboarding runs inside a savepoint in the identity transaction. Core identity changes flush before this boundary. Recoverable ledger errors roll back only the application; the identity operation continues only while its enclosing transaction remains usable. Connection loss and core authentication errors propagate. Ledger lock waits and statements are bounded, and prior timeout settings are restored. Successful applications commit or roll back with the identity transaction.

An authenticated user can explicitly retry with `POST /api/v1/users/me/rewards/welcome/application`. The endpoint accepts no owner or source input, uses the authenticated user and the same admission checks, and returns the existing application on repeat. Database failures, including commit failures, return a sanitized 503. It never advances, funds or unlocks a grant. GET remains read-only; eligible users without an application receive `not_applied`, while historical users outside admission dates receive `not_eligible`.

`advance(owner)` first freezes an admitted application against available ledger funding. A subsequent call may unlock it after current-email proof matches the latest decision's proof timestamp. The stable program/owner key prevents tariff-version replay. A trusted canonical beneficiary key is also required and unique; an email, wallet string or account ID alone is not proof of a unique person.

The trusted operator writer records reviewed attestations through a controlled CLI, never a user's approval flag. Automated decision producers and the process establishing a unique economic beneficiary remain launch prerequisites. Decision insertion takes the shared locks and assigns a per-grant revision after acquiring them. The largest revision supersedes previous decisions, regardless of identity allocation or issuer timestamps. Original issued/expiry times remain evidence; future or expired decisions fail closed. Evidence and funding rows are immutable. Opaque references must not contain emails, keys or raw risk reports.

Funding entries accept a whole allocation once, with globally unique allocation ID and evidence reference. Conflicting replay is rejected, not overwritten. This prevents duplication inside this ledger; it does not prove external funds/control or absence of other obligations. Funding/control verification, authorization and a trusted anti-abuse source are required before activation. An enable flag alone cannot manufacture funding.

The single welcome program fixes mint, token program and decimals. Integer raw amounts are snapshotted when funded; asset, amount, owner, source, beneficiary and tariff cannot be reassigned. Financial foreign keys restrict deletion. Existing deposits, contribution points, payout intents and on-chain accounts are untouched.

## Deployment and verification

Apply V115 and its repeatable integrity migration before the backend. Application rollback retains the additive tables and triggers; never delete financial history. Only a disabled program is seeded.

Real PostgreSQL tests cover concurrent reservations/applications, rollback, canonical deduplication, immutable history, rejected direct SQL transitions, decision ordering, unsupported isolation, email change/change-back and current read authorization. Onboarding tests additionally cover real registration/OAuth/claim routes, disabled and historical admission, repeat claims, concurrent explicit retries, recoverable SQL failure, lock timeout, outer rollback and sanitized commit failure. No real emails, RPC calls, signing or transfers are part of these tests.

## Record an operator decision

2026-11-08 · Operator interface · Owner: AgentSpore maintainers · Review before activation.

Run `uv run python -m app.cli.welcome_reward_decisions` from `backend/` with one JSON
object on standard input. The default validates an existing application's reviewed snapshot,
attempts the append, and rolls the transaction back. Add `--apply` to commit a valid decision.
There is no public endpoint. Execution and database access must be restricted to trusted
operators; `authority_ref` records an attribution and grants no authority itself.

The exact input contract is
[`WelcomeRewardDecision`](../app/schemas/welcome_reward_decisions.py).
Every field is required, including nullable `beneficiary_key` and `email_proof_at`.
Use timezone-aware timestamps, an integer `expected_revision` (zero for the first decision),
and the existing grant, owner and registration source IDs. Input is limited to 8192 UTF-8
bytes; unknown fields, duplicate JSON keys, invalid reference syntax and naive timestamps
are rejected. References are opaque identifiers of at most 200 ASCII characters, beginning
with an alphanumeric character and otherwise containing only letters, digits, `_`, `.`, `:`
or `-`. Syntax does not establish privacy, identity or uniqueness. Never submit email
addresses, credentials, raw risk reports or personal details in these fields.

An `allow` requires a canonical beneficiary obtained through a verified matching process.
An account ID, email address or unverified wallet is not such proof. The writer does not
invent this key or verify the operator's attestation. Hold and deny decisions also require
the reviewed proof snapshot to match: refresh the snapshot after an email change before
submitting a new review. Null proof remains null and cannot unlock a grant.

After locking user, program and grant, the writer checks the globally unique `evidence_ref`.
An exact replay returns the original receipt even after expiry or supersession. Changed
content, including a changed expected revision or another grant, conflicts. A new decision
must match the latest revision, current email proof and applicable policy (the funded
snapshot, otherwise the program tariff). A funded beneficiary cannot be replaced by allow.
The database trigger assigns the next revision. Hold and deny supersede admission without
releasing backing; neither directly changes the grant state.

| Result | Meaning | Exit code |
|---|---|---|
| `validated`, `applied: false` | Dry-run append rolled back; no saved decision or provisional receipt | 0 |
| `recorded`, `applied: true` | Commit completed; `decision_id` and `revision` identify the saved row | 0 |
| `replayed` | Existing receipt; `applied` distinguishes explicit apply from dry-run | 0 |
| `invalid_input` | Invalid input or a new decision outside its validity interval | 5 |
| `not_found` | Owner or application absent | 6 |
| `conflict` | Evidence or grant/beneficiary binding differs | 7 |
| `storage_error` | Configuration, database, commit or cleanup failed; reconcile before retry | 8 |
| `configuration_error` | Settings or environment could not be loaded at start; fix the configuration, nothing was attempted | 9 |
| `stale` | Expected revision, policy or email proof changed | 10 |
| `busy` | A shared reward lock was not granted within 10 seconds (`lock_timeout`); nothing was saved, retry later | 11 |

Errors contain no input references or database details. Only settings/OS failures at start are masked as `configuration_error`; a code defect (for example an `ImportError` after a rename) ends the process with an ordinary traceback that holds code frames only, never input or environment values. A cleanup failure after commit can
return `storage_error` even though the append committed; retry the identical input to recover
its receipt. Dry-run may consume a sequence value, but persists no row. It does not create
applications, enable the program, fund or advance grants, credit deposits, create payouts,
sign transactions, call RPC endpoints or send mail.
