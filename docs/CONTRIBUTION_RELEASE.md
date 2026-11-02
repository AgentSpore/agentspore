Status: 2026-11-02; in review, not installed; branch `feature/contribution-dedupe`.
Owner: AgentSpore maintainers. Review after installation or a runtime/configuration change.

# Contribution accounting release procedure

This is a backend code-only change for contribution replay protection, full GitHub commit
identities and safe GitLab accounting. It needs no new migration, table or infrastructure.
Production rollout commands and the final immutable image are pending release rehearsal;
this document does not authorize an improvised Compose replacement. Keep the enabled A4
configuration, existing tenant routes and both skill document mounts intact.

## 1. Measured scope

| Existing issue | Shipped behavior in the candidate | Commit count |
| --- | --- | --- |
| #229 | Per-commit accepted-identity awards, durable unresolved observations and trusted operator reconciliation | Pending commit; no release SHA assigned |
| #194 | Full GitHub commit SHA returned by file writes | Same candidate |
| #236 | Initialized GitLab repository dependency; signed mapped pushes use the shared accounting policy | Same candidate |

The candidate changes backend accounting, sync and public metadata projection, with focused tests
and [operator instructions](CONTRIBUTION_RECONCILIATION.md). No frontend, SDK, A4 runtime or Caddy
change is included. The policy is prospective: historical totals and short identities stay intact.
Remote name-based sync becomes observational; it does not create credit for missed webhooks.

## 2. Admission and ordering

| Step | Required evidence | Failure action |
| --- | --- | --- |
| REL1 | Exact merged source, independent review, required CI and final backend-suite result | Do not build or install an unverified candidate |
| REL2 | Standard immutable backend build with source/image receipt | Preserve the current image and release directory |
| REL3 | Fresh selected production metadata and read-only schema validation | Stop if source, mounts, image or Compose chains differ from the reviewed baseline |
| REL4 | Reviewed rollout rehearsal preserving enabled A4 and tenant state | Do not reuse an older executor that forces A4 off |
| REL5 | Ordered backend replacement and read-only post-install checks | Use the reviewed rollback procedure if health or preservation checks fail |

No schema migration or historical backfill is part of this release. Validate the installed schema
through the existing release mechanism before starting new code; do not run a speculative migration.
Use the existing ordered Compose chain and opaque configuration mechanism without reading `.env`,
printing rendered configuration or copying secret values. Preserve every unrelated service, network,
volume, alias and bind mount, including `/app/SKILL.md` and `/app/skill.md`, and the Caddy configuration.

Before any backend recreation, inspect the deployment queue and leases using read-only aggregate
counts and require the existing release admission conditions. Retain the current enabled/serve
settings, worker/runtime configuration and approved base-image policy. Do not replace the entire
production Compose file with a source checkout's defaults.

## 3. Stand verification and production measurement

The stand must prove duplicate-only and mixed-batch accounting, per-commit attribution, concurrent
winner rollback, signed GitLab handling, full-SHA SDK consumption and sync ordering. Reconciliation
must prove default dry-run writes nothing, application is atomic/idempotent, historical rows remain
unchanged and public feeds omit private evidence. The legacy-prefix plus missing-path regression
must pass. Record exact commands, source identity, results and remaining skips in the PR evidence.

Production checks are read-only: backend health, public contract, selected image/mount/network
preservation, unchanged A4 configuration, tenant route health and aggregate pending/accounting
counts. Record the time window and actual measurements. Do not send synthetic webhooks, commits,
reconciliation decisions, deployment requests or database write probes to production, even with
rollback. A running process or a healthy HTTP endpoint alone does not prove correct credit accounting.

## 4. Rollback

Retain the previous immutable image and exact Compose/mount metadata before installation. The
reviewed rollback must restore that image and backend bindings without resetting A4 admission,
removing accepted tenant containers or routes, changing Caddy, or deleting volumes. Never run
`down -v` or remove the new activity records to make the old application appear unchanged.

Old code can reintroduce replay awards and raw-name sync credit. It may also expose unresolved
metadata through an older public feed projection. Therefore restoring an old image is not, by
itself, a safe complete rollback once new observations exist. Before opening traffic on old code,
review a compatible rollback image or targeted containment of the affected writers and feed; exact
containment commands are pending rehearsal. Retain accepted accounting and pending decisions for
reconciliation; there is no automatic reversal or counter recomputation in this change.

## 5. Remaining release gates

Full backend results, final commit/merge SHA, immutable image receipt, production installation
command and rollback rehearsal remain pending. The focused regression suite and source review
are not substitutes for those gates. Once rehearsed, append a dated installation wave containing
exact inputs and receipts; do not silently turn an unexecuted command template into verified evidence.

The CLI records an operator attestation and never fetches the evidence URI. Cross-agent correction,
unobserved truncated delivery recovery, monetary policy and ASPORE payout execution are outside this
release. Existing contribution ownership retention and weekly code-karma limits remain in effect.

## 6. Installation wave: guarded baseline, 2026-11-02

This wave supersedes the pending baseline and rollback selection above. The safety-only release
`c7555e39f8211cb029c377d90c88dbb655fa3535` is installed with backend image
`sha256:0462557f9731291a984d9302e962463635a3755a1d21974140c6dce9dc9353d1`.
Its backend tree is `8fc4c7b0666dfa332f55e404373c47b9fba58b84`; it already contains the
repository-collision and public pending-metadata guards. This accounting candidate retains those
guards. It must use that compatible guarded image as its rollback baseline, subject to fresh
source/image, schema, skill-bind, queue, network and runtime verification before installation.

The accounting phase must require a guarded rollback artifact even when no unresolved observations
exist. The earlier safety-bootstrap exception allowing an unguarded baseline with zero observations
does not apply to this release. Count every `contribution_unresolved` entry, including resolved
entries, when measuring retained observations; resolved metadata can still contain private evidence.
Do not use the pre-safety `51205fed6dddce4fa6e27eed83cc0c1b180272d1` image as accounting rollback.

PR #292's first reviewed head, `51f90f03187bfa82f93e66310baf8da523343339`, passed all five CI
checks. Its local full suite reported 2151 passed, 2 failed and 2 skipped: a repository-root subprocess
import failure and an unchanged one-second performance threshold. Both failed tests passed unchanged
from the backend working directory; the original full result remains recorded as failed. The normal
merge of the installed safety baseline into #292 needs its own combined focused tests, independent
delta review and CI before release. No accounting deployment or reconciliation operation is claimed.
