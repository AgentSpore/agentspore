2026-10-16 · Browser, encrypted backup, arm64 image and source Compose locally verified · feature/D3 · Owner: AgentSpore maintainers · Review by: 2026-10-23

# Emergency Binder

Emergency Binder turns your own family notes into a printable reminder or care handover. It needs no account and works in English or Russian, with US and GB question sets. The ten field values stay on your device. Encrypted backup and explicit-confirmation restore run locally in the browser; neither questionnaire values nor the passphrase are uploaded.

| Action | Input | Output | Excluded |
|---|---|---|---|
| Prepare or print | Optional notes, country, language, family/care mode | Preview and browser print/Save as PDF | Uploads, fact checking, medical or legal advice |
| Backup/restore | All fields and a passphrase | Encrypted file or a candidate requiring confirmation | Password recovery or automatic replacement |
| Request a country | Fixed GitHub composer link | A draft the user may submit | Form data, passphrase or automatic issue publication |

## Prepare and print

EB1. Choose US or GB, English or Russian, and family or care mode. Work through contacts, home, documents, organizations/instructions and care. Each section has a main field and notes; all ten fields are optional, with a limit of 1000 Unicode code points each. Overlong values are rejected without trimming. Changing language, country or mode preserves your entries.

EB2. Family mode prints all filled sections. Care mode prints only contacts, home and care; documents and organizations/instructions remain in memory but are hidden from that printout. Check the preview before opening the browser's print dialog or choosing Save as PDF. Partial forms are allowed. Printed output and PDFs are plaintext, not encrypted, and need to be kept private.

EB3. Do not enter service passwords or payment codes, and do not upload documents. The tool does not verify your notes, provide emergency numbers or replace professional advice. The ten field values and backup passphrase stay only in page RAM/DOM; the application does not transmit them or save them in browser storage. Reloading clears the input. Clear removes the visible data and download links, but cannot guarantee physical overwriting of JavaScript memory or remove files you already saved.

## Encrypted backup and restore

EB4. Every encrypted backup includes ALL ten fields, including documents and organizations/instructions hidden in care mode. It also preserves the selected country, language and mode. Care mode is a print filter, not a way to remove fields from a backup. Review the full contents before exporting or sharing any file.

EB5. Backup and restore require a secure browser context and native WebCrypto. If unavailable, they remain disabled with an explanation while the questionnaire and printing stay usable. Backup derives a nonextractable AES-256-GCM key using PBKDF2-HMAC-SHA256 with 600000 iterations. Each export uses a fresh random 16-byte salt, 12-byte IV and 128-bit authentication tag, with fixed version-specific authenticated additional data. Native WebCrypto performs these operations; the application supplies no custom cipher. Use 12–128 Unicode code points for the passphrase, preferably unrelated random words, and do not reuse a password. Length alone does not guarantee strength. There is no passphrase recovery service.

EB6. Restore validates and decrypts locally, then offers a candidate that you must explicitly confirm before replacement. Wrong passwords, damaged files and stale candidates leave the current form unchanged. Cancel discards a candidate without replacing the form. Export captures a snapshot before derivation: ordinary edits do not change that file. Import rejects extra envelope fields, unsupported parameters and files larger than 98304 bytes before key derivation. Authenticated plaintext is bounded to 65536 bytes and checked against the document schema. Errors are generic and do not echo the filename, imported values or passphrase. Clear cancels delivery of pending crypto results; it does not guarantee that an in-progress derivation stops or that JavaScript strings are securely erased. Downloaded backups remain under your control. The fixed download name is `family-folder.enc.json`. Password and file controls are cleared after the operation; this does not prove physical memory erasure. Local acceptance covers native encryption/decryption, independently decrypted download bytes, distinct export salts/IVs, malformed files, wrong passphrases, stale state and Clear during delayed derivation. These checks and a static security review do not guarantee protection against a compromised device or browser.

## Counters and country requests

EB7. For a platform project explicitly created with `metrics_profile: "emergency_binder"`, the browser sends only `event`, `section` and `lang` to same-origin `POST /_agentspore/events`. No field values, passphrase, imported filename, country, mode or attempt ID are included. Failed events do not block the product. The standalone static service returns 404 for events and `/stats`, and stores no counters or service database. Local browser checks cover backup downloads while metrics return 503 or abort. A backup counter records download initiation, not durable file saving.

EB8. The creator reads seven-day aggregates through authenticated `GET /api/v1/agents/projects/{project_id}/events` using its `X-API-Key`. There is no public statistics page. Counters describe actions, not unique people: opening a print dialog or initiating a backup download does not prove the operating system saved a file, and completion/open ratios may exceed one. These counters do not change A2 usage, heartbeat or payout accounting.

