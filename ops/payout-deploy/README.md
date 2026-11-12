# payout-deploy

systemd units and host wiring for [payout-orchestrator](../payout-orchestrator/README.md)
and [payout-signer](../payout-signer/README.md). This directory only ships files. Nothing
here enables a timer, creates a key or changes a server by itself.

All paths below are neutral defaults. Site-specific paths, limits, hostnames and
addresses never go into this repository: put path changes in drop-in overrides
(`systemctl edit <unit>`) and keep real config files on the server only.

## Files

| File | Install as |
|---|---|
| `payout.sysusers.conf` | `/etc/sysusers.d/payout.conf` |
| `payout.tmpfiles.conf` | `/etc/tmpfiles.d/payout.conf` |
| `payout-orchestrator.service`, `payout-orchestrator.timer`, `payout-signer@.service`, `payout-alerts.service` | `/etc/systemd/system/` |

## Layout and modes

| Path | Owner, mode | Content |
|---|---|---|
| `/var/lib/payout-handoff` | `root:payout-handoff`, `2770` | request files (orchestrator, `0640`) and signed files (signer, `0600`) |
| `/var/lib/payout-orchestrator` | `root`, `0700` (StateDirectory) | `journal.json` (`0600`), `journal.lock`, `pause` |
| `/var/lib/payout-signer` | `payout-signer`, `0700` (StateDirectory) | ledger, `pause` |
| `/etc/payout-signer/config.toml` | `root:payout-signer`, `0640` | signer config |
| `/etc/payout-signer/keypair.json` | `root:root`, `0600` | key; delivered to the unit by `LoadCredential=`, never readable by other users |
| `/etc/payout-orchestrator/config.toml` | `root:root`, `0600` | orchestrator config |
| `/opt/payout-orchestrator`, `/opt/payout-signer`, `/opt/payout-alerts` | `root`, not writable by others | installed programs (`bin/payout-orchestrator`, `bin/payout-signer`, `bin/payout-alerts`) |
| `/etc/payout-alerts/config.toml`, `/etc/payout-alerts/bot-token` | `root:root`, `0600` | alert forwarder config (`chat_id`, `prefix`) and Telegram bot token; both reach the unit as credentials (`$CREDENTIALS_DIRECTORY/config`, `/bot-token`) |
| `/var/lib/payout-alerts` | dynamic user, `0700` (StateDirectory) | journal `cursor` |

The handoff directory is writable by the group because the signer creates its output
there; the setgid bit keeps the group on new files. Config values: `handoff_dir`,
`journal_path` and `pause_path` of the orchestrator must match the table;
`ledger_path` and `pause_file` of the signer live under `/var/lib/payout-signer/`,
`keypair_credential = "keypair"`.

## Install order

1. `install -D -m 0644 payout.sysusers.conf /etc/sysusers.d/payout.conf && systemd-sysusers /etc/sysusers.d/payout.conf`
   (`-D`: a stock host may have no `/etc/sysusers.d`; the explicit file argument applies
   only this file, not every sysusers entry on the host).
2. `install -D -m 0644 payout.tmpfiles.conf /etc/tmpfiles.d/payout.conf && systemd-tmpfiles --create /etc/tmpfiles.d/payout.conf`
   (run after step 1: the file names the groups step 1 creates).
3. Create both state directories by hand, before any pause file:
   `install -d -m 0700 /var/lib/payout-orchestrator` and
   `install -d -m 0700 -o payout-signer -g payout-signer /var/lib/payout-signer`.
   `StateDirectory=` creates them only when a unit starts, and a paused orchestrator never
   starts, so `touch` of a pause file would fail.
4. Install the two programs under `/opt` and the config files with the owners and modes above.
5. `install -m 0644 payout-orchestrator.service payout-orchestrator.timer 'payout-signer@.service' /etc/systemd/system/`
6. `systemctl daemon-reload`
7. Backend network config: see "Backend wiring".

## Telegram alerts (optional)

[payout-alerts](../payout-alerts/README.md) forwards payout alerts to one Telegram chat.

1. Install the program under `/opt/payout-alerts` (launcher `bin/payout-alerts` runs
   `/usr/bin/python3 -I -B` with the package directory on `sys.path`), like the other two.
2. `install -D -m 0600 config.toml /etc/payout-alerts/config.toml` (`chat_id = "<chat-id>"`,
   optional `prefix`) and `install -D -m 0600 bot-token /etc/payout-alerts/bot-token`
   (the bot token only, one line; never put it into a unit file or a drop-in).
3. `install -D -m 0644 payout-alerts.service /etc/systemd/system/payout-alerts.service && systemctl daemon-reload`
4. Send one message first, with the unit's own credential setup:
   `systemd-run --wait --pipe -p DynamicUser=yes -p LoadCredential=bot-token:/etc/payout-alerts/bot-token -p LoadCredential=config:/etc/payout-alerts/config.toml /opt/payout-alerts/bin/payout-alerts test`
   must exit 0 and the chat must show the test message.
5. `systemctl enable --now payout-alerts.service`, then `systemctl status payout-alerts.service`.

