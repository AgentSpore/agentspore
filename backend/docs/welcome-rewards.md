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

## Progress fields for the profile card

`GET /api/v1/users/me/rewards/welcome` keeps `state`, `reason`, `amount_raw`, `decimals`, `asset` and `next_action` unchanged and adds:

- `steps`: `email`, `wallet`, `application`, `review`, `payout`, each with `done`. `wallet` is not done while the bound wallet already has a welcome payout for another grant.
- `hold_reasons`: what stops the reward for this owner (`email_not_confirmed`, `disposable_email`, `wallet_missing`, `wallet_already_used`, `github_too_new`). Empty once a payout exists.
- `payout`: `null` until a payout intent exists, then `{state: pending|sent|confirmed, signature}`. `signature` is null while `pending`; it is the public transaction signature after.
- `wait_until`: set only with `github_too_new`; the date the owner's GitHub account becomes old enough.
- `next_step`: exactly one of `confirm_email`, `change_email`, `bind_wallet`, `apply`, `wait_review`, `wait_payout`, `none`. Order: payout state, then a disposable email, unconfirmed email, wallet, application, then waiting.
- `actions`: the existing endpoints that finish `next_step` inline: `confirm_email` lists `POST /api/v1/auth/email-proof/request` and `.../confirm`; `bind_wallet` lists `PATCH /api/v1/users/solana-wallet` (a 409 means the address belongs to another account); `apply` lists `POST /api/v1/users/me/rewards/welcome/application`. Waits and `change_email` have none: no email-change endpoint exists yet.

The response never includes the beneficiary key, another user's wallet or email, or internal limits.

## Internal contract

The service/repository participate in a caller-owned **READ COMMITTED** transaction. Grant mutations and decision insertion reject other isolation levels. A stable program row serializes reserve calculations; repeatable snapshots could otherwise reuse an old aggregate after waiting for a row lock. Lock order is user, program, grant; any future ownership integration must acquire the agent lock first. The automatic decision command takes a transaction-scoped advisory lock before this order, and no other writer takes it, so it cannot form a cycle with the ledger locks.

The internal `apply(owner)` uses the durable user registration ID as the source event and checks its creation time against explicit admission dates. New password registrations, newly created OAuth users and confirmed ownership claims attempt this application once per owner. Existing OAuth sign-ins do not apply. Claim retries preserve the same user ID source event and original user creation date; creating or claiming another agent cannot bypass admission dates or award another grant. Historical users outside those dates and ownerless agents receive no applications. Ownership changes cannot rewrite historical beneficiaries.

Optional onboarding runs inside a savepoint in the identity transaction. Core identity changes flush before this boundary. Recoverable ledger errors roll back only the application; the identity operation continues only while its enclosing transaction remains usable. Connection loss and core authentication errors propagate. Ledger lock waits and statements are bounded, and prior timeout settings are restored. Successful applications commit or roll back with the identity transaction.

An authenticated user can explicitly retry with `POST /api/v1/users/me/rewards/welcome/application`. The endpoint accepts no owner or source input, uses the authenticated user and the same admission checks, and returns the existing application on repeat. Database failures, including commit failures, return a sanitized 503. It never advances, funds or unlocks a grant. GET remains read-only; eligible users without an application receive `not_applied`, while historical users outside admission dates receive `not_eligible`.

`advance(owner)` first freezes an admitted application against available ledger funding. A subsequent call may unlock it after current-email proof matches the latest decision's proof timestamp. The stable program/owner key prevents tariff-version replay. A trusted canonical beneficiary key is also required and unique; an email, wallet string or account ID alone is not proof of a unique person.

The trusted operator writer records reviewed attestations through a controlled CLI, never a user's approval flag. A unique economic beneficiary is established by the verified email address (one reward per mailbox) and by the payout wallet (one reward per wallet); a GitHub account is not required. Every grant is still decided manually by the operator; a trusted anti-abuse source remains a launch prerequisite.

### Automatic decisions

