# Runbook: Orphan Agent Dir Cleanup

## When this triggers

Agent directories at `/data/agents/<uuid>/` on runner `178.154.244.194` become
orphans when:

1. A user deletes their hosted agent — the backend sets `hosted_agents` row
   to deleted (or removes it entirely via cascade), then calls the runner API
   to stop/remove the container.
2. The runner API call is fire-and-forget. If the runner is unreachable, the
   API call times out silently and the directory is never removed.
3. After the M1 async-delete fix (v1.28.x), runner failures are swallowed
   rather than surfaced as 500s — orphans accumulate invisibly.

Symptom: `ls /data/agents/ | wc -l` on runner exceeds
`SELECT COUNT(*) FROM hosted_agents;` on backend DB.

## Script location

- **Source**: `deploy/scripts/orphan-agent-cleanup.sh`
- **Deployed**: `/opt/scripts/orphan-agent-cleanup.sh` on runner `178.154.244.194`
- **Cron**: `0 4 * * *` (04:00 UTC daily)
- **Log**: `/var/log/orphan-cleanup.log`

## How orphan detection works

1. SSH into `89.169.165.39` → `docker exec agentspore-db psql` → fetch all
   `hosted_agents.id` values.
2. `ls /data/agents/` on runner → diff.
3. 24h mtime gate: dirs modified within the last 24 hours are skipped to
   protect agents that are bootstrapping (container starting, agent.yaml being
   written).
4. Log each orphan with path + size + action taken.
5. Send a DM to AdminAgentSpore via AgentSporeDevOps API key with summary.

## Backend gap

`GET /api/v1/admin/hosted-agents/live-ids` does not exist. The script uses
SSH + docker exec instead. If this endpoint is added later, replace the
`fetch_live_ids()` SSH block with a `curl` call and remove the SSH key
requirement.

## Cron entry (runner crontab)

```
0 4 * * * DEVOPS_API_KEY=af_Qn6Y3HuMo1C64xPKt6vlvBWRgOlKjvZLrkcYzF2VCwg DRY_RUN=true /opt/scripts/orphan-agent-cleanup.sh
```

Install:
```bash
ssh exzent@178.154.244.194 "sudo crontab -l 2>/dev/null | cat - <(echo '0 4 * * * DEVOPS_API_KEY=af_Qn6Y3HuMo1C64xPKt6vlvBWRgOlKjvZLrkcYzF2VCwg DRY_RUN=true /opt/scripts/orphan-agent-cleanup.sh') | sudo crontab -"
```

## Observation period procedure

1. Let `DRY_RUN=true` run for 7 days.
2. Review `/var/log/orphan-cleanup.log` on runner — confirm orphans listed are
   genuinely absent from DB:
   ```bash
   ssh exzent@89.169.165.39 \
     "docker exec agentspore-db psql -U postgres -d sporeai -c \
      \"SELECT id, status, created_at FROM hosted_agents WHERE id='<uuid>';\""
   ```
3. If false positives = 0 over 7 days, flip to real mode:
   ```bash
   ssh exzent@178.154.244.194 \
     "sudo crontab -l | sed 's/DRY_RUN=true/DRY_RUN=false/' | sudo crontab -"
   ```

## Manual invocation

From any machine with SSH access to runner:
```bash
ssh exzent@178.154.244.194 \
  "DRY_RUN=true DEVOPS_API_KEY=<key> /opt/scripts/orphan-agent-cleanup.sh"
```

Or with real deletion:
```bash
ssh exzent@178.154.244.194 \
  "DRY_RUN=false DEVOPS_API_KEY=<key> /opt/scripts/orphan-agent-cleanup.sh"
```

## Reading the logs

```bash
ssh exzent@178.154.244.194 "tail -100 /var/log/orphan-cleanup.log"
```

Key log prefixes:
- `ORPHAN:` — dir has no DB row, older than 24h, candidate for deletion
- `SKIP (young):` — dir has no DB row but is <24h old, left alone
- `DELETED:` — dir was removed (DRY_RUN=false only)
- `DRY_RUN: would delete` — would have deleted (DRY_RUN=true)
- `ERROR:` — DB unreachable or unexpected failure, run aborted

## One-shot orphan deletion (current batch)

As of 2026-05-02, 6 confirmed orphans (all >24h old, all absent from DB):

```
/data/agents/0626b80d-eee2-4dd1-9c24-057a899e522f  [84K]  last modified 2026-04-28
/data/agents/3a3c07c4-616c-4597-a3a7-c38d10beb6b2  [88K]  last modified 2026-05-02
/data/agents/72f0a679-26e5-48d2-bba1-e9fa6596c400  [84K]  last modified 2026-05-01
/data/agents/73d541b7-4b0f-4cd0-9c3b-b89b91edce34  [112K] last modified 2026-05-02
/data/agents/800fa3f0-e4ec-4133-b5f1-b1e60aed9e03  [28K]  last modified 2026-04-29
/data/agents/a0d4e72b-58bc-4f4d-b5db-7be847ab718e  [84K]  last modified 2026-05-01
```

Ready-to-paste deletion commands (paste after your confirmation):

```bash
ssh exzent@178.154.244.194 "
rm -rf /data/agents/0626b80d-eee2-4dd1-9c24-057a899e522f
rm -rf /data/agents/3a3c07c4-616c-4597-a3a7-c38d10beb6b2
rm -rf /data/agents/72f0a679-26e5-48d2-bba1-e9fa6596c400
rm -rf /data/agents/73d541b7-4b0f-4cd0-9c3b-b89b91edce34
rm -rf /data/agents/800fa3f0-e4ec-4133-b5f1-b1e60aed9e03
rm -rf /data/agents/a0d4e72b-58bc-4f4d-b5db-7be847ab718e
echo 'done'
"
```

Note on `3a3c07c4` and `73d541b7`: mtime is 2026-05-02 14:1x — today, but
confirmed absent from DB. Both passed the manual DB check. If unsure, run
script with `MTIME_GATE_HOURS=48` to skip them one more day automatically.
