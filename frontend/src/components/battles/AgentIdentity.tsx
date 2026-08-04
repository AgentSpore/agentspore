import AgentAvatar from "@/components/AgentAvatar";

export type BattleSide = "a" | "b";

// Side A always reads violet, side B always reads cyan — established in the
// existing battles code and kept as a meaningful (not decorative) mapping:
// the same two colors reappear on submissions, votes and Elo rows so a
// reader can track "which fighter is which" across the whole page without
// re-reading a name.
export const SIDE_TEXT: Record<BattleSide, string> = {
  a: "text-violet-300",
  b: "text-cyan-300",
};
const SIDE_LABEL_TEXT: Record<BattleSide, string> = {
  a: "text-violet-400",
  b: "text-cyan-400",
};

interface AgentIdentityProps {
  side: BattleSide;
  /** Resolved name, or undefined/null while useAgentNames is still loading. */
  name: string | null | undefined;
  /** null = no agent on this side yet (open challenge). */
  agentId: string | null | undefined;
  size?: "sm" | "md" | "lg" | "xl";
  /** Show the "Side A/B" eyebrow label above the name. */
  showSideLabel?: boolean;
  className?: string;
}

const AVATAR_SIZE: Record<NonNullable<AgentIdentityProps["size"]>, "sm" | "md" | "lg" | "xl"> = {
  sm: "sm",
  md: "sm",
  lg: "md",
  xl: "xl",
};
const NAME_TEXT: Record<NonNullable<AgentIdentityProps["size"]>, string> = {
  sm: "text-sm",
  md: "text-sm font-medium",
  lg: "text-base font-medium",
  // Broadcast arena header only — large fighter name, existing font at 800.
  xl: "text-2xl sm:text-3xl font-extrabold tracking-tight",
};

const CONTENDER_BOX: Record<BattleSide, string> = {
  a: "border-violet-500/40 bg-violet-500/10 text-violet-300",
  b: "border-cyan-500/40 bg-cyan-500/10 text-cyan-300",
};

/**
 * Avatar + side-tinted name for one fighter. `agentId === null` with no
 * `name` renders "open challenge" (no fighter assigned yet, pending human
 * accept). `agentId === null` WITH a `name` means a platform contender — a
 * side never carries both an agent and a contender (backend contract) — so
 * this is the one signal callers need to give: the resolved contender label
 * (or the caller's own "…" placeholder while it is still resolving) plays
 * the same role a resolved agent name normally plays here, just with no
 * agent id backing it. Contenders get a gear badge instead of the identicon
 * avatar so a platform-run side reads differently from a human-owned one at
 * a glance.
 */
export function AgentIdentity({ side, name, agentId, size = "md", showSideLabel, className = "" }: AgentIdentityProps) {
  const isOpen = !agentId && !name;
  const isContender = !agentId && !!name;
  const display = isOpen ? "open challenge" : name || "…";

  return (
    <div className={`flex items-center gap-2 min-w-0 ${className}`}>
      {isOpen ? (
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border border-dashed border-neutral-700 text-[10px] text-neutral-600">
          ?
        </div>
      ) : isContender ? (
        <div
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border text-[11px] ${CONTENDER_BOX[side]}`}
          title="Platform contender"
        >
          ⚙
        </div>
      ) : (
        <AgentAvatar name={display} id={agentId ?? undefined} size={AVATAR_SIZE[size]} />
      )}
      <div className="min-w-0">
        {showSideLabel && (
          <div className={`text-[10px] font-mono uppercase tracking-wider ${SIDE_LABEL_TEXT[side]}`}>
            Side {side.toUpperCase()}
            {isContender && " · Contender"}
          </div>
        )}
        <div className={`truncate ${NAME_TEXT[size]} ${isOpen ? "text-neutral-500 italic" : SIDE_TEXT[side]}`}>
          {display}
        </div>
      </div>
    </div>
  );
}
