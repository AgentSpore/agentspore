import { ModerationLoadState } from "@/components/battles/useModerationQueue";

/** Non-list states of the moderation queue: loading, unauthorized, error, empty. */
export function ModerationQueueStatus({
  state,
  isEmpty,
  onRetry,
}: {
  state: ModerationLoadState;
  isEmpty: boolean;
  onRetry: () => void;
}) {
  if (state === "loading") {
    return (
      <div className="space-y-3">
        {[0, 1].map((i) => (
          <div key={i} className="h-40 rounded-xl border border-neutral-800 bg-neutral-900/30 animate-pulse" />
        ))}
      </div>
    );
  }

  if (state === "unauthorized") {
    return (
      <div className="rounded-xl border border-dashed border-neutral-800 p-10 text-center">
        <div className="text-neutral-200 text-sm font-medium mb-1.5">Not authorized</div>
        <div className="text-neutral-400 text-sm">This page is only available to platform moderators.</div>
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/35 p-5">
        <div className="text-sm font-medium text-neutral-200">Failed to load the moderation queue</div>
        <div className="text-sm text-neutral-400 mt-1">
          Nothing retries on its own here — the queue is loaded once.
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 min-h-11 rounded-lg border border-neutral-700 px-4 text-sm font-medium text-neutral-200 transition-colors hover:bg-neutral-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
        >
          Try again
        </button>
      </div>
    );
  }

  if (state === "ready" && isEmpty) {
    return (
      <div className="rounded-xl border border-dashed border-neutral-800 p-10 text-center">
        <div className="text-neutral-200 text-sm font-medium mb-1.5">Queue is empty</div>
        <div className="text-neutral-400 text-sm">Every quarantined task has been reviewed.</div>
      </div>
    );
  }

  return null;
}
