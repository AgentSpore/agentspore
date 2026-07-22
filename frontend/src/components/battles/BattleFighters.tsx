"use client";

import { BATTLE_DIFFICULTY, BattleDetail, BattleSide } from "@/lib/api";
import { AgentIdentity } from "@/components/battles/AgentIdentity";
import { eloDeltaText } from "@/components/battles/battleUi";

// Compact mono countdown for the center slot — "mm:ss", or "h:mm:ss" once an
// hour or more remains. Battle deadlines are short (task time limits), so the
// verbose "0h 3m 23s" format from lib/api.ts (shared with the hackathon
// countdowns) reads clunky here; this stays local to the battle detail page.
export function formatArenaCountdown(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function FighterCard({
  battle,
  side,
  name,
}: {
  battle: BattleDetail;
  side: BattleSide;
  name: string;
}) {
  const agentId = side === "a" ? battle.agent_a_id : battle.agent_b_id;
  const before = side === "a" ? battle.elo_a_before : battle.elo_b_before;
  const after = side === "a" ? battle.elo_a_after : battle.elo_b_after;
  const delta = eloDeltaText(before, after);
  const completed = battle.status === "completed";

  return (
    <div
      className={`flex flex-col gap-2.5 p-5 sm:px-6 min-w-0 ${
        side === "b"
          ? "border-t sm:border-t-0 sm:border-l border-neutral-800/70 sm:text-right sm:items-end"
          : ""
      }`}
    >
      <AgentIdentity
        side={side}
        agentId={agentId}
        name={agentId ? name : null}
        size="lg"
        showSideLabel
        className={side === "b" ? "sm:flex-row-reverse" : ""}
      />
      {completed ? (
        <div className={`flex items-baseline gap-2 font-mono text-[13px] tabular-nums ${side === "b" ? "sm:flex-row-reverse" : ""}`}>
          <span className="text-neutral-500">{before ?? "—"}</span>
          <span className="text-neutral-600">→</span>
          <span className="text-neutral-100 font-semibold">{after ?? "—"}</span>
          <span className={`text-xs font-semibold ${delta.tone}`}>{delta.text}</span>
        </div>
      ) : (
        <div className={`flex items-baseline gap-2 font-mono text-[13px] tabular-nums ${side === "b" ? "sm:flex-row-reverse" : ""}`}>
          <span className="text-neutral-500">Elo {before ?? "—"}</span>
          {before !== null && after === null && (
            <>
              <span className="text-neutral-600">·</span>
              <span className="text-neutral-500">на кону</span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

interface BattleFightersProps {
  battle: BattleDetail;
  agentAName: string;
  agentBName: string;
  /** Milliseconds until deadline_at; null when there is no deadline to show. */
  deadlineMs: number | null;
}

/**
 * Fighter header — calm two-column identity row (no diagonal fields): side A
 * on the left, side B on the right, and a center slot that switches on
 * status — a live countdown while running, a sealed "VS" note while judging,
 * and the post-battle Elo score once completed. The countdown is
 * `aria-live="polite"` so screen readers hear the clock/state change as the
 * page auto-polls.
 */
export function BattleFighters({ battle, agentAName, agentBName, deadlineMs }: BattleFightersProps) {
  const isRunning = battle.status === "running";
  const isJudging = battle.status === "judging";
  const isCompleted = battle.status === "completed";

  const eloAFinal = battle.elo_a_after ?? battle.elo_a_before;
  const eloBFinal = battle.elo_b_after ?? battle.elo_b_before;

  const deadlinePassed = deadlineMs !== null && deadlineMs <= 0;
  const urgent = deadlineMs !== null && deadlineMs > 0 && deadlineMs < 60000;

  const timeLimitMin = battle.time_limit_seconds_snapshot
    ? Math.round(battle.time_limit_seconds_snapshot / 60)
    : null;

  return (
    <section
      aria-label="Участники"
      className="overflow-hidden rounded-xl border border-neutral-800/80 bg-neutral-900/35"
    >
      <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-stretch">
        <FighterCard battle={battle} side="a" name={agentAName} />

        {/* Center slot — countdown / sealed VS / Elo result */}
        <div className="flex flex-col items-center justify-center gap-2 border-t sm:border-t-0 sm:border-l border-neutral-800/70 px-5 py-4 sm:min-w-[150px]">
          {isRunning ? (
            <>
              <span className="text-[10px] font-mono uppercase tracking-[0.16em] text-neutral-500">
                До дедлайна
              </span>
              {battle.deadline_at && deadlineMs !== null ? (
                <span
                  aria-live="polite"
                  className={`font-mono tabular-nums text-[22px] leading-7 font-semibold ${
                    deadlinePassed || urgent ? "battle-urgent text-red-300" : "text-white"
                  }`}
                >
                  {formatArenaCountdown(deadlineMs)}
                </span>
              ) : (
                <span className="font-mono text-lg text-neutral-500">—</span>
              )}
              <span className="text-[11px] leading-[1.5] text-neutral-500 text-center">
                {timeLimitMin !== null && (
                  <>
                    лимит {timeLimitMin} мин
                    <br />
                  </>
                )}
                ответы скрыты
              </span>
            </>
          ) : isJudging ? (
            <>
              <span className="text-[10px] font-mono tracking-[0.16em] text-neutral-500">VS</span>
              <span className="text-[11px] leading-[1.5] text-neutral-500 text-center">
                ответы зафиксированы
                <br />
                жюри из трёх реплик работает
              </span>
            </>
          ) : isCompleted ? (
            battle.is_rated ? (
              <>
                <span className="text-[10px] font-mono uppercase tracking-[0.16em] text-neutral-500">
                  Elo после боя
                </span>
                <span className="font-mono tabular-nums text-lg font-semibold text-white">
                  {eloAFinal ?? "—"} : {eloBFinal ?? "—"}
                </span>
                <span className="text-[11px] leading-[1.5] text-neutral-500 text-center">
                  {battle.winner === null && "без изменений · нет кворума"}
                </span>
              </>
            ) : (
              <>
                <span className="text-[10px] font-mono uppercase tracking-[0.16em] text-neutral-500">
                  Без рейтинга
                </span>
                <span className="text-[11px] leading-[1.5] text-neutral-500 text-center">
                  Elo не изменился
                </span>
              </>
            )
          ) : (
            <span className="text-[10px] font-mono tracking-[0.16em] text-neutral-500">VS</span>
          )}
        </div>

        <FighterCard battle={battle} side="b" name={agentBName} />
      </div>

      {/* Footer — battle parameters, always safe to show (the filter reveals
          nothing about which concrete task was picked, V67). */}
      <div className="border-t border-neutral-800/70 px-5 sm:px-6 py-2.5 flex items-center justify-center gap-2 text-xs text-neutral-500 flex-wrap">
        {timeLimitMin !== null && (
          <>
            <span>Лимит {timeLimitMin} мин</span>
            <span className="text-neutral-700">·</span>
          </>
        )}
        <span>категория: {battle.task_category_filter ?? "любая"}</span>
        <span className="text-neutral-700">·</span>
        <span>
          сложность: {battle.task_difficulty_filter ? BATTLE_DIFFICULTY[battle.task_difficulty_filter] : "любая"}
        </span>
        <span className="text-neutral-700">·</span>
        <span>жюри: 3 независимые реплики</span>
      </div>
    </section>
  );
}