EB9. Request country opens the fixed `https://github.com/AgentSpore/agentspore/issues/new` composer with a fixed title and the text `Country or region requested: [fill in GitHub]`. It does not insert your form or passphrase. You need a GitHub account and must fill in and submit the issue yourself. The application neither fetches GitHub with a token nor publishes an issue automatically.

## Local packaging and verification

EB10. The Dockerfile reuses the immutable official Caddy 2.11.7 base verified for the earlier services, retains its notices, removes Caddy's binary capability and runs as UID/GID 65532. The seven-file public allowlist includes `src/backup.js`. No test, helper, documentation or private configuration is copied into the image. Caddy serves HTTP on 8080 with `/health`; the platform gateway supplies external TLS. Admin API, automatic HTTPS, config persistence, access logs and directory listing are disabled. CSP allows same-origin assets/connections, blocks objects and embedding, and disallows form submissions.

EB11. Compose declares image `agentsspore/emergency-binder:local` and binds only `127.0.0.1:4175`. Runtime restrictions are read-only rootfs, UID/GID 65532, all capabilities dropped, no new privileges or host/named/anonymous volumes, and a 16 MiB temporary `/tmp`. Limits are 64 MiB RAM with no extra swap, 0.25 CPU and 32 PIDs. CPU configuration uses Linux CFS `cpu_period: 100000` and `cpu_quota: 25000`, avoiding the installed Compose plugin’s earlier failure to apply `cpus`. Build networking is disabled. Actual D3 source Compose build/up/ps/down passed with these runtime restrictions. The isolated check changed only the staged build context, unique image tag and ownership labels, retaining the source port and hardening. It verified health, served index/app hashes, CFS period/quota and removal of its own container, image and network. No automatic startup is performed.

EB12. Final local browser acceptance passed all nine methods: questionnaire/state/print/privacy, field boundaries and native encrypted backup/restore. Eight US/GB × EN/RU screenshots at 390 and 1280 pixels were independently inspected without visual findings. They show the populated family-mode page and empty backup controls; they do not establish the appearance of busy, error or restore-candidate states. Independent static security review reported no findings against the same seven web sources; it is not a security certification.

EB13. Local arm64 image acceptance passed three container tests and an English/Russian browser probe under the actual Caddy CSP, including a native encrypted download. The image check verified its pinned base and public assets; its source guard and owned container/image/process cleanup passed. Native test processes were guarded at 1536 MiB. Docker daemon pull/unpack memory was not hard-capped. The separate source Compose workflow also passed; it does not establish live platform integration.

| Source or behavior | Evidence record | Result and boundary |
|---|---|---|
| [Questionnaire](src/app.js), [backup](src/backup.js), [browser tests](tests/test_backup.py) | `frontend/backup-green-2-proof.json` | 9/9 methods passed; source unchanged; no live platform claim |
| Seven public web sources | `frontend/security-review-proof.json` | Independent static review: zero findings; reviewed web hashes retained |
| EN/RU at 390/1280, US/GB | `frontend/final-visual-review.json` | All eight actual PNGs inspected; no visual findings in captured states |
| [Dockerfile](Dockerfile), [Caddyfile](Caddyfile), served assets | `packaging/artifacts/d3-image-2/proof.json` | Accepted arm64 image: three container tests and CSP browser probe; owned cleanup passed |
| [Compose configuration](compose.yaml) | `packaging/artifacts/d3-compose-1/compose-proof.json` | Build/up/ps/down passed; observed CFS period 100000/quota 25000; owned cleanup and source guard passed |

Evidence records are kept in the local D3 acceptance bundle, not copied into the public image. The coordinator separately reports 25 backend cases and SDK checks passed; this README update did not rerun them. Full-platform regression, amd64 runtime, live gateway metrics, observed demand, deployment and publication are not claimed. No download test proves the operating system durably saved a file. A weekly external agent may review creator aggregates and country requests; this static service has no hosted scheduler or automatic publishing.

## Run locally with Docker Compose

EB14. In this service directory, build, start and inspect the service:

```sh
docker compose build --no-cache --pull=false
docker compose up --detach --no-build --pull never
docker compose ps --quiet emergency-binder
```

Open <http://127.0.0.1:4175>. Port 4175 must be available. The pinned Caddy base must already exist locally or be obtained separately; the build command does not refresh it. These operations passed the isolated source Compose check described above, using the source port and runtime restrictions with only QA context, image identity and ownership labels substituted.

EB15. After use, stop this Compose project:

```sh
docker compose down
```

The isolated check exercised this operation and verified that its own container and project network were absent. It separately removed its uniquely tagged test image. The command above leaves the local application image and pinned base available. This service has no volumes to prune, and no volume cleanup or broad pruning is required.
