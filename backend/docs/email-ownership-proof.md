# Current email ownership proof

2026-11-08 · API contract · Owner: AgentSpore maintainers · Review by: 2026-12-08.

Email ownership proof records when and how the current mailbox was verified. It is separate from the legacy `is_verified` login flag. This API neither credits rewards nor sends tokens. Email ownership does not establish that accounts belong to distinct people.

## Request and confirm

An authenticated user whose account already permits sign-in calls `POST /api/v1/auth/email-proof/request` without a body. A successful response is HTTP 202. The existing email service sends a random single-use code to the account's stored email. The API never returns that code. Repeated requests within the configured resend cooldown return HTTP 429.

The same authenticated user submits `POST /api/v1/auth/email-proof/confirm` with JSON `{"code":"<code from the email>"}`. HTTP 200 confirms current email ownership. HTTP 400 means the code is invalid, expired or already used. Mail, Redis or proof-transaction database failures return HTTP 503 without exposing their details. Neither endpoint returns access or refresh tokens. A new request replaces the previous pending challenge.

This first version is API-only; there is no new browser confirmation page. Messages contain a code rather than a link to a missing interface. The existing registration verification page and sign-in behavior remain compatible.

## Proof and invalidation

New email-bound registration challenges and trusted verified Google/GitHub callbacks record proof. A provider callback only proves the email when it matches the current stored address. Older registration links without a captured email can still complete sign-in, but do not create proof suitable for rewards. Historical verified users receive no automatic proof backfill.

Changing the email invalidates proof and any pending challenge in PostgreSQL, including changes made directly through SQL. Changing the address back does not restore proof. Reverification codes have a distinct purpose and cannot be redeemed as registration or password-reset tokens. Proof consumption uses the authenticated user, database row lock, current email, expiration and single-use Redis key.

Deployment requires V114 and the `R__email_ownership_proof.sql` repeatable migration before the new backend. Existing `is_verified` semantics remain unchanged. Do not drop proof columns when rolling back application code; no financial program is activated by this migration.
