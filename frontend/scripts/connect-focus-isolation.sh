#!/usr/bin/env bash
set -euo pipefail

stage() {
  local name=$1 limit=$2 result
  case "$name" in
    "host network control"|"production build"|"build permissions"|"proxy controls"|"backend readiness"|"direct backend control"|"proxy readiness"|"browser tests") ;;
    *) echo 'Invalid stage name' >&2; return 1 ;;
  esac
  shift 2
  printf 'Starting %s\n' "$name"
  if timeout --kill-after=5s "$limit" "$@" >/dev/null 2>&1; then
    printf 'Passed %s\n' "$name"
  else
    result=$?
    printf '::error title=Connect focus::%s failed (exit %s)\n' "$name" "$result"
    return "$result"
  fi
}

stop_child() {
  local pid=$1 privileged=${2:-false}
  if [[ $privileged == true ]]; then
    timeout --kill-after=1s 5s sudo -n kill "$pid" 2>/dev/null || true
  else
    kill "$pid" 2>/dev/null || true
  fi
  for _ in {1..50}; do
    if ! jobs -pr | grep -Fxq "$pid"; then wait "$pid" 2>/dev/null || true; return 0; fi
    sleep .1
  done
  if [[ $privileged == true ]]; then
    timeout --kill-after=1s 5s sudo -n kill -KILL "$pid" 2>/dev/null || true
  else
    kill -KILL "$pid" 2>/dev/null || true
  fi
  return 1
}

cleanup_host() {
  local result=$?; trap - EXIT
  if [[ -n ${listener:-} ]]; then stop_child "$listener" true || result=1; fi
  timeout --kill-after=1s 10s sudo -n rm -rf -- "$scratch" || result=1
  exit "$result"
}

cleanup_services() {
  local result=$?; trap - EXIT
  stop_child "$next_pid" || result=1
  stop_child "$proxy_pid" || result=1
  exit "$result"
}

if [[ ${1:-} != --inside ]]; then
  [[ $(uname -s) == Linux && $(id -u) -ne 0 ]] || { echo 'Linux and an unprivileged runner are required' >&2; exit 1; }
  for binary in sudo unshare ip iptables python3 curl setpriv timeout; do command -v "$binary" >/dev/null || exit 1; done
  node_dir=$(dirname "$(command -v node)")
  runner_uid=$(id -u)
  runner_gid=$(id -g)
  [[ $runner_uid != 65534 ]] || exit 1
  scratch=$(mktemp -d)
  trap cleanup_host EXIT
  timeout --kill-after=1s 5s sudo -n chown "0:$runner_gid" "$scratch"
  timeout --kill-after=1s 5s sudo -n chmod 710 "$scratch"
  sudo -n python3 -c 'import http.server,sys
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  with open(sys.argv[1],"a") as f: f.write("hit\n")
  self.send_response(200); self.end_headers()
 def log_message(self,*args): pass
server=http.server.HTTPServer(("127.0.0.1",0),Handler)
with open(sys.argv[2],"w") as f: f.write(str(server.server_port))
server.serve_forever()' "$scratch/hits" "$scratch/port" >/dev/null 2>&1 &
  listener=$!
  printf 'Starting host listener control\n'
  for _ in {1..100}; do [[ -s $scratch/port ]] && break; sleep .05; done
  [[ -s $scratch/port ]] || exit 1
  port=$(cat "$scratch/port")
  [[ $port =~ ^[0-9]+$ ]] || exit 1
  curl --max-time 5 -fsS "http://127.0.0.1:$port/" >/dev/null
  [[ -f $scratch/hits && $(wc -l < "$scratch/hits") -eq 1 ]] || exit 1
  printf 'Passed host listener control\n'
  timeout --kill-after=1s 5s sudo -n chgrp "$runner_gid" "$scratch/hits"
  timeout --kill-after=1s 5s sudo -n chmod 640 "$scratch/hits"
  printf 'Starting isolated gate\n'
  if timeout --kill-after=10s 14m sudo -n env -i PATH="$node_dir:/usr/sbin:/usr/bin:/sbin:/bin" unshare --net bash "$PWD/scripts/connect-focus-isolation.sh" --inside "$runner_uid" "$PWD" "$node_dir" "$HOME/.cache/ms-playwright" "$scratch" "$port" "$runner_gid"; then
    printf 'Passed isolated gate\n'
  else
    result=$?
    printf '::error title=Connect focus::isolated gate failed (exit %s)\n' "$result"
    exit "$result"
  fi
  [[ $(wc -l < "$scratch/hits") -eq 1 ]] || { echo 'Host listener reached from namespace' >&2; exit 1; }
  exit 0
fi

