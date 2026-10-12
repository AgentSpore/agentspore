# Deploy an explicitly selected release branch

2026-10-12 · Draft, local AF-09 candidate on `feature/AF-09`.
Owner: Roman Konnov · Review by: 2026-10-26.

The [update script](../deploy/update.sh) requires exactly one branch argument,
`release/<name>`. It fetches that exact remote head, resolves one commit SHA,
and uses that SHA for dirty-file comparisons and reset. This candidate was
tested only in synthetic local Git repositories; production installation,
runtime callers, and a production release remain unverified.

| When | Input | Output | Boundary |
|---|---|---|---|
| Update is requested | One valid `release/<name>` argument | Exact remote release commit or refusal | No default branch or version selection |
| Fetch succeeds | Selected remote head | One resolved commit SHA | No fallback to stale tracking refs |
| Dirty guard succeeds | Local files and selected SHA | Reset, build, health check | No database rollback or data decision |

## Operator procedure

RD-01. Before a shared deployment, obtain approval for the target environment
and release branch. Merge the change into trunk, then include it in the release
branch through the normal release process. Feature-branch code runs locally only.

RD-02. Before invoking the script, confirm that the approved `release/<name>`
exists on `origin`, the server has the updated script, and every runtime caller
passes the argument. The CLI form is `bash deploy/update.sh release/<name>`
from the repository root; replace `<name>` with the approved branch suffix.
No actual production branch or release version is established by this document.

RD-03. When the script receives no argument, more than one argument, a malformed
Git branch, `main`, a feature branch, a tag, a SHA, or an option, it exits with
status 1 before opening the deploy log or checking the environment file. The
usage message is `Usage: <script-path> release/<name>`.

RD-04. When the exact branch fetch fails, stop and resolve the remote/access
problem. The script does not reset or build using an older tracking ref.
It fetches without tags and updates only the selected remote tracking ref.

RD-05. When HEAD already equals the fetched commit, the script reports
`Already up to date` and exits successfully without rebuilding. Otherwise it
refuses dirty tracked files that differ from the selected commit, except the
existing `RESET_IGNORE` entry. Preserve local patches through the normal source
review process; do not remove the guard to force a deployment.

RD-06. When reset runs, a dirty ignored file is backed up and restored on exit,
including build or health failures. A clean ignored file receives release
content. The script keeps the build exit status; after a successful build it
tries the backend container's loopback health endpoint up to 12 times, with
5-second delays after unsuccessful probes. Exhaustion returns status 1.
A failure after reset leaves HEAD at the selected commit; automatic rollback
is not provided.

## Verification and remaining release conditions

The [CLI regression tests](../backend/tests/test_deploy_update_script.py) run
real Bash and Git against temporary local bare remotes. Only Docker and sleep
are stubbed. The hard-coded log path is redirected in a fixture copy, the
fixture environment file is empty, and ignored compose content is synthetic.
The CLI invocation above is exercised with `release/test` in those fixtures;
it has not been executed against a server.

The isolated pytest command uses `--noconftest` because these process-level
tests need only pytest's built-in `tmp_path`, not application or database
fixtures. This verifies the changed Bash/Git layer; it does not establish
application, Docker, database, or production health.

RD-07. Before release, verify runtime webhook and manual/sudo caller integration
on the approved environment. Checked-in public caller inspection does not
establish what a server listener executes. The backend and frontend workflows
include pushes to `main` and `release/**`, and pull requests targeting those
branches. Feature-branch pushes remain excluded; the pull-request filter applies
to the target branch. These filters are checked locally; actual GitHub event
dispatch and job execution remain unverified until an authorized push or pull
request. Verify database compatibility separately. Do not treat
a successful local script test as permission to publish or deploy.

The rules above are normative; the CLI form illustrates the input contract.