`python -m app.cli.welcome_reward_decisions auto-decide [--apply]` approves undecided applications that satisfy every rule and leaves all others untouched for manual review; it never rejects, funds, enables the program, signs or sends. Rules: the current-email proof is valid, the beneficiary assessment has no holds, the account is at least the configured number of days old (the GitHub account when one is bound, judged at application time; otherwise the platform account, judged at decision time), the beneficiary is not already approved on another grant, and the rolling 24-hour count of automatic approvals is below the configured cap. Applications that already carry any decision (including manual hold or deny) are never considered.

Settings `WELCOME_AUTO_MIN_ACCOUNT_AGE_DAYS` and `WELCOME_AUTO_DAILY_LIMIT` have no effective default: unset or zero (negative values fail settings validation) makes the command do nothing and print `{"verdict": "disabled"}` with exit code 13 (fail closed). Values live only in server configuration.

Candidates are read without locks, then each approval runs in its own short transaction: advisory lock, recount of the rolling cap, then the manual decision writer (same revision, replay and conflict guards), then commit. The program row is therefore locked only inside one writer call, so onboarding's bounded lock wait is not starved. Dry-run (default) rolls back every transaction. Concurrent runs cannot exceed the cap because the recount happens under the advisory lock. Decisions are marked with `authority_ref` `auto:<rule version>`; the cap counts every `auto:` decision regardless of version. Verdicts: `auto_decided` (exit 0, with `approved`, `left`, `applied`), `disabled` (13), plus the existing `configuration_error`, `busy` and `storage_error`. Decision insertion takes the shared locks and assigns a per-grant revision after acquiring them. The largest revision supersedes previous decisions, regardless of identity allocation or issuer timestamps. Original issued/expiry times remain evidence; future or expired decisions fail closed. Evidence and funding rows are immutable. Opaque references must not contain emails, keys or raw risk reports.

Funding entries accept a whole allocation once, with globally unique allocation ID and evidence reference. Conflicting replay is rejected, not overwritten. This prevents duplication inside this ledger; it does not prove external funds/control or absence of other obligations. Funding/control verification, authorization and a trusted anti-abuse source are required before activation. An enable flag alone cannot manufacture funding.

The single welcome program fixes mint, token program and decimals. Integer raw amounts are snapshotted when funded; asset, amount, owner, source, beneficiary and tariff cannot be reassigned. Financial foreign keys restrict deletion. Existing deposits, contribution points, payout intents and on-chain accounts are untouched.

## Review and approve clean applications

Run inside the backend container with the same entry point as the other decision commands:

- `welcome_reward_decisions review`: table of the oldest undecided applications and applications whose allow decision expired before the grant froze (`HOLDS` shows `expired`) (age, e-mail duplicate, wallet duplicate, masked address, hold codes) and a final `pending=N shown=K clean=M` line. Read-only.
- `welcome_reward_decisions approve-clean [--max N] [--apply]`: records an allow decision (authority `operator:clean-v1`, valid one day) for at most N (default 20) applications with no holds, no e-mail duplicate and an account at least `WELCOME_AUTO_MIN_ACCOUNT_AGE_DAYS` old (the same rule as `auto-decide`; a younger account shows `account_too_young`). An expired allow is superseded by a new revision through the same writer, so the grant never stays stuck. Errors mid-run still report `approved` so far, through the manual decision writer, one transaction each. Without `--apply` it only reports `{"verdict": "approved_clean", "approved": N, "applied": false, ...}` and writes nothing. A re-run skips what is already decided. A missing wallet is a hold, so approval is not wasted before the owner can be paid.
- `welcome_reward_decisions pending-signal`: prints `{"alert": "welcome_pending", "pending": N, "clean": M}` when `N > 0`, nothing otherwise. Counters only. Scheduling: `ops/payout-orchestrator/README.md`.

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

