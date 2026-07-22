import { BATTLE_DIFFICULTY, BattleDetail } from "@/lib/api";
import { SectionHead } from "@/components/battles/battleUi";

interface TimelineRow {
  ts: string;
  dot: "neutral" | "a" | "b" | "ok" | "live";
  title: string;
  sub?: string;
}

const DOT_CLASS: Record<TimelineRow["dot"], string> = {
  neutral: "bg-neutral-600",
  a: "bg-violet-400",
  b: "bg-cyan-400",
  ok: "bg-emerald-400",
  live: "bg-orange-400",
};

function fmtTime(ts: string): string {
  const d = new Date(ts);
  if (!Number.isFinite(d.getTime())) return "—";
  return d.toLocaleTimeString("en-US");
}

/**
 * Timeline — derived entirely from timestamp fields already on BattleDetail
 * (challenged_at, agent_b_accepted_at, queued_at, started_at, ended_at), so
 * the feed can never disagree with the status pill or the stepper. Rows are
 * emitted only for events that actually happened; the live/terminal row
 * reflects the current status.
 */
export function BattleTimeline({
  battle,
  agentAName,
  agentBName,
}: {
  battle: BattleDetail;
  agentAName: string;
  agentBName: string;
}) {
  const rows: TimelineRow[] = [];

  const filterText = `${battle.task_category_filter ?? "any category"}, ${
    battle.task_difficulty_filter ? BATTLE_DIFFICULTY[battle.task_difficulty_filter] : "any difficulty"
  }`;

  rows.push({
    ts: battle.challenged_at,
    dot: "neutral",
    title: "Challenge sent",
    sub: `${agentAName} challenged ${agentBName} · filter: ${filterText}`,
  });

  if (battle.agent_b_accepted_at) {
    rows.push({
      ts: battle.agent_b_accepted_at,
      dot: "b",
      title: "Challenge accepted",
      sub: `${agentBName} confirmed participation`,
    });
  }

  if (battle.queued_at) {
    rows.push({
      ts: battle.queued_at,
      dot: "neutral",
      title: "Battle queued",
      sub: "both sides confirmed readiness",
    });
  }

  if (battle.started_at) {
    rows.push({
      ts: battle.started_at,
      dot: "live",
      title: "Battle started",
      sub: battle.deadline_at
        ? `task released to both sides · deadline ${fmtTime(battle.deadline_at)}`
        : "task released to both sides",
    });
  }

  if (battle.status === "judging") {
    rows.push({
      ts: battle.ended_at ?? battle.started_at ?? battle.challenged_at,
      dot: "live",
      title: "Replies locked in, jury review started",
      sub: "3 replicas × 2 submission orders (A→B and B→A)",
    });
  }

  if (battle.status === "completed" && battle.ended_at) {
    const eloText =
      battle.elo_a_before !== null && battle.elo_a_after !== null && battle.elo_b_before !== null && battle.elo_b_after !== null
        ? `${battle.elo_a_before}→${battle.elo_a_after} / ${battle.elo_b_before}→${battle.elo_b_after}`
        : null;
    rows.push({
      ts: battle.ended_at,
      dot: "ok",
      title: battle.winner === null ? "Battle finished without quorum" : "Verdict reached, Elo updated",
      sub: eloText ?? undefined,
    });
  }

  if (battle.status === "declined") {
    rows.push({ ts: battle.ended_at ?? battle.challenged_at, dot: "neutral", title: "Challenge declined" });
  } else if (battle.status === "expired") {
    rows.push({ ts: battle.ended_at ?? battle.challenged_at, dot: "neutral", title: "Challenge expired" });
  } else if (battle.status === "aborted") {
    rows.push({ ts: battle.ended_at ?? battle.challenged_at, dot: "neutral", title: "Battle aborted" });
  }

  return (
    <section aria-label="Timeline">
      <SectionHead title="Timeline" className="mb-2.5" />
      <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/35 px-5 py-1.5">
        <div className="flex flex-col">
          {rows.map((row, i) => (
            <div
              key={`${row.ts}-${i}`}
              className={`grid grid-cols-[96px_12px_minmax(0,1fr)] sm:grid-cols-[130px_16px_minmax(0,1fr)] gap-2 sm:gap-3 py-2.5 items-baseline ${
                i > 0 ? "border-t border-neutral-800/50" : ""
              }`}
            >
              <span className="font-mono text-xs text-neutral-500 tabular-nums">{fmtTime(row.ts)}</span>
              <span className={`h-[7px] w-[7px] rounded-full justify-self-center mt-1 ${DOT_CLASS[row.dot]}`} />
              <div className="text-[13px] leading-[1.55] text-neutral-300">
                {row.title}
                {row.sub && <div className="text-xs text-neutral-500 mt-px">{row.sub}</div>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
