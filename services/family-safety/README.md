2026-10-16 · Local browser, print and arm64 image acceptance verified · feature/D1 · Owner: AgentSpore maintainers · Review by: 2026-10-23

# Family Safety

Family Safety helps a family agree on a reminder plan and practise responding to urgent requests. Choose a private family phrase, optionally print it on a reminder card, and work through ten fictional messages. No account is needed. The exercise does not verify anyone's identity or guarantee protection.

| When | Input | Output | Excluded |
|---|---|---|---|
| Prepare a reminder | Optional family phrase and print consent | Printable card | Phrase upload or persistent storage |
| Practise | Ten pause-or-ordinary choices | Explanations and a score | Real conversations, identity checks or financial transactions |
| Share | The app's public address | WhatsApp or Telegram sharing link | Phrase, answers and attempt ID |

## Use the page

FS1. Agree on a phrase privately with your family. Do not reuse an existing password. The field is optional and accepts up to 80 graphemes, counting a joined emoji as one character. Excess input is rejected rather than shortened. The phrase and print consent stay only in the open page's RAM and are never transmitted. Training progress is held in RAM locally, while anonymous correctness snapshots are sent as described in FS5. Reloading clears local state but does not delete snapshots already received by the platform. Language changes preserve local state.

FS2. Print consent is off initially. Select it only if you want your phrase on the card, and clear it to exclude the phrase from subsequent prints. The card advises you to pause, end the call and call back using a number you already know. A familiar voice or correct phrase alone is not proof of identity. Print styling hides the editing controls and training.

FS3. Eight fictional messages need a pause and a separate check; two are ordinary requests. Choose an answer, read its explanation, then select the next message. The ordinary examples still do not establish the sender's identity. Choosing pause for all ten produces 8/10. Restart creates a new practice attempt.

FS4. The interface has 31 languages: en, ru, es, pt, fr, de, it, nl, pl, uk, cs, sk, ro, hu, el, tr, ar, he, fa, hi, bn, ur, id, ms, vi, th, ko, ja, zh-Hans, sw and tl. The shared curriculum defines round IDs, order and correct answers; translations supply the visible text. Arabic, Hebrew, Persian and Urdu use right-to-left presentation. The sharing links send only this app's address; agree on your phrase separately.

## Privacy and platform statistics

FS5. Practice sends best-effort snapshots to the same-origin `POST /_agentspore/events`: a random anonymous attempt ID, curriculum version, starting language, boolean correctness answers and completion. The booleans describe whether each answer was correct, not the selected button. No phrase, message text or contact details are included. Failed metrics do not block training, sharing or printing. There is no service database, secret, persistent client queue or external analytics SDK.

FS6. Statistics require a platform project explicitly created with `metrics_profile: "family_safety"` and served through the platform gateway. Only that profile exposes public `GET /stats`, with `curriculum_version` defaulting to 1. Standalone Caddy returns 404 for `/stats` and `/_agentspore/events`; standalone use has no metrics. The platform intercepts these paths before forwarding to the static service.

FS7. Platform statistics cover the current UTC date and six preceding UTC dates, grouped by curriculum version. Starts count observed anonymous attempts, not people or page opens; a lost initial snapshot can be followed by the first observed answer. Completion and per-round error ratios use those attempts and do not prove learning effectiveness or product demand. Snapshots are retained for the current UTC date and seven preceding dates, with older rows removed by the platform's shared hourly purge. These training metrics do not determine payments; A2 remains the sole payout source.

FS8. An external agent can review the public statistics and current public warnings weekly. A meaningful content or answer change requires a new curriculum version and review of all 31 translations. There is no hosted cron task in this static service. Sparse statistics are insufficient grounds for a demand claim or an automatic content change.

## Local container packaging

FS9. The Dockerfile uses the same immutable official Caddy 2.11.7 base as the verified FoundPhone packaging. It retains the base's notices and files, removes Caddy's binary capability and runs as UID/GID 65532. Only the 35 explicit public files and Caddy configuration are copied; tests, documentation, helper scripts and private files are excluded. Directory-level allowlist exceptions are deliberately absent.

FS10. Caddy serves HTTP on port 8080 and `/health`; the platform gateway provides external TLS. The admin API, automatic HTTPS, config persistence, request access logs and directory listing are disabled. The CSP permits same-origin scripts, styles, images and connections, blocks objects and embedding, and disallows form submissions. There are no CDN dependencies or dynamic backend routes.

FS11. Compose publishes only `127.0.0.1:4174`, with a read-only root filesystem, all capabilities dropped, no privilege escalation and no host, named or anonymous volumes. Only `/tmp` is writable through a 16 MiB tmpfs. Limits are 64 MiB RAM, no extra swap, 0.25 CPU and 32 PIDs. Build networking is disabled. Container hardening is not proof of isolation for untrusted code.

## Verification status

Seven local browser tests passed after the startup-readiness fix: two English training/privacy tests, three localization tests covering all 31 languages, one native print-PDF test with default, consented and revoked cases, and one delayed-startup test. The independent follow-up review reported no findings. These checks verify local behavior; native-speaker review of the 30 translations remains unverified.

Local arm64 image acceptance passed three container tests and an English/Russian browser check under the actual Caddy CSP. It verified health, all 35 served asset hashes, response headers and types, the non-root user, resource limits, disabled listeners, removed binary capability, read-only root filesystem and temporary filesystem. The browser captured a 390-pixel Russian screenshot, inspected by the coordinator. Only the attempt's uniquely owned containers and images were removed; process and sampler cleanup were verified.

Image acceptance used an ephemeral loopback port and the 64 MiB, 0.25 CPU, 32-PID runtime limits. The native test process family was guarded at 1536 MiB; Docker daemon memory was not hard-capped. The actual source Compose configuration also passed build, up, ps, health, asset and down checks. The installed Compose plugin silently ignored `cpus`, so the configuration now uses Linux CFS `cpu_period: 100000` and `cpu_quota: 25000`; the running container reported those exact values. The Compose check changed only the staged build context, unique image tag and ownership labels, and verified removal of its own container, image and network. Browser metric transport was substituted at the HTTP boundary, so this is not live platform integration. The existing pinned base was reused without a new pull. The CPU compatibility fix changes only Compose; the final README update changes no web asset or image input. Full-platform regression, amd64 runtime and live deployment are not claimed. Publication and deployment are not authorized by this README.

## Run locally with Docker Compose

FS12. In this service directory, use Docker Compose to build, start and inspect the service:

```sh
docker compose build --no-cache --pull=false
docker compose up --detach --no-build --pull never
docker compose ps --quiet family-safety
```

Open <http://127.0.0.1:4174>. A listener already using port 4174 must be resolved before startup. The pinned Caddy base must already exist locally or be obtained separately; the build command does not refresh it. These build/up/ps operations were exercised in the isolated Compose check described above, with the source port and runtime restrictions preserved.

FS13. After use, stop this Compose project:

```sh
docker compose down
```

The isolated check exercised this operation and verified that its project network and container were absent. It removed only its own uniquely tagged test image; the command above leaves the local application image and pinned base available. No volume cleanup or pruning is required.
