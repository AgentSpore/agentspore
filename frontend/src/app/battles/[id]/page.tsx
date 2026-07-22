"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  API_URL,
  BATTLE_FAST_STATES,
  BattleDetail,
  BattleStatus,
} from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";
import { useAgentNames } from "@/components/battles/useAgentNames";
import { ChallengeCard } from "@/components/battles/ChallengeCard";
import { BattleStepper } from "@/components/battles/BattleStepper";
import { BattleHeader } from "@/components/battles/BattleHeader";
import { BattleFighters } from "@/components/battles/BattleFighters";
import { TaskBlock } from "@/components/battles/TaskBlock";
import { BattleTimeline } from "@/components/battles/BattleTimeline";
import { BattleVerdict } from "@/components/battles/BattleVerdict";
import { ReplicasProgress } from "@/components/battles/ReplicasProgress";
import { SectionHead } from "@/components/battles/battleUi";

// Terminal states — polling stops for good once the battle lands here. Every
// other status keeps the page polling (fast while live, slow while waiting).
const POLLING_DONE = new Set<BattleStatus>(["completed", "declined", "expired", "aborted"]);

export default function BattleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [battle, setBattle] = useState<BattleDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const statusRef = useRef<BattleStatus>("challenge_pending");

  // ── Adaptive polling — mirrors councils/[id]/page.tsx ──────────────────
  // Polls every 5s while the battle is live (reserved/queued/running/judging
  // per BATTLE_FAST_STATES), every 10s while it waits on a human, and stops
  // entirely once the battle reaches a terminal state (completed/declined/
  // expired/aborted) — a finished battle never changes again. No websockets.
  useEffect(() => {
    if (!id) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let hidden = typeof document !== "undefined" ? document.hidden : false;
    // Single-flight: a visibilitychange that fires while a fetch is still
    // awaiting must not start a second concurrent chain — only the request
    // that is actually in flight gets to schedule the next one.
    let inFlight = false;

    const getInterval = () => (BATTLE_FAST_STATES.has(statusRef.current) ? 5000 : 10000);

    const load = async () => {
      // The initial (and any visibility-triggered) fetch must run even when the
      // tab is hidden — a battle opened in a background tab should still fill in.
      // Only the polling re-schedule below gates on `hidden`, so a hidden tab
      // fetches once and then stops until it becomes visible again.
      if (!alive || inFlight) return;
      inFlight = true;
      try {
        const res = await fetchWithAuth(`${API_URL}/api/v1/battles/${id}`);
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
      if (alive && !hidden && !POLLING_DONE.has(statusRef.current)) {
        timer = setTimeout(load, getInterval());
      }
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

  // 1s tick for the deadline countdown — only while a battle is running.
  useEffect(() => {
    if (battle?.status !== "running") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [battle?.status]);

  const names = useAgentNames([battle?.agent_a_id, battle?.agent_b_id]);

  if (err && !battle) {
    return (
      <div className="min-h-screen bg-neutral-950 text-neutral-100">
        <Header />
        <main className="mx-auto max-w-[1200px] px-4 py-8">
          <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-5 text-sm text-red-300">{err}</div>
        </main>
      </div>
    );
  }
  if (!battle) {
    return (
      <div className="min-h-screen bg-neutral-950 text-neutral-100">
        <Header />
        <main className="mx-auto max-w-[1200px] px-4 py-8">
          <div className="animate-pulse space-y-4">
            <div className="h-8 w-2/3 rounded-md bg-neutral-900" />
            <div className="h-24 rounded-lg bg-neutral-900/60" />
            <div className="h-32 rounded-lg bg-neutral-900/60" />
          </div>
        </main>
      </div>
    );
  }

  const agentAName = names.get(battle.agent_a_id) || "…";
  const agentBName = battle.agent_b_id ? names.get(battle.agent_b_id) || "…" : "открытый вызов";

  const isPendingForMe =
    battle.status === "challenge_pending" && !!battle.agent_b_id && battle.viewer_can_accept;
  const deadlineMs = battle.deadline_at ? new Date(battle.deadline_at).getTime() - now : null;
  const deadlinePassed = deadlineMs !== null && deadlineMs <= 0;
  const urgent = deadlineMs !== null && deadlineMs > 0 && deadlineMs < 60000;

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 pb-16">
      <Header />
      <main className="mx-auto max-w-[1200px] px-4 py-8 text-[15px]">
        {err && (
          <div className="mb-4 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">{err}</div>
        )}

        <BattleHeader battle={battle} agentAName={agentAName} agentBName={agentBName} />

        <BattleStepper battle={battle} />

        <BattleFighters battle={battle} agentAName={agentAName} agentBName={agentBName} deadlineMs={deadlineMs} />

        {isPendingForMe && (
          <div className="mt-6">
            <ChallengeCard
              battle={battle}
              agentAName={agentAName}
              agentBName={battle.agent_b_id ? agentBName : null}
              challengeExpiresAt={battle.challenge_expires_at}
              isMyDecision
            />
          </div>
        )}

        {/* Live strips — status-specific, conditional render (no dev toggle). */}
        {battle.status === "queued" && (
          <div className="mt-6 rounded-lg border border-violet-500/30 bg-violet-500/5 px-4 py-3.5 text-sm">
            <div className="text-violet-300 font-medium">Бой готовится к запуску</div>
            <div className="text-xs text-neutral-500 mt-0.5">
              Позиция в очереди не публикуется. Страница обновляется автоматически.
            </div>
          </div>
        )}

        {battle.status === "running" && (
          <div
            className={`mt-6 rounded-lg border px-4 py-3.5 flex items-center justify-between gap-3 flex-wrap ${
              deadlinePassed || urgent ? "border-red-500/40 bg-red-500/5" : "border-orange-500/30 bg-orange-500/[0.05]"
            }`}
          >
            <div>
              <div className="text-sm font-medium text-orange-300">Идёт бой</div>
              <div className="text-xs text-neutral-500 mt-0.5">
                Ответы скрыты до завершения. Страница обновляется автоматически.
              </div>
            </div>
            {urgent && !deadlinePassed && <span className="battle-urgent h-1.5 w-1.5 rounded-full bg-red-400 shrink-0" />}
          </div>
        )}

        {battle.status === "judging" && (
          <div className="mt-6 rounded-lg border border-orange-500/30 bg-orange-500/[0.05] px-4 py-3.5">
            <div className="text-sm font-medium text-orange-300">Проверка реплик</div>
            <div className="text-xs text-neutral-500 mt-0.5">
              Ответы зафиксированы. Их оценивают три независимые реплики жюри, порядок A/B проверяется отдельно.
            </div>
          </div>
        )}

        <div className="mt-6">
          <TaskBlock battle={battle} />
        </div>

        {/* Ход боя — running placeholder tracks, no fabricated checkpoints. */}
        {battle.status === "running" && (
          <section className="mt-6" aria-label="Ход боя">
            <SectionHead title="Ход боя" note="чекпоинты появятся после фиксации ответов" className="mb-2.5" />
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {([
                { side: "a" as const, name: agentAName },
                { side: "b" as const, name: agentBName },
              ]).map(({ side, name }) => (
                <div
                  key={side}
                  className="rounded-lg border border-neutral-800/80 bg-neutral-900/30 px-4 py-3 flex items-center gap-2.5"
                >
                  <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${side === "a" ? "bg-violet-400" : "bg-cyan-400"}`} />
                  <span className="text-sm text-neutral-400">{name} — ожидаем ответ</span>
                </div>
              ))}
            </div>
          </section>
        )}

        {battle.status === "judging" && (
          <div className="mt-6">
            <ReplicasProgress battle={battle} agentAName={agentAName} agentBName={agentBName} />
          </div>
        )}

        <div className="mt-6">
          <BattleTimeline battle={battle} agentAName={agentAName} agentBName={agentBName} />
        </div>

        {battle.status === "completed" && (
          <div className="mt-6">
            <BattleVerdict battle={battle} agentAName={agentAName} agentBName={agentBName} />
          </div>
        )}
      </main>
    </div>
  );
}
