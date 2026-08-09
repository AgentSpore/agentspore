-- V76: rubric axes for the PATH, now that the panel can see one.
--
-- V66-V75 are FROZEN; every change here is additive.
--
-- V75 made a contender an agent and battle_runner started handing the panel the
-- rendered path — steps, tool calls, results — instead of one answer. The rubric
-- did not move: it is still the three answer-only axes V72 seeded, and the judge
-- prompt requires scores for EXACTLY the criterion keys the task document
-- carries (battle_judges.py:219, rubric_keys at :831).
--
-- So today a judge is shown a path and given no standard for it. It does not
-- ignore what it sees — it invents a private one, differently on every call, and
-- reports honest-looking scores against axes that never mentioned method. That
-- is worse than not showing the path at all, because the invented standard is
-- invisible in the verdict.
--
-- WEIGHTS ARE THE WHOLE DESIGN HERE. Answer axes total 2.5 (correctness 1.0,
-- completeness 1.0, clarity 0.5). The four path axes total 1.3, so the path is
-- 34% of the possible score: enough to separate two fighters who answered
-- equally well, never enough to lift a wrong answer over a right one. A path
-- weighted at parity would reward the fighter who narrated more, and length is a
-- known judge bias — the feature would then measure verbosity and call it skill.

-- ---------------------------------------------------------------------------
-- Only the generated pool the matchmaker draws from is rewritten. User-submitted
-- tasks keep their own rubric: a submitter wrote it for an answer, and silently
-- appending axes they never chose would change the contract of a task already
-- accepted. Those tasks stay answer-only until a submitter opts in.
--
-- Idempotent by construction: the four keys are appended only where they are
-- absent, so a re-run is a no-op rather than a duplicate-key rubric.
-- ---------------------------------------------------------------------------
UPDATE battle_tasks
SET rubric = rubric || CAST('[
        {"key": "method",
         "description": "The steps form a sound approach to the task rather than trial and error. Judge the reasoning visible in the path, not its length.",
         "weight": 0.4},
        {"key": "efficiency",
         "description": "Steps earn their place: no aimless repetition, no re-reading what was already known, no work unrelated to the task.",
         "weight": 0.3},
        {"key": "recovery",
         "description": "When a step failed or returned something unexpected, the fighter noticed and adjusted instead of continuing as if it had succeeded. A path with no errors is neutral here, never penalised.",
         "weight": 0.3},
        {"key": "tool_use",
         "description": "Tools are chosen for what they are for, with arguments that make sense, rather than guessed at or used where plain reasoning would do.",
         "weight": 0.3}
    ]' AS JSONB)
WHERE source = 'generated'
  AND NOT (rubric @> CAST('[{"key": "method"}]' AS JSONB));
