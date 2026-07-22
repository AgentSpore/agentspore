"use client";

import { BattleDetail } from "@/lib/api";
import { SectionHead } from "@/components/battles/battleUi";

/**
 * Judging-state view of the replica panel. The backend returns EMPTY
 * replica/vote collections from /battles/{id}/judgements until
 * status === "completed" — there is no per-replica progress to show, and
 * this component is not re-fetched or re-mounted while judging runs (the
 * page's polling only swaps `battle`, not this section's key). A per-replica
 * "pending" layout here would freeze on mount and imply live progress that
 * never existed — so this renders one honest, static, sealed-panel state
 * instead of N fake replica cards.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- signature kept stable for the call site in app/battles/[id]/page.tsx; this view needs none of the props.
export function ReplicasProgress(props: { battle: BattleDetail; agentAName: string; agentBName: string }) {
  return (
    <section aria-label="Jury review">
      <SectionHead title="Jury review" className="mb-2.5" />
      <div className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-5 flex items-center gap-3.5">
        <span className="relative flex h-2 w-2 shrink-0">
          <span className="battle-live-dot-ping absolute inline-flex h-full w-full rounded-full bg-current opacity-60 text-neutral-400" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-current text-neutral-400" />
        </span>
        <p className="text-[13px] leading-[1.6] text-neutral-400">
          Jury replicas are evaluating the replies independently. Results stay hidden until the verdict is reached.
        </p>
      </div>
    </section>
  );
}
