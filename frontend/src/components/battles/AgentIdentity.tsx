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

/**
 * Avatar + side-tinted name for one fighter. Honest about the unknowns:
 * `agentId === null` renders "open challenge" (no agent assigned yet), and a
 * still-resolving name renders the same "…" placeholder useAgentNames/the
 * rest of the app already uses — never a fabricated name.
 */
export function AgentIdentity({ side, name, agentId, size = "md", showSideLabel, className = "" }: AgentIdentityProps) {
  const isOpen = agentId === null || agentId === undefined;
  const display = isOpen ? "open challenge" : name || "…";

  return (
    <div className={`flex items-center gap-2 min-w-0 ${className}`}>
      {isOpen ? (
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border border-dashed border-neutral-700 text-[10px] text-neutral-600">
          ?
        </div>
      ) : (
        <AgentAvatar name={display} id={agentId} size={AVATAR_SIZE[size]} />
      )}
      <div className="min-w-0">
        {showSideLabel && (
          <div className={`text-[10px] font-mono uppercase tracking-wider ${SIDE_LABEL_TEXT[side]}`}>
            Side {side.toUpperCase()}
          </div>
        )}
        <div className={`truncate ${NAME_TEXT[size]} ${isOpen ? "text-neutral-500 italic" : SIDE_TEXT[side]}`}>
          {display}
        </div>
      </div>
    </div>
  );
}
