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

## Changing the account email

`POST /api/v1/users/me/email/change` `{new_email, current_password}` sends a single-use link to the new address (`{frontend_url}/confirm-email-change#token=...`; the token sits in the fragment so mail previews and servers never see it). Password accounts must send the current password; OAuth-only accounts must hold a current email proof. Disposable domains get 422. A taken, unchanged or usable address all get the same 202 body, so the endpoint does not reveal which accounts exist. One request per cooldown window per user (429 after).

`POST /api/v1/users/me/email/change/confirm` `{token}` (authenticated, single-use, same TTL as email verification) switches the email, records a fresh `email_link` proof for it and notifies the old address. The pending change reuses the `verification_*` columns with purpose `email_change`; the old address stays active until confirmation. The database trigger clears the previous proof, so an existing reward decision, bound to the old proof, no longer matches: the welcome grant returns to risk review and its beneficiary key never changes.
