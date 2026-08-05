import { BattleSummary } from "@/lib/api";

/**
 * Query parameter of GET /api/v1/battles that admits the battles the list
 * hides by default. The name is the contract — a wrong one is not an error,
 * it silently returns the default list — so it lives here once.
 */
export const INCLUDE_UNDECIDED_PARAM = "include_undecided";

type UndecidableBattle = Pick<BattleSummary, "status" | "winner">;

/**
 * A battle that finished without a verdict: it reached "completed" and no side
 * was recorded as the winner. That covers the three outcomes the battle page
 * names one by one — a provider that could not be reached, a jury that never
 * reached quorum, and a panel recused for conflict with a contender.
 *
 * The feed cannot tell them apart: BattleSummary carries no verdict_reason,
 * only BattleDetail does. So the list counts them as one group and leaves the
 * distinction to the battle page rather than guessing at it.
 */
export function isUndecided(battle: UndecidableBattle): boolean {
  return battle.status === "completed" && !battle.winner;
}

export function countUndecided(battles: UndecidableBattle[]): number {
  return battles.filter(isUndecided).length;
}
