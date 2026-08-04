"use client";

import { useEffect, useState } from "react";
import { API_URL, BattleContender } from "@/lib/api";

// Module-level cache: the contender roster is small and near-static (V72),
// so unlike useAgentNames (one fetch per battle-specific agent id) this
// fetches the whole roster once per session and every mount reuses it.
let cache: Map<string, BattleContender> | null = null;
let inFlight: Promise<Map<string, BattleContender>> | null = null;

async function loadContenders(): Promise<Map<string, BattleContender>> {
  if (cache) return cache;
  if (!inFlight) {
    inFlight = fetch(`${API_URL}/api/v1/battles/contenders`)
      .then((res) => (res.ok ? res.json() : []))
      .then((rows: BattleContender[]) => {
        cache = new Map(rows.map((c) => [c.id, c]));
        return cache;
      })
      .catch(() => new Map<string, BattleContender>());
  }
  return inFlight;
}

/** Resolves id -> BattleContender for every platform contender, fetched once. */
export function useContenders(): Map<string, BattleContender> {
  const [contenders, setContenders] = useState<Map<string, BattleContender>>(() => cache ?? new Map());

  useEffect(() => {
    if (cache) return;
    let alive = true;
    loadContenders().then((resolved) => {
      if (alive) setContenders(resolved);
    });
    return () => {
      alive = false;
    };
  }, []);

  return contenders;
}

/**
 * "Mistral Small · Direct" — the seeded display_name already carries both the
 * model and its approach, and in better words than approach_key would give
 * ("Step by step", not "Stepwise"). Appending the key repeated the approach.
 */
export function contenderLabel(c: BattleContender | undefined): string | undefined {
  return c?.display_name;
}

/**
 * Resolved display name for one battle side: a real agent, a platform
 * contender ("…" while its roster entry is still loading), or null for a
 * genuinely open side (no fighter assigned yet).
 */
export function sideName(
  agentId: string | null | undefined,
  contenderId: string | null | undefined,
  agentNames: Map<string, string>,
  contenders: Map<string, BattleContender>
): string | null {
  if (agentId) return agentNames.get(agentId) || "…";
  if (contenderId) return contenderLabel(contenders.get(contenderId)) ?? "…";
  return null;
}
