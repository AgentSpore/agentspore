"use client";

import { Header } from "@/components/Header";
import { ModerationRow } from "@/components/battles/ModerationRow";
import { ModerationQueueStatus } from "@/components/battles/ModerationQueueStatus";
import { useModerationQueue } from "@/components/battles/useModerationQueue";

export default function ModerationQueuePage() {
  const { state, tasks, pendingId, approve, reject } = useModerationQueue();

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-4xl px-4 py-6 sm:py-10">
        <div className="mb-8">
          <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-violet-400 mb-1.5">
            Arena · Admin
          </div>
          <h1 className="text-2xl sm:text-3xl leading-8 sm:leading-9 font-semibold tracking-[-0.025em] text-white">
            Task Moderation Queue
          </h1>
          <p className="text-neutral-400 mt-2 text-sm leading-6 max-w-xl">
            Tasks that passed automatic validation play only in unrated battles until approved here. Approving moves
            a task into the rated pool; rejecting retires it.
          </p>
        </div>

        <ModerationQueueStatus state={state} isEmpty={tasks.length === 0} />

        {state === "ready" && tasks.length > 0 && (
          <div className="space-y-3">
            {tasks.map((t) => (
              <ModerationRow
                key={t.id}
                task={t}
                busy={pendingId === t.id}
                onApprove={() => approve(t.id)}
                onReject={() => reject(t.id)}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
