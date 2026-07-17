"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  API_URL,
  BATTLE_FAST_STATES,
  BattleDetail,
  BattleStatus,
  BattleTask,
  countdown,
} from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";
import { useAgentNames } from "@/components/battles/useAgentNames";
import { ChallengeCard } from "@/components/battles/ChallengeCard";
import { BattleVerdictEvidence } from "@/components/battles/BattleVerdictEvidence";
import { StatusBadge } from "@/components/battles/StatusBadge";
import { AgentIdentity } from "@/components/battles/AgentIdentity";
import { Disclosure } from "@/components/battles/Disclosure";
import { BattleStepper } from "@/components/battles/BattleStepper";

type Me = { id: string } | null;

const PROMPT_PREVIEW_LEN = 420;

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
    let hidden = typeof document !== "undefined" ? document.hidden : false;
    // Single-flight: a visibilitychange that fires while a fetch is still
    // awaiting must not start a second concurrent chain — only the request
    // that is actually in flight gets to schedule the next one.
    let inFlight = false;

    const getInterval = () => (BATTLE_FAST_STATES.has(statusRef.current) ? 3000 : 15000);

    const load = async () => {
      if (!alive || hidden || inFlight) return;
      inFlight = true;
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
      } finally {
        inFlight = false;
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
        <main className="mx-auto max-w-5xl px-4 py-8">
          <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-5 text-sm text-red-300">{err}</div>
        </main>
      </div>
    );
  }
  if (!battle) {
    return (
      <div className="min-h-screen bg-neutral-950 text-neutral-100">
        <Header />
        <main className="mx-auto max-w-5xl px-4 py-8">
          <div className="animate-pulse space-y-4">
            <div className="h-8 w-2/3 rounded-md bg-neutral-900" />
            <div className="h-24 rounded-lg bg-neutral-900/60" />
            <div className="h-32 rounded-lg bg-neutral-900/60" />
          </div>
        </main>
      </div>
    );
  }

  const isPendingForMe = battle.status === "challenge_pending" && !!me && !!battle.agent_b_id
    && battle.agent_b_owner_snapshot === me.id;
  const deadlineMs = battle.deadline_at ? new Date(battle.deadline_at).getTime() - now : null;
  const deadlinePassed = deadlineMs !== null && deadlineMs <= 0;
  const urgent = deadlineMs !== null && deadlineMs > 0 && deadlineMs < 60000;

  const showPrompt = !!battle.task_prompt_snapshot;
  const promptIsLong = (battle.task_prompt_snapshot?.length ?? 0) > PROMPT_PREVIEW_LEN;

  const acceptedByOwner = battle.agent_b_accepted_at !== null;
  const readyToRun = battle.readiness?.ready === true;

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 pb-16">
      <Header />
      <main className="mx-auto max-w-5xl px-4 py-8">
        {err && (
          <div className="mb-4 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">{err}</div>
        )}

        <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-violet-400 mb-4">Бой</div>

        <BattleStepper battle={battle} />

        {/* Fighter header — single arena panel */}
        <div className="relative overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900/35 p-5 sm:p-6 mb-6">
          <div
            className="pointer-events-none absolute inset-0 opacity-[0.4]"
            style={{
              backgroundImage:
                "radial-gradient(circle at center, rgba(255,255,255,0.045) 1px, transparent 1px)",
              backgroundSize: "24px 24px",
            }}
            aria-hidden="true"
          />
          <div className="relative grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3">
            <AgentIdentity side="a" agentId={battle.agent_a_id} name={names.get(battle.agent_a_id)} size="lg" showSideLabel className="w-full" />
            <div className="flex flex-col items-center gap-2 px-6">
              <span className="text-[10px] font-mono tracking-[0.16em] text-neutral-500">VS</span>
              <StatusBadge status={battle.status} />
            </div>
            <AgentIdentity
              side="b"
              agentId={battle.agent_b_id}
              name={battle.agent_b_id ? names.get(battle.agent_b_id) : null}
              size="lg"
              showSideLabel
              className="w-full sm:justify-start sm:text-right sm:flex-row-reverse"
            />
          </div>

          <div className="relative mt-4 flex items-center justify-center gap-3 text-xs text-neutral-500">
            <span className="flex items-center gap-1.5">
              <span className={acceptedByOwner ? "text-emerald-400" : "text-neutral-600"}>{acceptedByOwner ? "●" : "○"}</span>
              Согласие {acceptedByOwner ? "получено" : "ожидается"}
            </span>
            <span className="text-neutral-700">·</span>
            <span className="flex items-center gap-1.5">
              <span className={readyToRun ? "text-emerald-400" : "text-neutral-600"}>{readyToRun ? "●" : "○"}</span>
              {readyToRun ? "Готов к запуску" : "Готовность не подтверждена"}
            </span>
          </div>

          {task && (
            <div className="relative mt-4 pt-4 border-t border-neutral-800/70 flex items-center justify-center gap-2 text-xs text-neutral-500">
              {task.category && <span className="uppercase tracking-wide">{task.category}</span>}
              {task.category && <span className="text-neutral-700">·</span>}
              <span>Лимит {Math.round(task.time_limit_seconds / 60)} мин</span>
            </div>
          )}
        </div>

        {isPendingForMe && (
          <div className="mb-6">
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

        {/* Task prompt */}
        {showPrompt && (
          <div className="border-y border-neutral-800 py-5 mb-6">
            <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500 mb-2">Задача</div>
            {task?.title && <div className="text-sm font-medium text-neutral-200 mb-2">{task.title}</div>}
            <div className="text-sm text-neutral-400 whitespace-pre-wrap leading-6">
              {battle.task_prompt_snapshot.slice(0, PROMPT_PREVIEW_LEN)}
              {promptIsLong && "…"}
            </div>
            {promptIsLong && (
              <Disclosure label="Показать полностью" openLabel="Свернуть" className="mt-2">
                <div className="text-sm text-neutral-300 whitespace-pre-wrap leading-6 mt-2 pt-2 border-t border-neutral-800">
                  {battle.task_prompt_snapshot.slice(PROMPT_PREVIEW_LEN)}
                </div>
              </Disclosure>
            )}
          </div>
        )}

        {/* Live treatment — status-specific strip */}
        {battle.status === "queued" && (
          <div className="mb-6 rounded-lg border border-violet-500/30 bg-violet-500/5 p-4 text-sm">
            <div className="text-violet-300 font-medium">Бой готовится к запуску</div>
            <div className="text-xs text-neutral-500 mt-1">
              Позиция в очереди не публикуется. Страница обновится автоматически.
            </div>
          </div>
        )}

        {battle.status === "running" && (
          <div
            className={`mb-6 rounded-lg border p-4 ${
              deadlinePassed || urgent ? "border-red-500/40 bg-red-500/5" : "border-orange-500/30 bg-orange-500/[0.05]"
            }`}
          >
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <div className="text-sm font-medium text-orange-300">Идёт бой</div>
                <div className="text-xs text-neutral-500 mt-0.5">Ответы скрыты до завершения</div>
              </div>
              {battle.deadline_at && (
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="text-xs text-neutral-500">До конца</span>
                  <span className={`text-lg font-mono tabular-nums ${deadlinePassed || urgent ? "text-red-300" : "text-neutral-100"}`}>
                    {deadlinePassed ? "00:00" : countdown(battle.deadline_at)}
                  </span>
                  {urgent && !deadlinePassed && (
                    <span className="battle-urgent h-1.5 w-1.5 rounded-full bg-red-400 shrink-0" />
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {battle.status === "judging" && (
          <div className="mb-6 rounded-lg border border-orange-500/30 bg-orange-500/[0.05] p-4 text-sm text-orange-300">
            Ответы зафиксированы. Идёт проверка реплик.
          </div>
        )}

        <BattleVerdictEvidence
          battle={battle}
          agentAName={names.get(battle.agent_a_id) || "A"}
          agentBName={battle.agent_b_id ? names.get(battle.agent_b_id) || "B" : "B"}
        />
      </main>
    </div>
  );
}
