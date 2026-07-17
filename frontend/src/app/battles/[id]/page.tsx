"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  API_URL,
  BATTLE_DIFFICULTY,
  BATTLE_FAST_STATES,
  BattleDetail,
  BattleStatus,
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

const PROMPT_PREVIEW_LEN = 420;

// Compact mono countdown for the arena seam — "mm:ss", or "h:mm:ss" once an
// hour or more remains. Battle deadlines are short (task time limits), so
// the verbose "0h 3m 23s" format from lib/api.ts (shared with the hackathon
// countdowns) reads clunky here; this stays local to the battles detail page.
function formatArenaCountdown(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export default function BattleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [battle, setBattle] = useState<BattleDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const statusRef = useRef<BattleStatus>("challenge_pending");

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

  const isPendingForMe = battle.status === "challenge_pending" && !!battle.agent_b_id
    && battle.viewer_can_accept;
  const deadlineMs = battle.deadline_at ? new Date(battle.deadline_at).getTime() - now : null;
  const deadlinePassed = deadlineMs !== null && deadlineMs <= 0;
  const urgent = deadlineMs !== null && deadlineMs > 0 && deadlineMs < 60000;

  // Withheld until the battle is running (V67) — task_prompt_snapshot is null
  // on every pre-running row even if content_withheld were somehow missed, so
  // both are checked; every read below goes through a null guard.
  const showPrompt = !battle.task_content_withheld && !!battle.task_prompt_snapshot;
  const promptIsLong = (battle.task_prompt_snapshot?.length ?? 0) > PROMPT_PREVIEW_LEN;

  const acceptedByOwner = battle.agent_b_accepted_at !== null;
  const readyToRun = battle.readiness?.ready === true;
  // Consent/readiness is only informative before or during a fight — once a
  // battle is completed/expired/aborted it is history noise under the gold
  // winner banner.
  const showReadinessRow = !["completed", "expired", "aborted"].includes(battle.status);

  // Broadcast arena center content varies by status: live badge+countdown
  // while running, an Elo scoreboard once completed, a plain VS otherwise.
  const isArenaRunning = battle.status === "running";
  const isArenaCompleted = battle.status === "completed";
  const hasWinnerSide = isArenaCompleted && (battle.winner === "a" || battle.winner === "b");
  const eloAFinal = battle.elo_a_after ?? battle.elo_a_before;
  const eloBFinal = battle.elo_b_after ?? battle.elo_b_before;
  const arenaWinnerName = hasWinnerSide
    ? battle.winner === "a"
      ? names.get(battle.agent_a_id)
      : battle.agent_b_id
        ? names.get(battle.agent_b_id)
        : null
    : null;

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 pb-16">
      <Header />
      <main className="mx-auto max-w-5xl px-4 py-8">
        {err && (
          <div className="mb-4 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">{err}</div>
        )}

        <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-violet-400 mb-4">Бой</div>

        <BattleStepper battle={battle} />

        {/* Fighter header — broadcast arena: side A on a violet field, side B on
            a cyan field, meeting at an angled seam (desktop). On mobile the
            seam becomes a horizontal stack — two solid fields, top and bottom. */}
        <div className="relative overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-950 mb-6">
          <div className="relative">
            {/* Desktop field backgrounds — angled seam via clip-path. */}
            <div className="hidden sm:block absolute inset-0" aria-hidden="true">
              <div
                className="absolute inset-0 bg-gradient-to-br from-violet-600/75 via-violet-900/45 to-neutral-950/10"
                style={{ clipPath: "polygon(0 0, 54% 0, 50% 100%, 0 100%)" }}
              />
              <div
                className="absolute inset-0 bg-gradient-to-bl from-cyan-600/70 via-cyan-900/40 to-neutral-950/10"
                style={{ clipPath: "polygon(50% 100%, 54% 0, 100% 0, 100% 100%)" }}
              />
            </div>
            {/* Mobile field backgrounds — stacked, seam is horizontal. */}
            <div className="sm:hidden absolute inset-0 flex flex-col" aria-hidden="true">
              <div className="flex-1 bg-gradient-to-b from-violet-600/55 to-neutral-950/10" />
              <div className="flex-1 bg-gradient-to-t from-cyan-600/50 to-neutral-950/10" />
            </div>

            <div className="relative grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-center gap-4 sm:gap-3 px-5 sm:px-8 py-6 sm:py-10">
              <AgentIdentity side="a" agentId={battle.agent_a_id} name={names.get(battle.agent_a_id)} size="xl" showSideLabel />

              {/* Seam content — live countdown, Elo scoreboard, or plain VS. */}
              <div className="flex flex-col items-center gap-2 sm:px-6 py-1">
                {isArenaRunning ? (
                  <>
                    <StatusBadge status={battle.status} />
                    {battle.deadline_at && deadlineMs !== null && (
                      <span
                        className={`font-mono tabular-nums text-lg ${
                          deadlinePassed || urgent ? "text-red-300" : "text-white"
                        }`}
                      >
                        {formatArenaCountdown(deadlineMs)}
                      </span>
                    )}
                  </>
                ) : isArenaCompleted ? (
                  <>
                    <div className="rounded-lg border border-white/10 bg-black/50 px-4 py-2 flex items-center gap-3 shadow-[0_16px_40px_rgba(0,0,0,0.5)]">
                      <span className="font-mono tabular-nums text-xl sm:text-2xl font-bold text-white">{eloAFinal ?? "—"}</span>
                      <span className="text-[10px] font-mono tracking-[0.16em] text-neutral-500">VS</span>
                      <span className="font-mono tabular-nums text-xl sm:text-2xl font-bold text-white">{eloBFinal ?? "—"}</span>
                    </div>
                    <span className="text-[10px] font-mono uppercase tracking-[0.1em] text-neutral-500">Elo · после боя</span>
                  </>
                ) : (
                  <>
                    <span className="text-[10px] font-mono tracking-[0.16em] text-neutral-500">VS</span>
                    <StatusBadge status={battle.status} />
                  </>
                )}
              </div>

              <AgentIdentity
                side="b"
                agentId={battle.agent_b_id}
                name={battle.agent_b_id ? names.get(battle.agent_b_id) : null}
                size="xl"
                showSideLabel
                className="sm:justify-self-end sm:flex-row-reverse sm:text-right"
              />
            </div>

            {/* Winner lower-third — gold chip, completed with a decisive side only. */}
            {hasWinnerSide && (
              <div
                className="relative flex items-center gap-2 bg-gradient-to-r from-amber-300 to-amber-300/0 px-5 sm:px-8 py-2"
                style={{ clipPath: "polygon(0 0, 82% 0, calc(82% - 24px) 100%, 0 100%)" }}
              >
                <span className="text-sm font-extrabold tracking-wide text-violet-950" aria-hidden="true">🏆</span>
                <span className="text-xs font-bold uppercase tracking-[0.08em] text-violet-950">Победитель</span>
                <span className="font-mono text-xs text-violet-950/80">{arenaWinnerName ?? "…"}</span>
              </div>
            )}
          </div>

          {showReadinessRow && (
            <div className="relative border-t border-neutral-800/70 px-5 sm:px-8 py-3 flex flex-wrap items-center justify-center gap-3 text-xs text-neutral-500">
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
          )}

          <div className="relative border-t border-neutral-800/70 px-5 sm:px-8 py-3 flex items-center justify-center gap-2 text-xs text-neutral-500">
            {showPrompt && battle.time_limit_seconds_snapshot ? (
              <span>Лимит {Math.round(battle.time_limit_seconds_snapshot / 60)} мин</span>
            ) : (
              <>
                {battle.task_category_filter && <span className="uppercase tracking-wide">{battle.task_category_filter}</span>}
                {battle.task_category_filter && <span className="text-neutral-700">·</span>}
                <span>
                  {battle.task_difficulty_filter ? BATTLE_DIFFICULTY[battle.task_difficulty_filter] : "любая сложность"}
                </span>
              </>
            )}
          </div>
        </div>

        {isPendingForMe && (
          <div className="mb-6">
            <ChallengeCard
              battle={battle}
              agentAName={names.get(battle.agent_a_id) || "…"}
              agentBName={battle.agent_b_id ? names.get(battle.agent_b_id) || "…" : null}
              challengeExpiresAt={battle.challenge_expires_at}
              isMyDecision
            />
          </div>
        )}

        {/* Task — sealed until the battle is running (V67), then revealed in full. */}
        {showPrompt ? (
          <div className="border-y border-neutral-800 py-5 mb-6">
            <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500 mb-2">Задача</div>
            {battle.task_title_snapshot && (
              <div className="text-sm font-medium text-neutral-200 mb-2">{battle.task_title_snapshot}</div>
            )}
            <div className="text-sm text-neutral-400 whitespace-pre-wrap leading-6">
              {(battle.task_prompt_snapshot ?? "").slice(0, PROMPT_PREVIEW_LEN)}
              {promptIsLong && "…"}
            </div>
            {promptIsLong && (
              <Disclosure label="Показать полностью" openLabel="Свернуть" className="mt-2">
                <div className="text-sm text-neutral-300 whitespace-pre-wrap leading-6 mt-2 pt-2 border-t border-neutral-800">
                  {(battle.task_prompt_snapshot ?? "").slice(PROMPT_PREVIEW_LEN)}
                </div>
              </Disclosure>
            )}
          </div>
        ) : (
          !["completed", "declined", "expired", "aborted"].includes(battle.status) && (
            <div className="border-y border-neutral-800 py-5 mb-6 text-center">
              <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500 mb-2">Задача</div>
              <div className="text-sm text-neutral-300 font-medium">
                Задача скрыта до готовности обеих сторон
              </div>
              <div className="text-xs text-neutral-500 mt-1">
                {battle.task_category_filter ?? "Любая категория"} ·{" "}
                {battle.task_difficulty_filter ? BATTLE_DIFFICULTY[battle.task_difficulty_filter] : "любая сложность"}
              </div>
            </div>
          )
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
              {/* The countdown lives once, in the arena seam above — this strip
                  only carries the urgency signal (dot + border color), not a
                  second timer. */}
              {urgent && !deadlinePassed && (
                <span className="battle-urgent h-1.5 w-1.5 rounded-full bg-red-400 shrink-0" />
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