[[ $(id -u) -eq 0 && $# -eq 8 ]] || exit 1
runner_uid=$2; workdir=$3; node_dir=$4; browser_path=$5; scratch=$6; host_port=$7; runner_gid=$8
[[ $runner_uid =~ ^[0-9]+$ && $runner_uid != 0 && $runner_uid != 65534 ]] || exit 1
cd "$workdir"
compgen -G '.env*' >/dev/null && { echo 'Environment files are not permitted' >&2; exit 1; }
timeout --kill-after=1s 5s ip link set lo up
timeout --kill-after=1s 5s iptables -A OUTPUT -m owner --uid-owner "$runner_uid" -d 127.0.0.1/32 -p tcp --dport 3187 -j ACCEPT
timeout --kill-after=1s 5s iptables -A OUTPUT -m owner --uid-owner "$runner_uid" -j REJECT
mkdir "$scratch/work"
chgrp "$runner_gid" "$scratch/work"
chmod 770 "$scratch/work"
clean=(env -i "PATH=$node_dir:/usr/sbin:/usr/bin:/sbin:/bin" "XDG_CACHE_HOME=$scratch/work" "XDG_CONFIG_HOME=$scratch/work" "TMPDIR=$scratch/work" "NPM_CONFIG_USERCONFIG=$scratch/work/npmrc" "NPM_CONFIG_GLOBALCONFIG=$scratch/work/npmrc-global" "PLAYWRIGHT_BROWSERS_PATH=$browser_path" NEXT_TELEMETRY_DISABLED=1 CI=1)
runner=(setpriv --no-new-privs --reuid "$runner_uid" --regid "$runner_gid" --clear-groups "${clean[@]}")
service=(setpriv --no-new-privs --reuid 65534 --regid "$runner_gid" --clear-groups "${clean[@]}")
printf 'Starting privilege and evidence controls\n'
for identity in runner service; do
  if [[ $identity == runner ]]; then launch=("${runner[@]}"); else launch=("${service[@]}"); fi
  if timeout --kill-after=1s 5s "${launch[@]}" sh -c 'touch "$1/forbidden" 2>/dev/null || exit 0; exit 1' sh "$scratch" &&
     timeout --kill-after=1s 5s "${launch[@]}" sh -c 'rm "$1/hits" 2>/dev/null || exit 0; exit 1' sh "$scratch" &&
     timeout --kill-after=1s 5s "${launch[@]}" sh -c 'mv "$1/hits" "$1/replaced" 2>/dev/null || exit 0; exit 1' sh "$scratch" &&
     timeout --kill-after=1s 5s "${launch[@]}" sh -c '(printf fake >> "$1/hits") 2>/dev/null || exit 0; exit 1' sh "$scratch"; then
    :
  else
    echo "Scratch evidence could be modified" >&2
    exit 1
  fi
  if timeout --kill-after=1s 5s "${launch[@]}" sudo -n true >/dev/null 2>&1; then
    echo "Privilege escalation unexpectedly succeeded" >&2
    exit 1
  else
    result=$?
    [[ $result -ne 124 && $result -ne 137 ]] || exit "$result"
  fi
  timeout --kill-after=1s 5s "${launch[@]}" node -e 'const fs=require("fs");const status=fs.readFileSync("/proc/self/status","utf8");if(!/^NoNewPrivs:\s*1$/m.test(status))process.exit(1)'
done
printf 'Passed privilege and evidence controls\n'
stage "host network control" 10s "${service[@]}" node -e 'const s=require("net").connect(Number(process.argv[1]),"127.0.0.1");s.on("connect",()=>process.exit(1));s.on("error",()=>process.exit(0));setTimeout(()=>process.exit(1),2000)' "$host_port"
stage "production build" 5m "${runner[@]}" npm run build
stage "build permissions" 30s chmod -R g+rwX .next
stage "proxy controls" 30s "${service[@]}" node --test scripts/connect-focus-proxy.test.mjs
"${service[@]}" node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3186 >/dev/null 2>&1 &
next_pid=$!
"${service[@]}" node scripts/connect-focus-proxy.mjs >/dev/null 2>&1 &
proxy_pid=$!
trap cleanup_services EXIT
printf 'Starting backend readiness\n'
for _ in {1..40}; do
  if timeout --kill-after=1s 3s "${service[@]}" node -e 'fetch("http://127.0.0.1:3186/docs/connect",{signal:AbortSignal.timeout(1500)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))' 2>/dev/null; then break; fi
  sleep .5
done
stage "backend readiness" 5s "${service[@]}" node -e 'fetch("http://127.0.0.1:3186/docs/connect",{signal:AbortSignal.timeout(1500)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))'
stage "direct backend control" 10s "${runner[@]}" node -e 'const s=require("net").connect(3186,"127.0.0.1");s.on("connect",()=>process.exit(1));s.on("error",()=>process.exit(0));setTimeout(()=>process.exit(1),2000)'
printf 'Starting proxy readiness\n'
for _ in {1..40}; do
  if timeout --kill-after=1s 3s "${runner[@]}" node -e 'fetch("http://127.0.0.1:3187/docs/connect",{signal:AbortSignal.timeout(1500)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))' 2>/dev/null; then break; fi
  sleep .5
done
stage "proxy readiness" 5s "${runner[@]}" node -e 'fetch("http://127.0.0.1:3187/docs/connect",{signal:AbortSignal.timeout(1500)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))'
stage "browser tests" 6m "${runner[@]}" npx playwright test --config=playwright.connect-focus.config.ts
