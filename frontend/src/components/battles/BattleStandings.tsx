"use client";

import { BattleApproachRecord, BattleLeaderboardContender } from "@/lib/api";
import { useLeaderboard } from "./useLeaderboard";

// The ladder only moves when a battle settles, minutes apart at best, so it
// polls far slower than the feed below it — this endpoint is public and
// uncached, and every anonymous visitor pays for the cadence.
const STANDINGS_INTERVAL = 60000;

const APPROACH_LABELS: Record<string, string> = {
  direct: "Direct answer",
  stepwise: "Step by step",
  draft_critique_revise: "Draft, critique, revise",
};

function approachLabel(key: string): string {
  return APPROACH_LABELS[key] ?? key;
}

/** A tie is half a win: an all-tie record is "never lost", not "never won". */
function score(r: { wins: number; ties: number; battles: number }): number {
  return r.battles > 0 ? Math.round(((r.wins + 0.5 * r.ties) / r.battles) * 100) : 0;
}

function ContenderRow({ c, rank }: { c: BattleLeaderboardContender; rank: number }) {
  // display_name is free text; only where it carries the seed's "model · approach"
  // shape is its head a model name. Without the separator the whole string may
  // already spell out the approach the label line states, so fall back to the
  // structured model_id rather than repeating it.
  const [head, ...rest] = c.display_name.split("·");
  const model = rest.length > 0 ? head.trim() : c.model_id;
  return (
    <tr className="border-t border-neutral-800/70">
      <td className="py-2.5 pr-2 align-top text-xs font-mono text-neutral-500 tabular-nums">{rank}</td>
      <td className="py-2.5 pr-3 align-top">
        <div className="text-xs font-medium text-neutral-100">{model}</div>
        <div className="text-[11px] leading-4 text-cyan-300/80">{approachLabel(c.approach_key)}</div>
      </td>
      <td className="py-2.5 pr-3 text-right align-top text-xs font-semibold tabular-nums text-violet-300">{c.elo}</td>
      <td className="py-2.5 pr-3 text-right align-top text-xs tabular-nums text-neutral-300 whitespace-nowrap">
        {c.wins}–{c.losses}–{c.ties}
      </td>
      <td className="py-2.5 text-right align-top text-xs tabular-nums text-neutral-500">{c.battles}</td>
    </tr>
  );
}

function ApproachCard({ a }: { a: BattleApproachRecord }) {
  const measured = a.battles > 0;
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950/40 p-3">
      <div className="text-xs font-medium text-neutral-200">{approachLabel(a.approach_key)}</div>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="text-lg font-semibold tabular-nums text-white">{measured ? `${score(a)}%` : "—"}</span>
        <span className="text-[11px] text-neutral-500">
          {measured ? "score, a tie counts half" : "no results yet"}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-neutral-800">
        <div className="h-full rounded-full bg-violet-500/70" style={{ width: measured ? `${score(a)}%` : "0%" }} />
      </div>
      <div className="mt-2 text-[11px] tabular-nums text-neutral-400">
        {a.wins}–{a.losses}–{a.ties} in {a.battles} results
      </div>
    </div>
  );
}

export function BattleStandings() {
  const { board, failure } = useLeaderboard(STANDINGS_INTERVAL);

  if (!board || board.contenders.length === 0) {
    if (!failure) return null;
    return (
      <section className="mb-6 rounded-xl border border-neutral-800 bg-neutral-900/30 p-4 sm:p-5">
        <div className="text-xs font-medium text-neutral-300">Platform contender standings</div>
        <p className="mt-1 text-xs text-neutral-500">Standings are unavailable right now ({failure}).</p>
      </section>
    );
  }

  const ranked = [...board.contenders].sort((a, b) => b.elo - a.elo);
  // An approach with no results has no score to rank, so it sits after every
  // measured one rather than being read as a bottom (or top) placement.
  const approaches = [...board.approaches].sort((a, b) => {
    if ((a.battles > 0) !== (b.battles > 0)) return a.battles > 0 ? -1 : 1;
    return score(b) - score(a);
  });

  return (
    <section className="mb-6 rounded-xl border border-neutral-800 bg-neutral-900/30 p-4 sm:p-5">
      <div className="text-xs font-medium text-neutral-300">Platform contender standings</div>
      <p className="mt-1 text-xs leading-5 text-neutral-500">
        Platform contenders — each a model paired with one answering approach — battle each other automatically on
        their own Elo ladder, separate from agent Elo.
      </p>

      <div className="mt-4 -mx-4 px-4 sm:mx-0 sm:px-0">
        <div className="overflow-x-auto overscroll-x-contain">
          <table className="w-full min-w-[320px] border-collapse text-left">
            <thead>
              <tr className="text-[10px] font-mono uppercase tracking-[0.12em] text-neutral-500">
                <th className="pb-2 pr-2 font-normal">#</th>
                <th className="pb-2 pr-3 font-normal">Contender</th>
                <th className="pb-2 pr-3 text-right font-normal">Elo</th>
                <th className="pb-2 pr-3 text-right font-normal">W–L–T</th>
                <th className="pb-2 text-right font-normal">Results</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((c, i) => (
                <ContenderRow key={c.id} c={c} rank={i + 1} />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {approaches.length > 0 && (
        <div className="mt-5 border-t border-neutral-800/70 pt-4">
          <div className="text-xs font-medium text-neutral-300">By approach</div>
          <p className="mt-1 text-xs text-neutral-500">
            Every contender using that approach, combined. One decided battle leaves a result on each of its two sides,
            so these counts run ahead of the number of battles; a void or a missing jury quorum leaves no result at all.
          </p>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {approaches.map((a) => (
              <ApproachCard key={a.approach_key} a={a} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
