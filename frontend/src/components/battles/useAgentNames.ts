"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

/**
 * Resolves agent id -> display name for spectator screens.
 *
 * Battle rows carry only agent UUIDs (agent_a_id/agent_b_id) — the battles
 * API never joins in a name (see BattleSummary/BattleDetail). This hook
 * fills the gap with the public GET /agents/{id} profile endpoint, one call
 * per unique id.
 *
 * Cache is module-level (not per-hook-instance) so navigating between the
 * battles list and a battle detail page does not refetch names already
 * resolved elsewhere in the session. A FAILED fetch is never written to the
 * cache — a transient 500/network error must not freeze a wrong-looking
 * fallback in forever; the next render's effect run retries it. Only a
 * SUCCESSFUL response is cached, and a successful-but-nameless profile is
 * cached as an honest `id:<prefix>` label — never as a fabricated name.
 */
const globalNameCache = new Map<string, string>();

// Cap concurrent profile fetches so a battle list with many fighters does
// not fire dozens of requests at once.
const CONCURRENCY = 8;

async function fetchNamesInChunks(
  ids: string[],
  onResolved: (id: string, name: string) => void
): Promise<void> {
  for (let i = 0; i < ids.length; i += CONCURRENCY) {
    const chunk = ids.slice(i, i + CONCURRENCY);
    await Promise.all(
      chunk.map(async (id) => {
        try {
          const res = await fetch(`${API_URL}/api/v1/agents/${id}`);
          if (!res.ok) return; // transient failure — do not cache, allow retry
          const profile: { name?: string } | null = await res.json();
          const name = profile?.name || `id:${id.slice(0, 8)}`;
          globalNameCache.set(id, name);
          onResolved(id, name);
        } catch {
          // network error — do not cache, allow retry on next effect run
        }
      })
    );
  }
}

export function useAgentNames(ids: (string | null | undefined)[]): Map<string, string> {
  const [, forceRender] = useState(0);

  const key = Array.from(new Set(ids.filter((id): id is string => !!id))).sort().join(",");

  useEffect(() => {
    const uniqueIds = key ? key.split(",") : [];
    const missing = uniqueIds.filter((id) => !globalNameCache.has(id));
    if (missing.length === 0) return;
    let alive = true;

    fetchNamesInChunks(missing, () => {
      if (alive) forceRender((n) => n + 1);
    });

    return () => {
      alive = false;
    };
  }, [key]);

  return globalNameCache;
}
