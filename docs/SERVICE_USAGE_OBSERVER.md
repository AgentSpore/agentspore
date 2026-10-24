# Otkrytka usage observer

24 October 2026 · M3b source reference, not a production deployment · Owner: AgentSpore maintainers · Review by: 31 October 2026.

`POST /api/v1/_internal/usage/otkrytka/observe` accepts an empty body and no query. It records eligible HTML GET visits through existing PostgreSQL policy. It also records scoped board-creation delivery receipts. It does not forward application traffic, verify ingress coverage or close #171.

Server settings: `OTKRYTKA_USAGE_PROJECT_ID` and a separate `OTKRYTKA_USAGE_OBSERVER_KEY` (`SecretStr`). Both are optional; absent configuration disables the observer. Agent/admin API keys are not accepted. Provision no real secret through chat or public configuration.

The producer must replace incoming `X-Usage-*` headers with its own values. Required headers: `X-Usage-Observer-Key`, `X-Usage-Request-Id` (UUID4, reused for delivery retries), `X-Usage-User-Agent`, `X-Usage-Accept`, `X-Usage-Kind` (`visit` or `board_created`), `X-Usage-Method`, `X-Usage-Status`, `X-Usage-Content-Type`, `X-Usage-Path-Class`, `X-Usage-Client-IP`. `X-Usage-Visitor-Id` is the extracted existing UUID or empty. Unknown usage headers, duplicate protected headers, oversized headers, transfer encoding and nonempty bodies are rejected generically. No raw path, query, body, organizer token or asserted user identity is accepted.

An optional ordinary platform Bearer token is verified using the existing user lookup, in the same function-scoped transaction as the visit. It goes only to the observer. Missing, expired or invalid-signature proof is anonymous; a signed non-UUID subject fails closed with a generic 503 and no visit; the verified project owner and recognized bots/probes are excluded by existing policy.

Pre-commit processing has a one-second deadline. A 204 acknowledgment is sent only after commit. Deferred transaction errors return a fixed 503 without SQL parameters or raw metadata in logs. A late outbound delivery failure logs only a fixed warning and aborts without a second response or escaped traceback. A post-commit Redis call is separately bounded to 200 ms; an outage produces a fixed warning and preserves the committed acknowledgment and cookie. Invalid producer authorization returns 403; disabled configuration returns 503; malformed transport/metadata returns 400 or 431; missing configured project returns 404.

Valid visitor UUIDs are reused without refreshing their cookie. Missing/malformed IDs use a domain-separated HMAC of the ingress request UUID with the scoped observer key, converted to UUID4. Retries therefore share an identifier without persisting the raw request UUID. The host-only `__Host-visitor` cookie has Secure, HttpOnly, SameSite=Lax, Path=/ and Max-Age=691200. Eligible visits retain only project ID, random visitor ID and UTC date. Address material is stored only as the existing domain/day HMAC in Redis for 24 hours, without extending its TTL.

Before integration, verify the installed Caddy behavior and explicitly remove its generated request UUID from access logs. Authenticated observation is not evidence of complete measurement coverage. A real Otkrytka end-to-end run remains separate work.

For `visit`, the producer must assert GET, a 2xx HTML response and `document` or `probe`. For `board_created`, it must assert POST, exactly 200, JSON and `board_creation`, matching the pinned application's successful `/api/v1/boards` response. Other combinations fail closed. The producer classifies paths; it never sends the raw board path or content.

Board creation records no visit and sets or refreshes no cookie. Recognized bots and the verified project owner are excluded. PostgreSQL stores only project ID, UTC action day, closed action name and a 32-byte domain-separated HMAC of the ingress UUID using the scoped key. The receipt domain differs from the visitor domain. Concurrent retries of one ingress UUID share one receipt; distinct UUIDs remain distinct. This is delivery deduplication within the eight-day retention window, not business idempotency or exactly-once client delivery. Key rotation changes receipt derivation and must not occur during delivery retries.

The existing purge removes receipts older than today minus seven UTC days while preserving its deleted-visit return count. No public action statistics or new heartbeat fields are introduced. Coverage remains unverified even when receipts exist.

## Optional ingress configuration

The tracked [restored-services configuration](../deploy/restored-services.caddy) wraps only the direct Otkrytka proxy. PodMemory and Pereklichka retain their existing routes. This source configuration is not evidence that production loaded it.

Set `OTKRYTKA_USAGE_OBSERVER_KEY` for Caddy and the backend, and `OTKRYTKA_USAGE_PROJECT_ID` for the backend, through the operator's existing local Compose environment file. Use a separate scoped key. Leave both variables absent to disable observation; do not set the optional project UUID to an empty string. Compose's null environment mappings pass configured values and omit unresolved variables from the container environment. Caddy resolves the key at runtime; a missing or empty key skips delivery without changing application responses.

The producer sends empty POSTs with no query, strips incoming headers before adding the observer contract, and forwards Bearer proof only to the observer. Only GET 2xx HTML and exact POST `/api/v1/boards` 200 JSON responses qualify. Application cookies, status and body remain intact. A private Caddy loopback responder on `127.0.0.1:8081` supplies the fallback acknowledgment; it needs no published port or extra daemon. Observer connection and response-header deadlines are 200 ms, with a 500 ms retry budget. Only a trusted backend 204 with a nonempty cookie may add a visitor cookie. Request UUIDs are removed from service access logs.

Before enabling production, verify the installed Caddy version and loaded routes, validate the configuration, confirm the configured project binding, and retain the previous configuration for rollback. Acceptance still requires a real application/database run and production ingress evidence; coverage remains unverified.

Before HTML visit delivery, the producer excludes the exact probe names and descendants defined by `UsageService._PROBES`, case-insensitively. For example, `/ready` and `/ready/check` are excluded, while `/ready-made` remains eligible. Classification stays inside Caddy; raw paths are never sent to the observer.
