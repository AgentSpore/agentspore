-- V82: retire the zai/glm-4.5-flash contender — no key configured on prod.
--
-- V72-V81 are FROZEN; every change here is additive.
--
-- Measured on production 2026-08-18: 31 battles / 6h, zero verdicts, every
-- submission "provider unreachable: provider error or timeout". Root cause
-- (fixed in code, not here): a contender was called with the JUDGE's
-- credentials rather than its own, so zai/glm-4.5-flash (empty zai_api_key on
-- prod) was silently routed to whatever provider the judge resolved to —
-- misdiagnosed as an outage instead of a missing key.
--
-- zai/glm-4.5-flash is ALSO JUDGE_MODEL (battle_judges.py) and the head of
-- settings.battle_judge_models — that roster is code, not this table, and is
-- out of scope here. provider_health.pick_live_model already treats a
-- no-configured-key candidate as `no_api_key` and skips it without a network
-- call, so the dead judge seat does not block the panel as long as at least
-- one other roster model resolves.
--
-- Disabled, not deleted (V72's ON DELETE RESTRICT still applies, and the Elo
-- history of real matches stays readable). A battle already holding this
-- contender keeps it via get_contender, which reads by id regardless of
-- `enabled` — an in-flight battle still finishes and settles normally.
UPDATE battle_contenders
SET enabled = FALSE, updated_at = NOW()
WHERE provider = 'zai'
  AND model_id = 'glm-4.5-flash'
  AND enabled;
