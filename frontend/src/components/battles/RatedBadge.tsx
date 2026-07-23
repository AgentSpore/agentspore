import { BattleDetail, BattleSummary, JUDGING_STOP_REASON, RATED_INELIGIBILITY_REASON } from "@/lib/api";

interface RatedBadgeProps {
  battle: BattleSummary | BattleDetail;
  className?: string;
}

/**
 * "Rated" / "Unrated" pill (V68 F1). Reads three frozen backend
 * facts, never infers: `is_rated` (settled, only meaningful once completed),
 * `rated_eligible` (the pre-completion acceptance decision), and the two
 * reason strings that explain an unrated outcome. Anti-Sybil gate reasons
 * (`rated_ineligibility_reason`) and judging-stopped-early reasons
 * (`judging_stop_reason`) are surfaced separately because they name two
 * different points of failure.
 */
export function RatedBadge({ battle, className = "" }: RatedBadgeProps) {
  const isCompleted = battle.status === "completed";
  const reason =
    (battle.rated_ineligibility_reason && RATED_INELIGIBILITY_REASON[battle.rated_ineligibility_reason]) ||
    (battle.judging_stop_reason && JUDGING_STOP_REASON[battle.judging_stop_reason]) ||
    null;

  // Pre-completion: rated_eligible is the only settled fact so far.
  if (!isCompleted) {
    if (battle.rated_eligible === null) return null; // undecided (not yet accepted)
    if (battle.rated_eligible) {
      return (
        <span
          className={`inline-flex items-center rounded-md border border-violet-500/30 bg-violet-500/10 px-2 py-0.5 text-xs font-medium text-violet-300 ${className}`}
        >
          Rated
        </span>
      );
    }
    return (
      <span
        title={reason ?? undefined}
        className={`inline-flex items-center rounded-md border border-neutral-700 px-2 py-0.5 text-xs font-medium text-neutral-500 ${className}`}
      >
        Unrated
      </span>
    );
  }

  // Completed: is_rated is the settled outcome.
  if (battle.is_rated) {
    return (
      <span
        className={`inline-flex items-center rounded-md border border-violet-500/30 bg-violet-500/10 px-2 py-0.5 text-xs font-medium text-violet-300 ${className}`}
      >
        Rated · Elo updated
      </span>
    );
  }
  return (
    <span
      title={reason ?? undefined}
      className={`inline-flex items-center rounded-md border border-neutral-700 px-2 py-0.5 text-xs font-medium text-neutral-500 ${className}`}
    >
      Unrated{reason && <span className="ml-1 text-neutral-600 hidden sm:inline">· {reason}</span>}
    </span>
  );
}
