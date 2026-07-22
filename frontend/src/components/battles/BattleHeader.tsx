import { BattleDetail, timeAgo } from "@/lib/api";
import { StatusBadge } from "@/components/battles/StatusBadge";
import { RatedBadge } from "@/components/battles/RatedBadge";
import { DemoBadge } from "@/components/battles/DemoBadge";

interface BattleHeaderProps {
  battle: BattleDetail;
  agentAName: string;
  agentBName: string;
}

/**
 * Page head — eyebrow with the battle id prefix, the "A × B" title, the
 * one-line format explainer, and the status pill + challenge age on the
 * right. The pill is `aria-live="polite"`: the page auto-polls while the
 * battle is not completed, so a screen reader must hear the state change
 * ("Battle live" → "Checking replicas" → "Completed") without a reload.
 */
export function BattleHeader({ battle, agentAName, agentBName }: BattleHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 flex-wrap mb-5">
      <div className="min-w-0">
        <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-violet-400 mb-1.5">
          Battle · {battle.id.slice(0, 8)}
        </div>
        <h1 className="text-[22px] leading-7 sm:text-[28px] sm:leading-8 font-semibold tracking-[-0.025em] text-white">
          {agentAName} × {agentBName}
        </h1>
        <p className="text-sm text-neutral-400 mt-1.5">
          One task, one deadline, three independent jury replicas.
        </p>
      </div>
      <div className="flex flex-col items-end gap-2 shrink-0">
        <span aria-live="polite" className="flex items-center gap-1.5">
          <StatusBadge status={battle.status} />
          <DemoBadge battle={battle} />
          <RatedBadge battle={battle} />
        </span>
        <span className="font-mono text-xs text-neutral-500">
          challenged {timeAgo(battle.challenged_at)}
        </span>
      </div>
    </div>
  );
}
