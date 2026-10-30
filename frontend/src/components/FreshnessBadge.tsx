"use client";

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { sharedMessages } from '@/lib/i18n/shared';

import { useEffect, useState } from "react";
import { timeAgo } from "@/lib/api";

interface FreshnessBadgeProps {
  lastUpdated: number | null;
  error: Error | null;
  onRetry?: () => void;
  /** Loaded item count; an empty list has nothing to be fresh about. */
  count?: number;
}

const isUnderMinute = (stamp: number, now: number) => now - stamp < 60000;

// A failing poll must never look identical to fresh data — that's the whole
// point of this component. Healthy state is subdued; error state is loud.
export function FreshnessBadge({ lastUpdated, error, onRetry, count }: FreshnessBadgeProps) {
  const { locale } = useLocale();
  const tr = useTranslations(sharedMessages);
  // Self-ticking so the relative time stays truthful between polls, without
  // triggering a data refetch.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 10000);
    return () => clearInterval(id);
  }, []);

  if (lastUpdated === null && !error) return null;
  if (count === 0 && !error) return null;

  if (error) {
    return (
      <div role="status" className="flex flex-wrap items-center gap-2 text-xs font-mono text-amber-400">
        <span>
          {tr('staleLastUpdated')}{" "}{lastUpdated !== null ? timeAgo(new Date(lastUpdated).toISOString(), locale) : tr('never')}
        </span>
        {onRetry && (
          <button
            onClick={onRetry}
            className="min-h-11 min-w-11 px-2 py-0.5 rounded border border-amber-400/30 text-amber-400 hover:bg-amber-400/10 transition-colors"
          >
            {tr('retry')}</button>
        )}
      </div>
    );
  }

  return (
    <span className="text-xs font-mono text-neutral-400">
      {tr('updated')}{" "}{isUnderMinute(lastUpdated as number, now) ? tr('justNow') : timeAgo(new Date(lastUpdated as number).toISOString(), locale)}
    </span>
  );
}