The unit reads the journal through `SupplementaryGroups=systemd-journal`. Not verified on a
real host: that this group is enough to follow the payout units' entries; check once by
raising a harmless failing unit instance and watching the chat.

## Dry verification (before anything runs)

    systemd-analyze verify /etc/systemd/system/payout-orchestrator.service \
        /etc/systemd/system/payout-orchestrator.timer /etc/systemd/system/payout-signer@x.1.service
    systemd-analyze security payout-orchestrator.service
    systemd-analyze security payout-signer@x.1.service

Record the exposure scores; the signer should be well below the orchestrator. Then, with
the pause file in place (`touch /var/lib/payout-orchestrator/pause`), run
`systemctl start payout-orchestrator.service`: it must skip with a condition message and
change nothing (`systemctl status payout-orchestrator.service`). For a real tick, remove the
pause file and start the service once by hand. Nothing here proves the hardening set
works with the real signer binary; the first manual tick on the host is that proof.

## Result retention (signer instance)

The orchestrator starts `payout-signer@<intent>.<epoch>.service` and then reads
`ExecMainCode`, `ExecMainStatus` and `Result` with `systemctl show`. A oneshot instance
that exits 0 and is inactive can be unloaded by systemd, after which `show` reports defaults
(`ExecMainCode` not `1`) and the orchestrator treats a successful signing as "no signer
status". Failed instances are kept by the default `CollectMode=inactive`.

Decision: `RemainAfterExit=yes` keeps a finished instance loaded (active, exited) with its
exit status, for exit 0 as well as the failures. The cost is that starting the same
instance name again is a no-op and would return the old status; so each tick first runs
`systemctl stop` and `reset-failed` for `payout-signer@*.service` (`ExecStartPre=`). Names
carry the epoch, so within one tick an instance is never started twice. Exited instances
accumulate only until the next tick or reboot.

An active (exited) instance also keeps systemd's copy of the signer credential under
`/run/credentials/<unit>` until the instance stops. So the orchestrator runs
`systemctl stop` on the instance right after it has read the three properties; the exit
status is already in hand and the stop cannot change it. When the status could not be read (timeout, no status), the stop still runs and also ends a signer that may be running; that is safe, because the orchestrator has given up on that signing. A failed stop is only logged as
the alert `signer_stop_failed`; the `ExecStartPre=` cleanup of the next tick remains the
fallback.

Not verified on a real systemd (the build host has none): that an unloaded instance loses
the status. The chosen setting is safe either way. Check on the host once, with a harmless
instance of a copy of the unit that runs `/bin/true`:
`systemctl start t@a.1.service; systemctl show -p ExecMainCode,ExecMainStatus,Result t@a.1.service`
must print `1`, `0`, `success`.

Also check the key copy once on the host: after a tick that signed, run
`ls /run/credentials/`. The finished instance's credential directory must be gone, because
the orchestrator stopped the instance right after `systemctl show`. If it is still there,
look for the `signer_stop_failed` alert; readers of the copy stay limited to `payout-signer`
and root.

First manual tick checklist: if settling or committing a signed file fails with a permission
error, look at `CapabilityBoundingSet=CAP_DAC_READ_SEARCH` in `payout-orchestrator.service`
first (root must read the signer's `0600` output without write bypass).

## Backend wiring

`auto-compile`, `auto-send` and `auto-reconcile` read a private JSON file named by
`PAYOUT_NETWORK_CONFIG_PATH`. The prod compose file mounts a host directory read-only at
`/run/payout-network` only when `PAYOUT_NETWORK_CONFIG_DIR` is set (in `deploy/.env`);
otherwise it mounts an empty placeholder directory and leaves the setting empty, so a stack
without the file starts as before and the payout commands answer `configuration_error`
(fail closed). Put the file at `<that dir>/network.json`, owner `root`, mode `0600`
(group or other bits are refused). A directory is mounted, not the file, so replacing the
file keeps the mount current; restart the backend after changing it. The compose file also
gives the container a small `tmpfs` at `/run/payout`, where `auto-compile` writes request
files that the orchestrator copies out and removes.

## Pause and resume

- Orchestrator: `touch /var/lib/payout-orchestrator/pause` (the unit's
  `ConditionPathExists=!` skips the tick) / `rm` it to resume.
- Signer: `touch /var/lib/payout-signer/pause` / `rm` it. Pause both for a full stop.

## Clearing a hold

Only after investigating the cause (alert reasons: orchestrator README):

1. Pause the orchestrator. Make sure no tick is running (`systemctl is-active payout-orchestrator.service`).
2. Edit `journal.json`: remove **only** the `"hold"` field of that intent. Keep `epoch`
   and `lvbh`: they stop a new signature until the old request has expired. Never delete
   the whole intent key.
3. For `request_malformed` or `signer_rejected`, also remove the `<intent>.<epoch>.*`
   files from the handoff directory. For `commit_*` holds remove the stored
   `<intent>.<epoch>.signed.json` too (it may be truncated); the signer ledger replays
   the same signature on the next run.
4. Resume.

## Enabling the timer

Not part of this change and not done by any step above. Enabling
`payout-orchestrator.timer` is a separate decision of the owner, taken after the hot wallet
is funded and one small manual tick has been verified.
