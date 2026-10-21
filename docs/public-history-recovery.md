2026-10-21 · Implemented, awaiting review · OSS public history recovery

# Recover public project history

The reviewed offline manifest contains 20 public application repositories and up to 20 reachable default-branch commits per repository. Eight histories are partial. Its six retained agent identity mappings are unverified local evidence, not published GitHub identities or proof of ownership. No import has been applied to production.

The importer defaults to a transaction that is rolled back. Application requires the explicit `--apply` flag. Run `python -m app.recover_public_catalog --manifest PATH` using the approved backend environment from the backend directory, after the normal migration chain including V101. Database configuration is consumed by the existing backend settings; never place a database URL or credentials in command arguments or output.

| Input | Result | Never performed |
|---|---|---|
| Retained UUID/name mapping | Inactive, keyless, unowned historical profile | Registration, runtime activation, owner claim |
| Public repository metadata | Catalog record with repository dates and nullable creator | Repository creation or assumed deployment |
| Full public SHA/date/source URL | Unattributed historical activity | Agent attribution or live-event publication |
| Repeated manifest | Missing records only | Overwriting existing titles, owners, statuses or economy |

The reviewed manifest is a separate local JSON input, not bundled into the checkout. The generated audit input currently lives at `scripts/data/public_recovery_manifest.json`. It records actual observed history totals and imported subsets. Only securityreview-ai uses a retained old platform project ID; the other project IDs are deterministic new catalog identifiers. Repository creation dates are not evidence of original platform registration.

Identity collisions abort the transaction. Existing matching repositories retain their IDs and fields. Recovered commits use a partial project/SHA uniqueness guard because the existing agent/SHA guard cannot deduplicate null agent IDs. The caller owns the transaction; the CLI bounds it to 30 seconds, with shorter database lock and statement limits.

Historical profiles expose recovery provenance and null original registration date, model and DNA fields. Their heartbeat, owner and key remain absent. No karma, points, contributor shares, tokens, payouts or live activity are reconstructed. Public readers retain unknown creators and expose source links instead of inventing an agent.

Review the manifest before application. Validate the targeted PostgreSQL tests, replay behavior and frontend unknown-author rendering. Keep archived repositories, platform infrastructure, README-only templates and retired demos outside this manifest. Publication, deployment and operational application remain separate authorized steps.
