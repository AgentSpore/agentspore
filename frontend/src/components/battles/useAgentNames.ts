"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

/**
 * Resolves agent id -> display name for spectator screens.
 *
 * Battle rows carry only agent UUIDs (agent_a_id/agent_b_id) — the battles
 * API never joins in a name (see BattleSummary/BattleDetail). This hook
 * fills the gap with the public GET /agents/{id} profile endpoint, one call
 * per unique id, cached across re-renders of the same page.
 */
export function useAgentNames(ids: (string | null | undefined)[]): Map<string, string> {
  const [names, setNames] = useState<Map<string, string>>(new Map());

  const key = Array.from(new Set(ids.filter((id): id is string => !!id))).sort().join(",");

  useEffect(() => {
    const uniqueIds = key ? key.split(",") : [];
    const missing = uniqueIds.filter((id) => !names.has(id));
    if (missing.length === 0) return;
    let alive = true;

    Promise.all(
      missing.map((id) =>
        fetch(`${API_URL}/api/v1/agents/${id}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((profile: { name?: string; handle?: string } | null) => [id, profile] as const)
          .catch(() => [id, null] as const)
      )
    ).then((results) => {
      if (!alive) return;
      setNames((prev) => {
        const next = new Map(prev);
        for (const [id, profile] of results) {
          next.set(id, profile?.name || `Агент ${id.slice(0, 8)}`);
        }
        return next;
      });
    });

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return names;
}
