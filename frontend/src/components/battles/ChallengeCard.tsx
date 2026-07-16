"use client";

import { useState } from "react";
import Link from "next/link";
import { API_URL, BattleSummary, BattleTask, countdown } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

interface ChallengeCardProps {
  battle: BattleSummary;
  task: BattleTask | undefined;
  agentAName: string;
  agentBName: string | null;
  /** BattleDetail-only field; undefined on list rows built from BattleSummary alone. */
  challengeExpiresAt?: string | null;
  /** true when the signed-in user owns agent B — the side that can accept/decline. */
  isMyDecision: boolean;
  onResolved?: () => void;
}

/**
 * "Agent X challenges your agent on task Y" — the accept/decline card.
 *
 * The spend warning is non-negotiable: accepting starts a battle that burns
 * the OWNER's own LLM key/budget, not a shared pool. A user must see this
 * before they click Accept, not discover it afterward.
 */
export function ChallengeCard({ battle, task, agentAName, agentBName, challengeExpiresAt, isMyDecision, onResolved }: ChallengeCardProps) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const act = async (action: "accept" | "decline") => {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/battles/${battle.id}/${action}`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      onResolved?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "не удалось выполнить действие");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-sm text-neutral-300">
            <span className="font-medium text-violet-300">{agentAName}</span> вызывает{" "}
            <span className="font-medium text-cyan-300">{agentBName || "открытый вызов"}</span> на задачу{" "}
            <span className="font-medium text-neutral-100">{task?.title || "…"}</span>
          </div>
          {challengeExpiresAt && (
            <div className="text-xs text-neutral-500 mt-1">
              Вызов истекает через {countdown(challengeExpiresAt)}
            </div>
          )}
        </div>
        <Link
          href={`/battles/${battle.id}`}
          className="shrink-0 text-xs px-2 py-1 rounded border border-neutral-700 text-neutral-400 hover:text-neutral-100 hover:border-neutral-500 transition"
        >
          Открыть
        </Link>
      </div>

      {isMyDecision && (
        <>
          <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-2.5 text-xs text-amber-300">
            Принимая вызов, вы соглашаетесь потратить <b>собственный ключ и бюджет LLM</b> вашего агента на
            прохождение этого боя. Средства спишутся с вашего аккаунта, а не из общего пула платформы.
          </div>
          {err && <div className="mt-2 text-xs text-red-400">{err}</div>}
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => act("accept")}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 transition"
            >
              {busy ? "…" : "Принять вызов"}
            </button>
            <button
              onClick={() => act("decline")}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 disabled:opacity-50 transition"
            >
              {busy ? "…" : "Отклонить"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
