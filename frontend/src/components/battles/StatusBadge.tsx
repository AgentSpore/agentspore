import { BATTLE_FAST_STATES, BATTLE_STATUS, BattleStatus } from "@/lib/api";

interface StatusBadgeProps {
  status: BattleStatus;
  className?: string;
}

/**
 * Battle status pill. Colors/labels stay sourced from BATTLE_STATUS (the
 * single source of truth in lib/api.ts) — this component only owns motion:
 * a live-indicator dot for BATTLE_FAST_STATES instead of the whole badge
 * pulsing (a pulsing dot signals "something is happening" without dimming
 * the label text, which is what the raw `animate-pulse` on the badge did).
 */
export function StatusBadge({ status, className = "" }: StatusBadgeProps) {
  const meta = BATTLE_STATUS[status];
  const isLive = BATTLE_FAST_STATES.has(status);
  // BATTLE_STATUS bakes `animate-pulse` onto the badge itself for fast
  // states — strip it here since the dot carries that signal instead.
  const classes = meta.classes.replace(/\s*animate-pulse\s*/g, " ").trim();

  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${classes} ${className}`}
    >
      {isLive && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="battle-live-dot-ping absolute inline-flex h-full w-full rounded-full bg-current opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
        </span>
      )}
      {meta.label}
    </span>
  );
}
