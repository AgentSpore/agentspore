-- V80: two more llm7 contenders to widen the pool below judge failure.
--
-- V72-V79 are FROZEN; every change here is additive.
--
-- Production is still voiding battles from provider contention: 5 enabled
-- contenders (4 llm7 + zai/glm-4.5-flash) share ONE llm7 account at ~1 req/8s,
-- and the judge panel draws on the same account. In 3 of the last 4 voids
-- exactly ONE side failed and both sides were llm7 models — the pool is too
-- small, not the judge.
--
-- gpt-oss:20b and minimax-m2.7 were excluded by V79 for returning
-- finish_reason='length' with empty/truncated content — but that verdict was
-- taken at the JUDGE token cap. Re-probed today from the production host with
-- a 700-token cap and a plain question:
--     gpt-oss:20b      finish=stop  len=106
--     minimax-m2.7     finish=stop  len=140
--     codestral-latest finish=stop  len=113  (control — already a contender)
-- Both are usable as CONTENDERS, where an answer gets a generous budget. They
-- remain UNUSABLE as JUDGES, where the cap is tight and they spend it on
-- reasoning instead of the verdict — do NOT add either to
-- settings.battle_judge_models.
-- ---------------------------------------------------------------------------
INSERT INTO battle_contenders
    (display_name, provider, model_id, approach_key, system_prompt)
SELECT m.display, 'llm7', m.model_id, a.approach_key, a.system_prompt
FROM (VALUES
    ('GPT-OSS 20B (llm7) · Direct',            'gpt-oss:20b',    'direct'),
    ('MiniMax M2.7 (llm7) · Step by step',      'minimax-m2.7',   'stepwise')
) AS m(display, model_id, approach)
JOIN (
    SELECT DISTINCT ON (approach_key) approach_key, system_prompt
    FROM battle_contenders
    ORDER BY approach_key, created_at
) AS a ON a.approach_key = m.approach
ON CONFLICT (provider, model_id, approach_key) DO NOTHING;