An `allow` requires the canonical beneficiary `email:<sha256 hex>` of the owner, checked by
the writer against the stored verified email, and no hold reason. The key hashes the
normalized address: lower-cased, IDNA domain, any `+tag` dropped from the local part, and for
`gmail.com` / `googlemail.com` the dots dropped and `googlemail.com` mapped to `gmail.com`. Known provider domain aliases collapse to one mailbox: Yandex domains and `ya.ru` / `narod.ru` to `yandex.ru` (a dot in the login equals a hyphen), `me.com` / `mac.com` to `icloud.com`, and `protonmail.com` / `protonmail.ch` / `pm.me` to `proton.me`.
The raw address is never part of the key. Reasons, in order: `github_account_age_unknown`,
`github_account_too_new` (only when a GitHub identity is bound; under 30 days at decision
time), `no_current_email_proof`, `disposable_email_domain` (pinned `disposable-email-domains`
list, matched on the domain, its IDNA form and every parent suffix). A mismatch or any reason returns `ineligible`. Hold and deny are not checked.
Run `python -m app.cli.welcome_reward_decisions assess --user-id <uuid>` to print the
canonical key and reason codes (never the email); it writes nothing. Registration-source
clusters are not assessed. Hold and deny decisions also require
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
| `storage_error` | Database, commit or cleanup failed; reconcile before retry | 8 |
| `configuration_error` | Settings, secret or database URL could not be loaded or parsed at start; fix the configuration, nothing was attempted | 9 |
| `stale` | Expected revision, policy or email proof changed | 10 |
| `assessed` | `assess` result with `beneficiary_key` and `holds`; nothing is written | 0 |
| `ineligible` | Allow beneficiary is not the canonical email key, or a hold reason applies; nothing was saved | 12 |
| `busy` | A shared reward lock was not granted within 10 seconds (`lock_timeout`); nothing was saved, retry later | 11 |

Errors contain no input references or database details. Only settings, OS and database-URL failures at start are masked as `configuration_error`; a code defect (for example an `ImportError` after a rename) ends the process with an ordinary traceback that holds code frames only, never input or environment values. A cleanup failure after commit can
return `storage_error` even though the append committed; retry the identical input to recover
its receipt. Dry-run may consume a sequence value, but persists no row. It does not create
applications, enable the program, fund or advance grants, credit deposits, create payouts,
sign transactions, call RPC endpoints or send mail.

## Link available rewards to payout intents

Run `uv run python -m app.cli.welcome_payouts link [--apply]` from `backend/`. The default is a
dry run that rolls back every item. Candidates are grants of an enabled program with a saved
wallet and no payout intent (namespace `welcome`, allocation id = grant id), whose state is
frozen or available, or application with a current allow decision. They are read in keyset
pages ordered by creation time, so a grant that is skipped never hides a later payable one,
up to 500 grants per run. With `--apply`, each is handled in its own transaction: reservation
is advanced until the reward is available, and the wallet's Token-2022 associated token account
is checked on chain. The chain is queried through the server-side network configuration outside
the database transaction, after one check per run that the endpoint serves the trusted network
(otherwise nothing is linked and the exit code is 9). With `create_recipient_ata` off (the
default) the account must already exist as an initialized account of the program mint owned
by that wallet; no rent is paid, and other grants are skipped as `no_token_account` until the
account appears. With the flag on, a missing account is accepted and the payout creates it,
paid by the hot wallet; an unusable account at that address is skipped as `bad_token_account`
and an unusual wallet as `invalid_wallet`
([PAYOUT_RECIPIENT_ATA.md](../../docs/PAYOUT_RECIPIENT_ATA.md#link-verdicts-backendappserviceswelcome_payout_link_servicepy)). The intent
carries the exact grant amount, the configured signer as authority and fee payer, and the
configured source account.

Output is one JSON object: counts of `linked`, `replayed`, `conflict`, `skipped` by reason, and
intent ids. It never contains wallets, amounts, emails or the endpoint. Linked grants are no longer
candidates, so a rerun does not revisit them; a concurrent linker (or a direct relink) with the
same data yields `replayed`, and one with a different wallet yields `conflict` (exit code 7).
A wallet that already has a welcome payout intent for another grant is skipped as
`wallet_already_rewarded`; no second reward is linked to it.
The stored intent is never overwritten. Configuration failures exit 9, storage failures 8,
bad arguments 5, an unreachable RPC endpoint on the once-per-run network check 2 (the same code
`payout_delivery` uses for a transport failure). A failed lookup of one token account after that
check is counted as `skipped.rpc_error` and the run still exits 0. Exit codes are per command and
differ from `payout_delivery`; a caller should branch on the JSON `verdict`. The command signs and sends nothing.
