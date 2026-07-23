import { BattleDetail, BattleSummary } from "@/lib/api";

interface DemoBadgeProps {
  battle: BattleSummary | BattleDetail;
  className?: string;
}

/**
 * "Demo · unrated" pill (V71). Shown only when `is_demo` — a battle
 * against the platform sparring opponent, so the viewer isn't left wondering
 * why Elo never moved. Sits next to RatedBadge rather than replacing it: the
 * two pills answer different questions (what kind of battle / did rating
 * apply), and RatedBadge already reads `rated_ineligibility_reason: "demo"`
 * for its own tooltip.
 */
export function DemoBadge({ battle, className = "" }: DemoBadgeProps) {
  if (!battle.is_demo) return null;
  return (
    <span
      title="Demo battle against the platform's sparring agent — no rating change"
      className={`inline-flex items-center rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-xs font-medium text-cyan-300 ${className}`}
    >
      Demo · unrated
    </span>
  );
}
