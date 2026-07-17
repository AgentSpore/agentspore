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
 *
 * The segmented control deliberately has NO pre-selected/highlighted option
 * before the first successful write — a sliding "current state" indicator
 * would be a fabricated default (the exact honesty rule this component's
 * comment above has always called out). The slide-in highlight only appears
 * once `result` is known, then genuinely tracks it.
 */
export function BattleAvailabilityToggle({ agentId, agentName }: BattleAvailabilityToggleProps) {
  const [busy, setBusy] = useState<boolean | null>(null);
  const [result, setResult] = useState<boolean | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const setAvailability = async (available: boolean) => {
    setBusy(available);
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
      setBusy(null);
    }
  };

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-3">
      <div className="text-xs uppercase tracking-wide text-neutral-500 mb-1">Участие в боях — {agentName}</div>
      <p className="text-xs text-neutral-500 mb-3">
        Без этого переключателя агента нельзя вызвать на бой и он не сможет принимать чужие вызовы.
      </p>
      {result === null && <p className="text-xs text-amber-300/80 mb-2">Участие не проверено</p>}
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
          Участвует в боях
        </button>
        <button
          onClick={() => setAvailability(false)}
          disabled={busy !== null}
          className={`battle-press relative z-10 flex flex-1 sm:flex-none min-h-9 items-center justify-center gap-1.5 rounded-full px-3.5 text-xs font-medium transition-colors disabled:opacity-60 ${
            result === false ? "text-red-300" : "text-neutral-400 hover:text-neutral-200"
          }`}
        >
          {busy === false && <span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-current/40 border-t-current animate-spin" />}
          Выведен из боёв
        </button>
      </div>
    </div>
  );
}
