"use client";

import { useEffect, useState } from "react";
import { timeAgo } from "@/lib/api";

interface FreshnessBadgeProps {
  lastUpdated: number | null;
  error: Error | null;
  onRetry?: () => void;
}

// A failing poll must never look identical to fresh data — that's the whole
// point of this component. Healthy state is subdued; error state is loud.
export function FreshnessBadge({ lastUpdated, error, onRetry }: FreshnessBadgeProps) {
  // Self-ticking so the relative time stays truthful between polls, without
  // triggering a data refetch.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 10000);
    return () => clearInterval(id);
  }, []);

  if (lastUpdated === null && !error) return null;

  if (error) {
    return (
      <div role="status" className="flex items-center gap-2 text-[11px] font-mono text-amber-400">
        <span>
          Stale — last updated {lastUpdated !== null ? timeAgo(new Date(lastUpdated).toISOString()) : "never"}
        </span>
        {onRetry && (
          <button
            onClick={onRetry}
            className="px-2 py-0.5 rounded border border-amber-400/30 text-amber-400 hover:bg-amber-400/10 transition-colors"
          >
            Retry
          </button>
        )}
      </div>
    );
  }

  return (
    <span className="text-[11px] font-mono text-neutral-600">
      Updated {timeAgo(new Date(lastUpdated as number).toISOString())}
    </span>
  );
}
