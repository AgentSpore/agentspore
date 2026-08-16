-- V79: llm7 gives the roster a live contender pool again.
--
-- V72-V78 are FROZEN; every change here is additive.
--
-- Measured live from production 2026-08-16: every paid provider (mistral,
-- deepseek, moonshot) now returns 402/429-insufficient-balance on every
-- completion, leaving zai/glm-4.5-flash as the sole living contender out of
-- nine enabled rows. 72 battles ended in 12h with zero verdicts — not a judge
-- defect, a dead-fighter defect: the matchmaker kept drawing models that
-- cannot answer.
--
-- llm7.io needs no signup, no key, no payment (base_url wired in
-- OpenRouterService.EXTRA_PROVIDERS, 'llm7', key_optional). Four of its free
-- models were verified live to return finish_reason='stop' with real content
-- under the judge-style prompt; two more (gpt-oss:20b, minimax-m2.7) returned
-- finish_reason='length' with empty/truncated content at the same token cap
-- and are deliberately NOT seeded here.

-- ---------------------------------------------------------------------------
-- Retire every mistral contender: the account now returns 402 on every call,
-- confirmed live today. Disabled, not deleted (V72's ON DELETE RESTRICT still
-- applies, and the Elo history of real matches stays readable).
-- ---------------------------------------------------------------------------
UPDATE battle_contenders
SET enabled = FALSE, updated_at = NOW()
WHERE provider = 'mistral'
  AND enabled;

-- ---------------------------------------------------------------------------
-- Seed the four verified-live llm7 models, reusing the existing approach
-- prompts (V72) so a contender's wording never drifts from its label.
-- ---------------------------------------------------------------------------
INSERT INTO battle_contenders
    (display_name, provider, model_id, approach_key, system_prompt)
SELECT m.display, 'llm7', m.model_id, a.approach_key, a.system_prompt
FROM (VALUES
    ('DeepSeek V4 Flash (llm7) · Direct',        'DeepSeek-V4-Flash-0731',    'direct'),
    ('Codestral (llm7) · Step by step',          'codestral-latest',          'stepwise'),
    ('Gemini 3.1 Flash Lite (llm7) · Direct',    'gemini-3.1-flash-lite',     'direct'),
    ('Mistral Nemo (llm7) · Draft, critique, revise',
                                                  'mistral-Nemo-Instruct-2407', 'draft_critique_revise')
) AS m(display, model_id, approach)
JOIN (
    SELECT DISTINCT ON (approach_key) approach_key, system_prompt
    FROM battle_contenders
    ORDER BY approach_key, created_at
) AS a ON a.approach_key = m.approach
ON CONFLICT (provider, model_id, approach_key) DO NOTHING;
