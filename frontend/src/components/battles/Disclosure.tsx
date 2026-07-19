"use client";

import { useState } from "react";

interface DisclosureProps {
  label: string;
  openLabel?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
  className?: string;
}

/**
 * Progressive-disclosure panel (raw judge runs, long task prompts). Height
 * animates via grid-template-rows 0fr→1fr — the standard CSS technique for
 * animating to an unknown content height without measuring in JS — paired
 * with a chevron rotate and an inner opacity fade so content doesn't just
 * "pop" in. Respects prefers-reduced-motion (battles CSS block in globals.css
 * collapses this to an instant show/hide).
 */
export function Disclosure({ label, openLabel, defaultOpen = false, children, className = "" }: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={className}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="battle-press flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider text-neutral-500 hover:text-neutral-300 transition-colors"
      >
        <svg
          width="10"
          height="10"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className="battle-chevron shrink-0"
          style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)" }}
        >
          <path d="M4 2l4 4-4 4" />
        </svg>
        {open ? openLabel ?? label : label}
      </button>
      <div className="battle-disclosure" data-open={open || undefined}>
        <div className="battle-disclosure-inner">{children}</div>
      </div>
    </div>
  );
}
