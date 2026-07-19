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
 * resolved elsewhere in the session. Session-lifetime cache is accepted —
 * no TTL/rename-staleness handling (deferred, out of scope for this fix).
 *
 * A FAILED fetch is never written to the cache — a transient 500/network
 * error must not freeze a wrong-looking fallback in forever; the id stays
 * unresolved so the next mount's effect retries it. Only a SUCCESSFUL
 * response is cached, and a successful-but-nameless profile is cached as an
 * honest `id:<prefix>` label — never as a fabricated name.
 *
 * In-flight requests are deduped by id at module level: two hook instances
 * mounted at once (e.g. the list and a card inside it) that need the same
 * id share one fetch instead of firing it twice. `subscribers` is the
 * fan-out for that — every mounted hook instance registers a re-render
 * callback, and any resolution (regardless of which instance triggered the
 * fetch) notifies all of them, so a peer that didn't request the id still
 * sees the name once it resolves.
 */
const globalNameCache = new Map<string, string>();
const inFlight = new Map<string, Promise<void>>();
const subscribers = new Set<() => void>();

function notifySubscribers(): void {
  subscribers.forEach((notify) => notify());
}

// Cap concurrent profile fetches so a battle list with many fighters does
// not fire dozens of requests at once. Shared across all hook instances —
// a simple counting semaphore, not a per-call batch.
const CONCURRENCY = 8;
let activeCount = 0;
const waiters: (() => void)[] = [];

function acquireSlot(): Promise<void> {
  if (activeCount < CONCURRENCY) {
    activeCount++;
    return Promise.resolve();
  }
  return new Promise((resolve) => waiters.push(resolve));
}

function releaseSlot(): void {
  activeCount--;
  const next = waiters.shift();
  if (next) {
    activeCount++;
    next();
  }
}

function fetchOne(id: string): Promise<void> {
  const existing = inFlight.get(id);
  if (existing) return existing;

  const request = (async () => {
    await acquireSlot();
    try {
      const res = await fetch(`${API_URL}/api/v1/agents/${id}`);
      if (!res.ok) return; // transient failure — do not cache, allow retry
      const profile: { name?: string } | null = await res.json();
      globalNameCache.set(id, profile?.name || `id:${id.slice(0, 8)}`);
    } catch {
      // network error — do not cache, allow retry on next mount's effect
    } finally {
      releaseSlot();
      inFlight.delete(id);
      notifySubscribers();
    }
  })();

  inFlight.set(id, request);
  return request;
}

export function useAgentNames(ids: (string | null | undefined)[]): Map<string, string> {
  const [, forceRender] = useState(0);

  const key = Array.from(new Set(ids.filter((id): id is string => !!id))).sort().join(",");

  // Subscribe once per mount — any id's resolution anywhere re-renders this instance.
  useEffect(() => {
    const notify = () => forceRender((n) => n + 1);
    subscribers.add(notify);
    return () => {
      subscribers.delete(notify);
    };
  }, []);

  useEffect(() => {
    const uniqueIds = key ? key.split(",") : [];
    const missing = uniqueIds.filter((id) => !globalNameCache.has(id));
    for (const id of missing) {
      void fetchOne(id);
    }
  }, [key]);

  return globalNameCache;
}
