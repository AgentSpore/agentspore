# Otkrytka visit observer

24 October 2026 · M3a source reference, not a production deployment · Owner: AgentSpore maintainers · Review by: 31 October 2026.

`POST /api/v1/_internal/usage/otkrytka/observe` accepts an empty body and no query. It records eligible HTML GET visits through existing PostgreSQL policy. It does not forward application traffic, record useful actions, verify ingress coverage or close #171.

Server settings: `OTKRYTKA_USAGE_PROJECT_ID` and a separate `OTKRYTKA_USAGE_OBSERVER_KEY` (`SecretStr`). Both are optional; absent configuration disables the observer. Agent/admin API keys are not accepted. Provision no real secret through chat or public configuration.

The producer must replace incoming `X-Usage-*` headers with its own values. Required headers: `X-Usage-Observer-Key`, `X-Usage-Request-Id` (UUID4, reused for delivery retries), `X-Usage-User-Agent`, `X-Usage-Accept`, `X-Usage-Method` (GET), `X-Usage-Status` (2xx), `X-Usage-Content-Type` (HTML), `X-Usage-Path-Class` (`document` or `probe`), `X-Usage-Client-IP`. `X-Usage-Visitor-Id` is the extracted existing UUID or empty. Unknown usage headers, duplicate protected headers, oversized headers, transfer encoding and nonempty bodies are rejected generically. No raw path, query, body, organizer token or asserted user identity is accepted.

An optional ordinary platform Bearer token is verified using the existing user lookup, in the same function-scoped transaction as the visit. It goes only to the observer. Missing, expired or invalid-signature proof is anonymous; a signed non-UUID subject fails closed with a generic 503 and no visit; the verified project owner and recognized bots/probes are excluded by existing policy.

Pre-commit processing has a one-second deadline. A 204 acknowledgment is sent only after commit. Deferred transaction errors return a fixed 503 without SQL parameters or raw metadata in logs. A late outbound delivery failure logs only a fixed warning and aborts without a second response or escaped traceback. A post-commit Redis call is separately bounded to 200 ms; an outage produces a fixed warning and preserves the committed acknowledgment and cookie. Invalid producer authorization returns 403; disabled configuration returns 503; malformed transport/metadata returns 400 or 431; missing configured project returns 404.

Valid visitor UUIDs are reused without refreshing their cookie. Missing/malformed IDs use a domain-separated HMAC of the ingress request UUID with the scoped observer key, converted to UUID4. Retries therefore share an identifier without persisting the raw request UUID. The host-only `__Host-visitor` cookie has Secure, HttpOnly, SameSite=Lax, Path=/ and Max-Age=691200. Eligible visits retain only project ID, random visitor ID and UTC date. Address material is stored only as the existing domain/day HMAC in Redis for 24 hours, without extending its TTL.

Before integration, verify the installed Caddy behavior and explicitly remove its generated request UUID from access logs. Authenticated observation is not evidence of complete measurement coverage. Useful-action receipts and a real Otkrytka end-to-end run remain separate work.
