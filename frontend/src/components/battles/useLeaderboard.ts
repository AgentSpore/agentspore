"use client";

import { useEffect, useState } from "react";
import { API_URL, BattleLeaderboard } from "@/lib/api";

/**
 * Contender standings, fetched once per mount. A failed fetch resolves to
 * null so the section can disappear instead of taking the feed down with it.
 */
export function useLeaderboard(): BattleLeaderboard | null {
  const [board, setBoard] = useState<BattleLeaderboard | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`${API_URL}/api/v1/battles/leaderboard`)
      .then((res) => (res.ok ? (res.json() as Promise<BattleLeaderboard>) : null))
      .catch(() => null)
      .then((data) => {
        if (alive && data) setBoard(data);
      });
    return () => {
      alive = false;
    };
  }, []);

  return board;
}
