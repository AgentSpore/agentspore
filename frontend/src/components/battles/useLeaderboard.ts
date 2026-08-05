"use client";

import { useEffect, useState } from "react";
import { API_URL, BattleLeaderboard } from "@/lib/api";

export interface LeaderboardState {
  board: BattleLeaderboard | null;
  /** Last attempt failed: HTTP status, or a transport error as its message. */
  failure: string | null;
}

/**
 * Contender standings, polled on the caller's interval so the ladder cannot
 * keep showing a pre-battle Elo while the feed already shows that battle as
 * completed. A failed attempt keeps the previous board and reports the reason,
 * so a broken endpoint stays distinguishable from an empty roster instead of
 * both rendering as silence.
 */
export function useLeaderboard(intervalMs: number): LeaderboardState {
  const [state, setState] = useState<LeaderboardState>({ board: null, failure: null });

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;

    const load = async () => {
      try {
        const res = await fetch(`${API_URL}/api/v1/battles/leaderboard`, { signal: controller.signal });
        if (res.ok) {
          const board = (await res.json()) as BattleLeaderboard;
          setState({ board, failure: null });
        } else {
          setState((prev) => ({ board: prev.board, failure: `HTTP ${res.status}` }));
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          const failure = err instanceof Error ? err.message : "request failed";
          setState((prev) => ({ board: prev.board, failure }));
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(load, intervalMs);
    };

    load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [intervalMs]);

  return state;
}
