"use client";

import { useState, useSyncExternalStore, type ReactNode } from "react";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(notify: () => void) {
  const query = window.matchMedia?.(REDUCED_MOTION);
  query?.addEventListener("change", notify);
  return () => query?.removeEventListener("change", notify);
}

const prefersReducedMotion = () => window.matchMedia?.(REDUCED_MOTION).matches ?? false;

interface PausableStripProps {
  label: string;
  pauseLabel: string;
  playLabel: string;
  trackClassName?: string;
  children: ReactNode;
}

/** Edge-faded looping strip with a pause button (WCAG 2.2.2); stays still and scrolls by hand under reduced motion. */
export function PausableStrip({ label, pauseLabel, playLabel, trackClassName = "", children }: PausableStripProps) {
  const [userPaused, setUserPaused] = useState(false);
  const reduced = useSyncExternalStore(subscribeReducedMotion, prefersReducedMotion, () => false);
  const paused = userPaused || reduced;
  return (
    <div role="group" aria-label={label} data-strip data-paused={String(paused)} data-reduced={String(reduced)} className="flex items-center gap-2 px-2 sm:px-4">
      {!reduced && (
        <button
          type="button"
          aria-pressed={userPaused}
          aria-label={userPaused ? playLabel : pauseLabel}
          onClick={() => setUserPaused((p) => !p)}
          className="inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full border border-neutral-700 bg-neutral-900 text-neutral-300 transition-colors hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
            {userPaused ? <path d="M3 1.5v9l7.5-4.5z" /> : <path d="M2.5 1.5h2.5v9H2.5zM7 1.5h2.5v9H7z" />}
          </svg>
        </button>
      )}
      <div className="strip-viewport min-w-0 flex-1 overflow-hidden">
        <div className={`strip-track flex w-max items-center ${trackClassName}`}>
          <div data-strip-copy className="flex items-center gap-8 pr-8">{children}</div>
          {!reduced && (
            <div data-strip-copy aria-hidden="true" inert className="flex items-center gap-8 pr-8">{children}</div>
          )}
        </div>
      </div>
    </div>
  );
}
