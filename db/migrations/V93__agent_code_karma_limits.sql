CREATE TABLE agent_code_karma_limits (
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    week_start DATE NOT NULL,
    awarded INTEGER NOT NULL DEFAULT 0 CHECK (awarded BETWEEN 0 AND 50),
    PRIMARY KEY (agent_id, week_start),
    CHECK (EXTRACT(ISODOW FROM week_start) = 1)
);

-- INVARIANT(A3): an upgrade must not grant a second budget for rewards already earned.
WITH week AS (
    SELECT date_trunc('week', statement_timestamp() AT TIME ZONE 'UTC') AS starts_at
), prior_rewards AS (
    SELECT activity.agent_id, COUNT(*) * 10 AS points
    FROM agent_activity AS activity CROSS JOIN week
    WHERE activity.action_type = 'code_commit'
      AND NULLIF(activity.metadata->>'commit_sha', '') IS NOT NULL
      AND activity.metadata->>'source' IS DISTINCT FROM 'github_sync'
      AND activity.created_at >= week.starts_at AT TIME ZONE 'UTC'
      AND activity.created_at < (week.starts_at + interval '1 week') AT TIME ZONE 'UTC'
    GROUP BY activity.agent_id
    UNION ALL
    SELECT audit.agent_id, SUM(CASE
        WHEN audit.method = 'PUT' AND audit.path = '/contents' THEN 10
        WHEN audit.method = 'PUT' AND audit.path LIKE '/contents/%' THEN 5
        WHEN audit.method = 'DELETE' AND audit.path LIKE '/contents/%' THEN 3
        ELSE 0
    END) AS points
    FROM agent_github_actions AS audit CROSS JOIN week
    WHERE audit.status_code < 400
      AND audit.created_at >= week.starts_at AT TIME ZONE 'UTC'
      AND audit.created_at < (week.starts_at + interval '1 week') AT TIME ZONE 'UTC'
      AND ((audit.method = 'PUT' AND audit.path = '/contents')
           OR (audit.method IN ('PUT', 'DELETE') AND audit.path LIKE '/contents/%'))
    GROUP BY audit.agent_id
)
INSERT INTO agent_code_karma_limits (agent_id, week_start, awarded)
SELECT prior_rewards.agent_id, week.starts_at::date, LEAST(50, SUM(prior_rewards.points))::integer
FROM prior_rewards CROSS JOIN week
GROUP BY prior_rewards.agent_id, week.starts_at;
