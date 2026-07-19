import { BattleSide } from "@/lib/api";

// ── Shared battle-detail primitives ─────────────────────────────────────────
// Small building blocks reused across the battle-detail sections (fighters,
// verdict, replicas). Side A always reads violet, side B always reads cyan —
// the same meaningful mapping AgentIdentity establishes.

export const SIDE_ACCENT: Record<BattleSide, { text: string; chip: string; meter: string; answer: string }> = {
  a: {
    text: "text-violet-300",
    chip: "border-violet-500/30 bg-violet-500/10 text-violet-300",
    meter: "bg-violet-400",
    answer: "border-violet-500/25 bg-violet-500/[0.04]",
  },
  b: {
    text: "text-cyan-300",
    chip: "border-cyan-500/30 bg-cyan-500/10 text-cyan-300",
    meter: "bg-cyan-400",
    answer: "border-cyan-500/25 bg-cyan-500/[0.04]",
  },
};

export function eloDeltaText(before: number | null, after: number | null): { text: string; tone: string } {
  if (before === null || after === null) return { text: "—", tone: "text-neutral-600" };
  const d = after - before;
  if (d > 0) return { text: `+${d}`, tone: "text-emerald-400" };
  if (d < 0) return { text: `${d}`, tone: "text-rose-400" };
  return { text: "0", tone: "text-neutral-500" };
}

/** Section scaffolding — title row + optional muted note on the right. */
export function SectionHead({ title, note, className = "" }: { title: string; note?: string; className?: string }) {
  return (
    <div className={`flex items-baseline justify-between gap-3 ${className}`}>
      <div className="text-sm font-semibold text-neutral-100">{title}</div>
      {note && <div className="text-xs text-neutral-500">{note}</div>}
    </div>
  );
}
