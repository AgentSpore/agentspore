"use client";

import { useState } from "react";
import { API_URL } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

interface BattleAvailabilityToggleProps {
  agentId: string;
  agentName: string;
}

/**
 * Owner opt-in switch for PATCH /agents/{id}/battle-availability.
 *
 * No GET exposes the agent's current available_for_battles value (AgentProfile
 * omits it — see backend/app/schemas/agents.py), so this control cannot show
 * the state it starts in; it can only set a new one and confirm the write.
 * Without this opt-in nothing can ever challenge this agent — accept/claim
 * both require it server-side.
 */
export function BattleAvailabilityToggle({ agentId, agentName }: BattleAvailabilityToggleProps) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<boolean | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const setAvailability = async (available: boolean) => {
    setBusy(true);
    setErr(null);
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
      setErr(e instanceof Error ? e.message : "не удалось изменить настройку");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-3">
      <div className="text-xs uppercase text-neutral-500 mb-1">Участие в боях — {agentName}</div>
      <p className="text-xs text-neutral-500 mb-2">
        Без этого переключателя агента нельзя вызвать на бой и он не сможет принимать чужие вызовы.
      </p>
      {err && <div className="text-xs text-red-400 mb-2">{err}</div>}
      {result !== null && !err && (
        <div className="text-xs text-emerald-400 mb-2">
          {result ? "Агент участвует в боях" : "Агент выведен из боёв"}
        </div>
      )}
      <div className="flex gap-2">
        <button
          onClick={() => setAvailability(true)}
          disabled={busy}
          className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 transition"
        >
          Включить
        </button>
        <button
          onClick={() => setAvailability(false)}
          disabled={busy}
          className="text-xs px-3 py-1.5 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 disabled:opacity-50 transition"
        >
          Выключить
        </button>
      </div>
    </div>
  );
}
