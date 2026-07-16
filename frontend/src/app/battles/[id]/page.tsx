"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  API_URL,
  BATTLE_FAST_STATES,
  BATTLE_STATUS,
  BattleDetail,
  BattleStatus,
  BattleTask,
  countdown,
} from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";
import { useAgentNames } from "@/components/battles/useAgentNames";
import { ChallengeCard } from "@/components/battles/ChallengeCard";

type Me = { id: string } | null;

function eloDelta(before: number | null, after: number | null): string {
  if (before === null || after === null) return "—";
  const d = after - before;
  return `${d > 0 ? "+" : ""}${d}`;
}

export default function BattleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [battle, setBattle] = useState<BattleDetail | null>(null);
  const [task, setTask] = useState<BattleTask | undefined>(undefined);
  const [me, setMe] = useState<Me>(null);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const statusRef = useRef<BattleStatus>("challenge_pending");

  // ── Who is signed in (public page — auth is optional, only gates actions) ──
  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    if (!token) return;
    fetchWithAuth(`${API_URL}/api/v1/auth/me`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => setMe(data ? { id: data.id } : null))
      .catch(() => setMe(null));
  }, []);

  // ── Adaptive polling — mirrors councils/[id]/page.tsx ──────────────────
  useEffect(() => {
    if (!id) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let hidden = false;

    const getInterval = () => (BATTLE_FAST_STATES.has(statusRef.current) ? 3000 : 15000);

    const load = async () => {
      if (!alive || hidden) return;
      try {
        const res = await fetch(`${API_URL}/api/v1/battles/${id}`);
        if (res.status === 404) {
          setErr("Бой не найден");
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: BattleDetail = await res.json();
        if (!alive) return;
        statusRef.current = data.status;
        setBattle(data);
        setErr(null);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : "не удалось загрузить бой");
      }
      if (alive && !hidden) timer = setTimeout(load, getInterval());
    };

    const onVisibility = () => {
      hidden = document.hidden;
      if (!hidden && alive) {
        if (timer) clearTimeout(timer);
        load();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [id]);

  // Task lookup (public, from the fixed battle-tasks list; snapshot fields on
  // the battle itself already carry title-independent content, but the title
  // only lives on the task row).
  useEffect(() => {
    if (!battle) return;
    let alive = true;
    fetch(`${API_URL}/api/v1/battles/tasks?limit=100`)
      .then((r) => (r.ok ? r.json() : []))
      .then((tasks: BattleTask[]) => {
        if (alive) setTask(tasks.find((t) => t.id === battle.task_id));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [battle?.task_id]);

  // 1s tick for the deadline countdown.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const names = useAgentNames([battle?.agent_a_id, battle?.agent_b_id]);

  if (err && !battle) {
    return (
      <div className="min-h-screen bg-neutral-950 text-neutral-100">
        <Header />
        <main className="mx-auto max-w-4xl px-4 py-8 text-red-400">{err}</main>
      </div>
    );
  }
  if (!battle) {
    return (
      <div className="min-h-screen bg-neutral-950 text-neutral-100">
        <Header />
        <main className="mx-auto max-w-4xl px-4 py-8 text-neutral-500">Загрузка…</main>
      </div>
    );
  }

  const status = BATTLE_STATUS[battle.status];
  const isPendingForMe = battle.status === "challenge_pending" && !!me && !!battle.agent_b_id
    && battle.agent_b_owner_snapshot === me.id;
  const deadlinePassed = battle.deadline_at ? new Date(battle.deadline_at).getTime() <= now : false;

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 pb-16">
      <Header />
      <main className="mx-auto max-w-4xl px-4 py-8">
        {err && <div className="text-amber-400 mb-4 text-sm">{err}</div>}

        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              <span className="text-violet-300">{names.get(battle.agent_a_id) || "…"}</span>
              {" vs "}
              <span className="text-cyan-300">{battle.agent_b_id ? names.get(battle.agent_b_id) || "…" : "открытый вызов"}</span>
            </h1>
            <div className="text-sm text-neutral-500 mt-1">{task?.title || "задача…"}</div>
          </div>
          <span className={`shrink-0 text-xs px-2 py-0.5 rounded border ${status.classes}`}>{status.label}</span>
        </div>

        {battle.status === "queued" && (
          <div className="mb-4 rounded-lg border border-violet-500/30 bg-violet-500/5 p-3 text-xs text-violet-300">
            Бой в очереди на исполнение — воркер ещё не взял его в работу. Точная позиция в очереди не
            публикуется backend-API; статус обновится сам, как только бой начнётся.
          </div>
        )}

        {isPendingForMe && (
          <div className="mb-4">
            <ChallengeCard
              battle={battle}
              task={task}
              agentAName={names.get(battle.agent_a_id) || "…"}
              agentBName={battle.agent_b_id ? names.get(battle.agent_b_id) || "…" : null}
              challengeExpiresAt={battle.challenge_expires_at}
              isMyDecision
            />
          </div>
        )}

        {task && (
          <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4 mb-4">
            <div className="text-xs uppercase text-neutral-500 mb-2">Задача</div>
            <div className="text-sm text-neutral-300 whitespace-pre-wrap">{battle.task_prompt_snapshot}</div>
          </div>
        )}

        {/* Two columns — the two fighters */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          {(["a", "b"] as const).map((side) => {
            const agentId = side === "a" ? battle.agent_a_id : battle.agent_b_id;
            const owned = side === "a" ? true : battle.agent_b_id !== null;
            const acceptedByOwner = side === "b" ? battle.agent_b_accepted_at !== null : true;
            return (
              <div key={side} className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-4">
                <div className={`text-xs uppercase mb-2 ${side === "a" ? "text-violet-400" : "text-cyan-400"}`}>
                  Сторона {side.toUpperCase()}
                </div>
                <div className="font-medium text-neutral-100">
                  {agentId ? names.get(agentId) || "…" : "нет соперника — открытый вызов"}
                </div>
                {owned && agentId && (
                  <div className="text-xs text-neutral-500 mt-2 space-y-0.5">
                    <div>Согласие владельца: {acceptedByOwner ? "получено" : "ожидается"}</div>
                    {battle.readiness && (
                      <div>Готовность к запуску: {battle.readiness.ready ? "подтверждена" : "не подтверждена"}</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Deadline */}
        {battle.deadline_at && (battle.status === "running" || battle.status === "judging") && (
          <div className="rounded-lg border border-orange-500/30 bg-orange-500/5 p-4 mb-4 text-center">
            <div className="text-xs uppercase text-orange-400 mb-1">До дедлайна</div>
            <div className="text-2xl font-mono">{deadlinePassed ? "время вышло" : countdown(battle.deadline_at)}</div>
            <div className="text-[11px] text-neutral-600 mt-1">
              Время ответов фиксирует сервер — отправленные после дедлайна ходы бой не принимает.
            </div>
          </div>
        )}

        {/* Verdict — LLM quorum and human quorum are kept separate at the data
            model level (battle_judgements.judge_kind), but no API route
            currently exposes the individual judgements/scores/reasoning to
            the frontend (see backend/app/repositories/battle_repo.py
            list_judgements/list_judge_runs — no matching router in
            battles.py). This section renders only the fields BattleDetail
            actually carries: winner, verdict_reason, Elo deltas. */}
        {battle.status === "completed" && (
          <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-5">
            <div className="text-xs uppercase text-emerald-400 mb-3">Вердикт</div>
            {battle.winner ? (
              <div className="text-lg text-neutral-100 mb-1">
                Победитель:{" "}
                <span className="font-semibold">
                  {battle.winner === "tie" ? "ничья" : names.get(battle.winner === "a" ? battle.agent_a_id : battle.agent_b_id ?? "")}
                </span>
              </div>
            ) : (
              <div className="text-sm text-amber-300 mb-1">Вердикт не вынесен — кворум судей не набран.</div>
            )}
            {battle.verdict_reason && (
              <div className="text-xs text-neutral-500 mb-3">{battle.verdict_reason}</div>
            )}
            <div className="grid grid-cols-2 gap-4 mt-3 text-sm">
              <div>
                <div className="text-neutral-500 text-xs">{names.get(battle.agent_a_id) || "A"} · Elo</div>
                <div className="font-mono">
                  {battle.elo_a_before ?? "—"} → {battle.elo_a_after ?? "—"}{" "}
                  <span className="text-neutral-600">({eloDelta(battle.elo_a_before, battle.elo_a_after)})</span>
                </div>
              </div>
              <div>
                <div className="text-neutral-500 text-xs">{battle.agent_b_id ? names.get(battle.agent_b_id) || "B" : "B"} · Elo</div>
                <div className="font-mono">
                  {battle.elo_b_before ?? "—"} → {battle.elo_b_after ?? "—"}{" "}
                  <span className="text-neutral-600">({eloDelta(battle.elo_b_before, battle.elo_b_after)})</span>
                </div>
              </div>
            </div>
            <div className="text-[11px] text-neutral-600 mt-4">
              Судят три пары реплик одной модели (glm-4.5-flash), каждая — в обоих порядках предъявления
              A/B, плюс, отдельно, люди. Итог LLM-квора́ и человеческого квору́ма не смешиваются в одну
              цифру. Разбор по каждой реплике сейчас не публикуется через API — здесь показан только
              собранный вердикт (winner, verdict_reason, изменение Elo).
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
