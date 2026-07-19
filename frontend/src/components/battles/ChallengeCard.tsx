"use client";

import { useState } from "react";
import Link from "next/link";
import { API_URL, BATTLE_DIFFICULTY, BattleSummary, countdown } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

interface ChallengeCardProps {
  battle: BattleSummary;
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
export function ChallengeCard({ battle, agentAName, agentBName, challengeExpiresAt, isMyDecision, onResolved }: ChallengeCardProps) {
  const [busy, setBusy] = useState<"accept" | "decline" | "block" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);

  const act = async (action: "accept" | "decline") => {
    setBusy(action);
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
      setBusy(null);
    }
  };

  // Blocks the CHALLENGER's owner (V68 D) — resolved server-side from
  // agent_a_id, so this covers every current and future agent of that owner.
  // Independent of accept/decline: blocking does not itself resolve this
  // challenge, it only prevents future ones.
  const block = async () => {
    setBusy("block");
    setErr(null);
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/battles/blocks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ blocked_agent_id: battle.agent_a_id }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      setBlocked(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "не удалось заблокировать владельца");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl border border-violet-500/30 bg-neutral-900/35 p-4 sm:p-5">
      <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-violet-400 mb-2">Вам бросили вызов</div>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="text-sm text-neutral-300">
            <span className="font-medium text-violet-300">{agentAName}</span> вызывает{" "}
            <span className="font-medium text-cyan-300">{agentBName || "открытый вызов"}</span>
          </div>
          <div className="text-sm text-neutral-100 font-medium mt-1">
            {battle.task_category_filter ?? "Любая категория"} ·{" "}
            {battle.task_difficulty_filter ? BATTLE_DIFFICULTY[battle.task_difficulty_filter] : "любая сложность"}
          </div>
          <div className="text-xs text-neutral-500 mt-0.5">Задача скрыта до готовности обеих сторон</div>
          {challengeExpiresAt && (
            <div className="text-xs text-neutral-500 mt-1">
              Вызов истекает через {countdown(challengeExpiresAt)}
            </div>
          )}
        </div>
        <Link
          href={`/battles/${battle.id}`}
          className="battle-press shrink-0 text-xs px-2.5 py-1 rounded-md border border-neutral-700 text-neutral-400 hover:text-neutral-100 hover:border-neutral-500 transition-colors"
        >
          Открыть
        </Link>
      </div>

      {isMyDecision && (
        <>
          <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-2.5 text-xs text-amber-300">
            <span className="mt-px shrink-0">⚠</span>
            <span>
              Принимая вызов, вы соглашаетесь потратить <b>собственный ключ и бюджет LLM</b> вашего агента на
              прохождение этого боя. Средства спишутся с вашего аккаунта, а не из общего пула платформы.
            </span>
          </div>
          {err && <div className="mt-2 text-xs text-red-400">{err}</div>}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={() => act("accept")}
              disabled={busy !== null}
              className="battle-press inline-flex min-h-11 items-center gap-1.5 text-sm px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
            >
              {busy === "accept" && <span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-white/40 border-t-white animate-spin" />}
              Принять вызов
            </button>
            <button
              onClick={() => act("decline")}
              disabled={busy !== null}
              className="battle-press inline-flex min-h-11 items-center gap-1.5 text-sm px-4 rounded-lg border border-neutral-700 text-neutral-300 hover:bg-white/[0.03] hover:text-red-300 hover:border-red-500/30 disabled:opacity-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
            >
              {busy === "decline" && <span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-current/40 border-t-current animate-spin" />}
              Отклонить
            </button>
            <button
              onClick={block}
              disabled={busy !== null || blocked}
              title="Заблокировать владельца вызывающего агента: он больше не сможет вызывать ваших агентов"
              className="battle-press inline-flex min-h-11 items-center gap-1.5 text-sm px-4 rounded-lg border border-neutral-700 text-neutral-500 hover:bg-white/[0.03] hover:text-amber-300 hover:border-amber-500/30 disabled:opacity-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
            >
              {busy === "block" && <span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-current/40 border-t-current animate-spin" />}
              {blocked ? "Владелец заблокирован" : "Заблокировать владельца"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
