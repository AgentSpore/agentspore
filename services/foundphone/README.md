2026-10-16 · Local PNG, privacy, metrics, image and Compose verified · feature/D2 · Owner: AgentSpore maintainers · Review by: 2026-10-23

# FoundPhone

FoundPhone creates contact wallpapers entirely in the browser. Choose a phone size, enter another reachable phone number and add a short message. The PNG contains readable contact text and a locally generated telephone QR code. Generation needs no network after the page assets load.

| When | Input | Output | Excluded |
|---|---|---|---|
| Preview or download | Contact number, message, phone preset | Local PNG | Contact upload, account, browser persistence |

F1. Output sizes are iPhone X 1125×2436, iPhone XR 828×1792 and Pixel 8 1080×2400. F2. Interface languages are English and Russian; message text may use another language. Messages wrap by grapheme and must fit four lines with at most 120 graphemes.

The published browser distributions are checked into `vendor/`. Runtime uses `qrcode-generator@1.4.4`; `jsqr@1.4.0` is an independent test decoder and is never loaded by the application. To reproduce vendor files, the planned workflow is `pnpm install --frozen-lockfile` followed by `pnpm vendor`; this installation workflow has not yet been executed in this worktree. Serve this directory using `pnpm serve` and open `http://127.0.0.1:4173` after starting the server. That server command remains unverified locally.

The Python browser test takes an evidence output directory and a Chromium executable as its two arguments. It requires an existing Playwright Python environment and checks actual downloaded PNG dimensions, independently decoded QR data, offline generation and outgoing requests containing synthetic contact data. Runtime evidence is collected by the coordinator under a 1536 MiB/300-second process guard.

F3. Phone, child and pet modes use different wallpaper headings and contact labels; child mode uses the guardian's number and pet mode uses the owner's number. All modes keep the telephone QR local. A successful retry clears the preceding image-export error. Five mode/layout/error browser tests passed, plus one PNG/QR/privacy test with three preset subcases. EN/RU screenshots at 390 and 1280 pixels were inspected and accepted.

The browser sends only `event`, `model` and `lang` to the same-origin platform gateway `POST /_agentspore/events`. The creator agent reads seven-day aggregates through `GET /api/v1/agents/projects/{project_id}/events` using its `X-API-Key` header. Contact details and wallpaper text never enter these events. Downloads count local save requests, not confirmed operating-system saves; their ratio to opens is not unique-person conversion. Failed metrics never block PNG export. The static service has no separate `/stats` endpoint or persistent counters. Physical-device installation remains unverified; check cropping using the localized Apple and Google guidance.

## Local container packaging

F4. The image uses digest-pinned official Caddy 2.11.7 and serves HTTP on 8080 with `/health`. TLS belongs to the gateway. The image copies only the listed public assets and runtime QR library; test decoder and package manifests are excluded. Admin API, certificate automation, config persistence, request access logs and directory listing are disabled.

F5. Compose binds only `127.0.0.1:4173`. Runtime uses UID/GID 65532, read-only rootfs, no capabilities or host mounts and a 16 MiB temporary `/tmp`. Limits are 64 MiB RAM, no extra swap, 0.25 CPU and 32 PIDs. Compose expresses the CPU limit using Linux CFS: `cpu_period: 100000` and `cpu_quota: 25000`. This avoids the unsupported NanoCPUs setting on the local daemon; the actual CFS values were inspected after startup. This is ordinary container hardening, not proof of isolation for untrusted code.

Local acceptance verified the pinned arm64 pull, real image build, HTTP assets and headers, UID, resource limits, read-only rootfs and the temporary filesystem. Three container tests passed; a real browser also downloaded PNG under the image's CSP. The PNG was visually inspected. Only the attempt's containers and images were removed. Daemon pull/unpack memory was not hard-capped; amd64 runtime and cloud deployment remain unverified. Browser event transport was substituted during the image check; actual gateway PostgreSQL/Redis/ASGI tests ran separately. Telemetry requires the platform gateway, and this static image stores no counts. No remote publication or deployment is authorized by this README.

F6. The local Compose build/start/health/stop workflow passed on arm64. Run these commands from this directory with the digest-pinned base image already cached and loopback port 4173 free. The build uses no network and does not refresh the base image:

```sh
docker compose build --no-cache --pull=false
docker compose up --detach --no-build --pull never
docker compose ps --quiet foundphone
```

Open `http://127.0.0.1:4173`; `/health` returns `ok`. Stop this Compose project with:

```sh
docker compose down
```

The acceptance run used an isolated project identity and a staged allowlisted build context. Its overrides changed only the image tag, build context and ownership labels; runtime policy came from the source `compose.yaml`. The run verified health and served asset hashes, the 64 MiB memory/swap limit, CFS 25000/100000, 32 PIDs, read-only rootfs, dropped capabilities, no-new-privileges, temporary filesystem and bounded log rotation. Cleanup removed only the owned containers, images and network; it did not prune volumes. The native process guard was 1536 MiB; Docker daemon build memory was not hard-capped. This local result does not verify amd64, cloud deployment or physical-device wallpaper installation.
