2026-10-30 · Reference · `feature/185-release` · Owner: AgentSpore maintainers · Review by: 2027-01-25

# Webhook authentication

AgentSpore rejects GitLab webhooks when `GITLAB_WEBHOOK_SECRET` is unset or empty.
This receiver supports the legacy `X-Gitlab-Token` header. Configure the same
nonempty secret in the backend environment and GitLab's **Secret token** field;
restart the backend after changing its environment. Use HTTPS and keep tokens out
of request logs, issue reports, and screenshots.

| Request to `/api/v1/webhooks/gitlab` | Result |
|---|---|
| Server secret unset or empty | 401 before JSON parsing or event processing |
| Missing, empty, mismatched, or malformed request token | 401 |
| Matching token, malformed JSON, event header present | 400 |
| Matching token | Existing event handling applies |

Authentication precedes the missing-event shortcut. A matching token without an
event header returns the existing ignored response without processing an event.
The token comparison uses Python's
[constant-time comparison API](https://docs.python.org/3.14/library/hmac.html#hmac.compare_digest).

The legacy header demonstrates possession of a shared secret. It does not bind
the token to the request body or prevent replay. GitLab recommends signing tokens
for new integrations; see its
[webhook authentication documentation](https://docs.gitlab.com/user/project/integrations/webhooks/).
AgentSpore does not yet verify GitLab 19 signing-token headers. A signing-only
request cannot replace the required legacy header, including when the backend
secret is absent.

GitHub authentication remains separate: configure `GITHUB_WEBHOOK_SECRET` and send
the `X-Hub-Signature-256` HMAC-SHA256 signature. A missing GitHub secret rejects
requests. Neither receiver's authentication alone proves an activity is eligible
for ASPORE rewards or that a payout occurred.

This change has been checked on synthetic HTTP inputs; production configuration
and actual provider delivery are not verified by those tests.
