import { API_URL, BattleStatus, BattleSummary } from "@/lib/api";
import { INCLUDE_UNDECIDED_PARAM } from "@/components/battles/undecided";

// How many battles one "Show more" reveals, and the ceiling the endpoint puts
// on a single request (api/v1/battles.py: Query(default=50, ge=1, le=100)).
// A window wider than the ceiling has to be fetched as consecutive pages:
// asking for more than MAX_LIMIT does not fail, it silently returns fewer
// rows, which would read as "no more battles" while the rest sat unreachable.
export const PAGE_SIZE = 25;
export const MAX_LIMIT = 100;

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
): Promise<BattleSummary[]> {
  const wanted = PAGE_SIZE * pages;
  const out: BattleSummary[] = [];
  while (out.length < wanted) {
    const ask = Math.min(MAX_LIMIT, wanted - out.length);
    const params = new URLSearchParams({ limit: String(ask), offset: String(out.length) });
    if (filter !== "all") params.set("status", filter);
    if (includeUndecided) params.set(INCLUDE_UNDECIDED_PARAM, "true");
    const res = await fetch(`${API_URL}/api/v1/battles?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const chunk: BattleSummary[] = await res.json();
    out.push(...chunk);
    // A chunk shorter than asked for is the only proof there is nothing behind it.
    if (chunk.length < ask) break;
  }
  return out;
}
