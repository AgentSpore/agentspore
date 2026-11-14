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

`POST /api/v1/users/me/email/change` `{new_email, current_password}` returns 202 and mails a single-use link (`{frontend_url}/confirm-email-change#token=...`; the token sits in the fragment so mail previews and servers never see it). Mail is sent after the pending change is committed; a provider failure is logged and never changes the reply. Taken, unchanged and usable addresses get the same 202 body. One request per cooldown window per user (429 after). Disposable domains get 422.

- Password accounts send the current password (403 otherwise); the link goes to the new address.
- Accounts without a password need a current email proof, then change in two steps: the first link goes to the CURRENT address, and only after it is confirmed is the link sent to the new one. A stolen session alone cannot move such an account to an attacker's mailbox. The two tokens have their own purposes (`email_change_old`, `email_change`), their own single-use keys, and cannot stand in for each other.
- While the welcome reward is reserved or allowed and not yet paid out, the request is refused with 409: the grant is bound to the address it was reserved under, and a different address would leave it in review with no way out. After the payout is confirmed the change is open.

`POST /api/v1/users/me/email/change/confirm` `{token}` (authenticated, single-use, same TTL as email verification) either approves the first step or switches the email, records a fresh `email_link` proof and notifies the old address. The pending change reuses the `verification_*` columns; the old address stays active until confirmation. The database trigger clears the previous proof when the email changes.

Known gaps:

- Pending password-reset links (`password_reset:{token}`) are not indexed by user, so a change does not revoke a reset link already mailed to the old address; it expires with its TTL. Sessions (refresh tokens) also stay valid until they expire.
- The confirmation page `/confirm-email-change` belongs to the frontend track and is not part of this change; until it ships the links lead nowhere.
- `users_verification_purpose` (V114) lists only `registration` and `email_proof`; a migration must add `email_change` and `email_change_old` before this ships.
