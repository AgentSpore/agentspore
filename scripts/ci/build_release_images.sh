#!/usr/bin/env bash
# Build one unchanged release Dockerfile; publish only a small JSON report.
set -euo pipefail
umask 077

service=${1:?Usage: build_release_images.sh backend|frontend|agent-runner|caddy}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
options=(--platform linux/amd64 --load --provenance=false)
case "$service" in
  backend|agent-runner) context=$service; dockerfile=$service/Dockerfile ;;
  frontend)
    context=frontend; dockerfile=frontend/Dockerfile
    options+=(--build-arg NEXT_PUBLIC_API_URL=https://agentspore.com
      --build-arg NEXT_PUBLIC_GA_ID= --add-host agentspore.com=127.0.0.1)
    ;;
  caddy) context=deploy; dockerfile=deploy/Dockerfile.caddy ;;
  *) echo 'release_images_invalid_service' >&2; exit 1 ;;
esac

: "${RUNNER_TEMP:?RUNNER_TEMP must name the ephemeral runner directory}"
reports=$RUNNER_TEMP/release-image-reports
mkdir -p -- "$reports"
rm -f -- "$reports/$service.json"
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo 'release_images_host_architecture' >&2; exit 1;
}
daemon=$(timeout --signal=TERM --kill-after=5s 15s docker info --format '{{.OSType}}/{{.Architecture}}')
case "$daemon" in
  linux/amd64|linux/x86_64) ;;
  *) echo 'release_images_daemon_architecture' >&2; exit 1 ;;
esac
source_sha=$(git -C "$repo" rev-parse HEAD)
[[ $source_sha =~ ^[a-f0-9]{40}$ ]] || { echo 'release_images_source_sha' >&2; exit 1; }
work=$(mktemp -d "$RUNNER_TEMP/release-image.XXXXXXXX")
exit_status=0
trap 'exit_status=$?; rm -f -- "$work/image.id" "$work/image.tar"; rmdir -- "$work"; exit "$exit_status"' EXIT
export DOCKER_BUILDKIT=1 BUILDX_GIT_INFO=0

if ! timeout --signal=TERM --kill-after=10s 600s docker buildx build \
  "${options[@]}" --iidfile "$work/image.id" \
  --file "$repo/$dockerfile" "$repo/$context" >/dev/null 2>&1; then
  echo 'release_images_build_failed' >&2; exit 1
fi
image_id=$(cat "$work/image.id")
[[ $image_id =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'release_images_image_id' >&2; exit 1; }
projection='{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}}}'
metadata=$(timeout --signal=TERM --kill-after=5s 15s docker image inspect --format "$projection" "$image_id")
if ! timeout --signal=TERM --kill-after=5s 60s docker image save \
  --output "$work/image.tar" "$image_id" >/dev/null 2>&1; then
  echo 'release_images_export_failed' >&2; exit 1
fi

timeout --signal=TERM --kill-after=5s 30s python3 - \
  "$service" "$source_sha" "$image_id" "$metadata" "$work/image.tar" "$reports/$service.json" <<'PYTHON'
import hashlib
import json
from pathlib import Path
import re
import sys

service, source, image, metadata, archive, output = sys.argv[1:]
record = json.loads(metadata)
if record != {"id": image, "os": "linux", "architecture": "amd64"}:
    sys.exit("release_images_final_architecture")
path = Path(archive)
size = path.stat().st_size
if size <= 0:
    sys.exit("release_images_empty_archive")
with path.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
report = {"service": service, "source_sha": source, "image_id": image,
          "architecture": "amd64", "archive_sha256": digest, "archive_bytes": size}
data = json.dumps(report, sort_keys=True) + "\n"
if len(data.encode()) > 4096 or not re.fullmatch(r"[a-f0-9]{64}", digest):
    sys.exit("release_images_report_invalid")
Path(output).write_text(data)
PYTHON
