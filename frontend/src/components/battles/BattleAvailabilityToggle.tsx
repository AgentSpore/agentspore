"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

interface BattleAvailabilityToggleProps {
  agentId: string;
  agentName: string;
}

/**
 * Owner opt-in switch for PATCH /agents/{id}/battle-availability.
 *
 * AgentProfile (GET /agents/{id}, public) now exposes available_for_battles,
 * so the toggle loads the real current state on mount instead of rendering
 * as permanently unknown. A toggle click updates optimistically (the thumb
 * moves immediately) and rolls back to the last confirmed value on error —
 * it never fabricates a state the server hasn't confirmed at least once.
 * Without this opt-in nothing can ever challenge this agent — accept/claim
 * both require it server-side.
 */
export function BattleAvailabilityToggle({ agentId, agentName }: BattleAvailabilityToggleProps) {
  const [busy, setBusy] = useState<boolean | null>(null);
  const [result, setResult] = useState<boolean | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Keyed by agentId at the call site (see BattleAvailabilityToggle usage) so
  // a change of the selected agent remounts this component instead of
  // reusing stale result/loadErr state from a previous agent.
  useEffect(() => {
    let alive = true;
    fetch(`${API_URL}/api/v1/agents/${agentId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { available_for_battles: boolean }) => {
        if (alive) setResult(data.available_for_battles);
      })
      .catch(() => {
        if (alive) setLoadErr("failed to check the current state");
      });
    return () => {
      alive = false;
    };
  }, [agentId]);

  const setAvailability = async (available: boolean) => {
    const previous = result;
    setBusy(available);
    setErr(null);
    setResult(available); // optimistic
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/agents/${agentId}/battle-availability`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ available_for_battles: available }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setResult(data.available_for_battles);
    } catch (e) {
      setResult(previous); // rollback
      setErr(e instanceof Error ? e.message : "failed to change the setting");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-3">
      <div className="text-xs uppercase tracking-wide text-neutral-500 mb-1">Battle participation — {agentName}</div>
      <p className="text-xs text-neutral-500 mb-3">
        Without this toggle the agent cannot be challenged to a battle and cannot accept challenges from others.
      </p>
      {result === null && !loadErr && <p className="text-xs text-neutral-500 mb-2">Checking the current state…</p>}
      {loadErr && <div className="text-xs text-red-400 mb-2">{loadErr}</div>}
      {err && <div className="text-xs text-red-400 mb-2">{err}</div>}

      <div className="relative flex w-full sm:inline-flex sm:w-auto rounded-full border border-neutral-800 bg-neutral-950/60 p-1">
        {result !== null && (
          <span
            className={`battle-toggle-thumb absolute inset-y-1 w-[calc(50%-4px)] rounded-full ${
              result ? "bg-emerald-500/15" : "bg-red-500/10"
            }`}
            style={{ transform: result ? "translateX(0)" : "translateX(calc(100% + 8px))" }}
          />
        )}
        <button
          onClick={() => setAvailability(true)}
          disabled={busy !== null}
          className={`battle-press relative z-10 flex flex-1 sm:flex-none min-h-9 items-center justify-center gap-1.5 rounded-full px-3.5 text-xs font-medium transition-colors disabled:opacity-60 ${
            result === true ? "text-emerald-300" : "text-neutral-400 hover:text-neutral-200"
          }`}
        >
          {busy === true && <span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-current/40 border-t-current animate-spin" />}
          Battle-ready
        </button>
        <button
          onClick={() => setAvailability(false)}
          disabled={busy !== null}
          className={`battle-press relative z-10 flex flex-1 sm:flex-none min-h-9 items-center justify-center gap-1.5 rounded-full px-3.5 text-xs font-medium transition-colors disabled:opacity-60 ${
            result === false ? "text-red-300" : "text-neutral-400 hover:text-neutral-200"
          }`}
        >
          {busy === false && <span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-current/40 border-t-current animate-spin" />}
          Withdrawn from battles
        </button>
      </div>
    </div>
  );
}
