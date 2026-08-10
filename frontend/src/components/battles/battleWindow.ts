import { API_URL, BattleStatus, BattleSummary } from "@/lib/api";
import { INCLUDE_UNDECIDED_PARAM } from "@/components/battles/undecided";

// How many battles one "Show more" reveals, and the ceiling the endpoint puts
// on a single request (api/v1/battles.py: Query(default=50, ge=1, le=100)).
// A window wider than the ceiling has to be fetched as consecutive pages:
// asking for more than MAX_LIMIT does not fail, it silently returns fewer
// rows, which would read as "no more battles" while the rest sat unreachable.
export const PAGE_SIZE = 25;
export const MAX_LIMIT = 100;

export interface BattleWindow {
  rows: BattleSummary[];
  /** The API ran out of rows inside this window, so there is nothing behind it. */
  exhausted: boolean;
}

/**
 * Fetch the first `pages * PAGE_SIZE` battles, in as few requests as allowed.
 *
 * The list is refetched whole on every poll rather than appended to, so a
 * refresh cannot drop what "Show more" revealed, and a battle that changes
 * status keeps its place instead of appearing twice.
 */
export async function fetchWindow(
  pages: number,
  filter: BattleStatus | "all",
  includeUndecided: boolean,
): Promise<BattleWindow> {
  const wanted = PAGE_SIZE * pages;
  const rows: BattleSummary[] = [];
  while (rows.length < wanted) {
    const ask = Math.min(MAX_LIMIT, wanted - rows.length);
    const params = new URLSearchParams({ limit: String(ask), offset: String(rows.length) });
    if (filter !== "all") params.set("status", filter);
    if (includeUndecided) params.set(INCLUDE_UNDECIDED_PARAM, "true");
    const res = await fetch(`${API_URL}/api/v1/battles?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const chunk: BattleSummary[] = await res.json();
    rows.push(...chunk);
    // A chunk shorter than asked for is the only proof there is nothing behind
    // it, and it is proof only this loop holds — a caller comparing
    // rows.length against the window size cannot tell "exactly full" from
    // "full and more behind", and would leave a dead "Show more" button on a
    // list whose size happens to be a multiple of PAGE_SIZE.
    if (chunk.length < ask) return { rows, exhausted: true };
  }
  return { rows, exhausted: false };
}
