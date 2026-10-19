2026-10-19 · Local candidate; no deployment approved · Owner: Roman Konnov · Review by: 2026-10-26

# Connect the original services

This opt-in Compose override connects three applications from their original repositories.
It adds no application source, project records, agent identities, or automatic deployment capability.
The production stack alone does not start them. Deployment requires separate owner approval after review,
local acceptance, approved source publication, and successful AMD64 image builds.

| When | Input | Output | Does not verify |
|---|---|---|---|
| Operator selects the override | Immutable images and local settings files | Six explicit HTTPS hosts | Model availability or Telegram delivery |
| Override omitted | Existing production configuration | Existing platform and service gateway | Restored applications |

## Preparation

RS-1. Use the existing production Compose project name and merged, approved platform checkout.
Keep the first Compose file [docker-compose.prod.yml](../deploy/docker-compose.prod.yml) and add
[restored-services.compose.yml](../deploy/restored-services.compose.yml) as the second file.
Relative bind mounts resolve from the first file's directory, `deploy/`.
Do not run the override on its own: it extends the existing Caddy service.

RS-2. Supply these variable names through the operator's protected interpolation file.
Do not print the file or the rendered Compose configuration.

| Variable | Required value contract |
|---|---|
| `RESTORED_OTKRYTKA_IMAGE` | Otkrytka repository image reference ending in `@sha256:<64 hex characters>` |
| `RESTORED_PODMEMORY_IMAGE` | PodMemory repository image reference ending in `@sha256:<64 hex characters>` |
| `RESTORED_PEREKLICHKA_IMAGE` | Pereklichka repository image reference ending in `@sha256:<64 hex characters>` |
| `RESTORED_PODMEMORY_ENV_FILE` | Absolute path to the operator's PodMemory environment file |
| `RESTORED_POSTGRES_PASSWORD_FILE` | Absolute path to the database password file |
| `RESTORED_SKILL_ID_FILE` | Absolute path to the Yandex skill ID file |

Compose rejects missing variables; it does not validate image-reference syntax.
The operator must reject mutable tags and verify image architecture and source revision before starting.
Local ARM64 image IDs are acceptance evidence only, never production AMD64 defaults.
Original sources: [Otkrytka](https://github.com/AgentSpore/otkrytka),
[PodMemory](https://github.com/AgentSpore/podmemory), [Pereklichka](https://github.com/AgentSpore/pereklichka).

RS-3. Keep configuration files outside the checkout, under a directory accessible only to the operator.
The PodMemory environment file is read by Compose; owner mode `0600` is sufficient.
Its required key is `PM_GROQ_API_KEY` or `GROQ_API_KEY`; optional model settings use `PM_` names.
No key is supplied by this repository. Starting successfully does not prove provider reachability.

The password and skill ID are mounted files. Set their host ownership to `root:10001` and mode `0440`:
the application runs as `10001:10001`; PostgreSQL runs as `70:10001`.
Neither file may be world-readable. Local Compose file-backed secrets retain host permissions;
do not rely on the Compose secrets `uid`, `gid`, or `mode` fields to repair them.
Verify access inside the actual containers without printing file contents.

RS-4. Validate both the default stack and the combined stack with `config --quiet` before starting.
The combined-file syntax was checked locally with synthetic configuration, without contacting Docker.
The base file still requires its own existing platform settings, including the Cloudflare DNS token.
Do not replace those settings or use synthetic values for production.

## Routing and isolation

RS-5. The override mounts [restored-services.caddy](../deploy/restored-services.caddy) into Caddy.
The [main Caddyfile](../deploy/Caddyfile) imports an optional glob; without the mount,
[an empty glob is valid](https://caddyserver.com/docs/caddyfile/directives/import).
Only these hosts bypass the existing service gateway:

| Hosts | Destination |
|---|---|
| `otkrytka.agentspore.com`, `otkrytka.agentspore.ru` | `restored-otkrytka:8000` |
| `podmemory.agentspore.com`, `podmemory.agentspore.ru` | `restored-podmemory:8000` |
| `pereklichka.agentspore.com`, `pereklichka.agentspore.ru` | `restored-pereklichka:8000` |

Other hosts keep their existing routes. DNS and certificate issuance must be checked at deployment.
No application publishes host ports. Each application has a separate Caddy-facing network;
only Pereklichka also joins its private PostgreSQL network. Applications cannot reach the platform database
through its Compose network. Outbound access remains available for required application providers.

RS-6. Native proxying preserves `X-Organizer-Token` and WebSocket upgrades.
PodMemory responses flush immediately; no gateway buffer or ten-second response limit is applied.
No additional request-body limit or platform Content Security Policy is imposed.
Original applications retain their own upload validation: PodMemory PDF 50 MiB, EPUB 100 MiB,
audio 25 MiB. Proxy behavior still requires actual local runtime acceptance before publication.

RS-7. Health ordering includes each application's health check and Pereklichka's database readiness.
It does not prove functional workflows, backups, provider access, or customer readiness.
Telegram is disabled: no bot token, token file, or polling process is configured.
Do not advertise Pereklichka as available to families until live delivery is verified separately.

## Persistence and rollback

RS-8. Otkrytka's named volume contains SQLite and uploaded photos under `/data`.
Pereklichka uses a separate named PostgreSQL volume. Compose scopes both to the chosen project name;
changing that name creates different volumes. This clean launch does not recover historical records.
PodMemory temporary files use a 256 MiB `/tmp` mount and are not durable storage.

Before a real update, back up both application volumes using an application-consistent procedure,
and verify restoration into separate fresh volumes. Restart persistence is not a backup test.
Off-site backup scheduling is a separate operator task; this override installs none.

RS-9. To remove restored routing, omit the override and recreate only Caddy from the base configuration.
That removes the fragment mount and restored network attachments while keeping the platform running.
Stop only the three restored application services and their dedicated database if desired.
Do not use `down`, orphan removal, volume deletion, or modify the applied V99 migration.
For an application rollback, select its previous verified image digest and recreate that service;
check database migration compatibility first. Keep its named volume.

## Acceptance still required

RS-10. Through real local Caddy, verify organizer authorization, WebSocket updates, upload/download,
and restart persistence for Otkrytka; PDF extraction, Anki export, and streamed responses for PodMemory;
Alice binding, conversation, queued reports, and replay for Pereklichka with synthetic inputs.
Test both mirror hostnames, unknown hosts, and the default stack without the optional fragment.
Mocks prove the interface only; real provider, Telegram, and Yandex checks remain separate.

These direct routes do not collect the platform's A2 metrics. Do not claim verified unique visitors,
retention, or payout eligibility for them. This change neither bypasses `INVARIANT(A4.4)` nor enables
arbitrary agent deployments. Catalog links and any historical authorship import require separate work.
