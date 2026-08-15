import { BATTLE_DIFFICULTY, ModerationTaskView } from "@/lib/api";
import { Disclosure } from "@/components/battles/Disclosure";

const PROMPT_PREVIEW_LEN = 420;

/** The quarantine record — the evidence a moderator approves or rejects on. */
function QuarantineStats({ task }: { task: ModerationTaskView }) {
  return (
    <div className="mt-4 border-t border-neutral-800/70 pt-3 grid grid-cols-3 gap-2 text-center">
      <div>
        <div className="text-sm font-semibold text-neutral-100">{task.quarantine_battles}</div>
        <div className="text-[11px] text-neutral-500">Quarantine battles</div>
      </div>
      <div>
        <div className="text-sm font-semibold text-neutral-100">{task.settled_battles}</div>
        <div className="text-[11px] text-neutral-500">Settled</div>
      </div>
      <div>
        <div className="text-sm font-semibold text-neutral-100">{task.decisive_battles}</div>
        <div className="text-[11px] text-neutral-500">Decisive</div>
      </div>
    </div>
  );
}

function ModerationRowHeader({
  task,
  busy,
  onApprove,
  onReject,
}: {
  task: ModerationTaskView;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
      <div>
        <div className="text-sm font-medium text-neutral-100">{task.title}</div>
        <div className="mt-1 text-xs text-neutral-500">
          {task.category} · {BATTLE_DIFFICULTY[task.difficulty]}
          {!task.author_user_id && " · harvested"}
        </div>
      </div>
      <div className="flex gap-2 shrink-0">
        <button
          type="button"
          onClick={onApprove}
          disabled={busy}
          className="battle-press min-h-11 rounded-lg bg-emerald-600 hover:bg-emerald-500 px-4 text-sm font-medium text-white transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={busy}
          className="battle-press min-h-11 rounded-lg border border-red-500/40 text-red-300 hover:bg-red-500/10 px-4 text-sm font-medium transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
        >
          Reject
        </button>
      </div>
    </div>
  );
}

export function ModerationRow({
  task,
  busy,
  failed,
  onApprove,
  onReject,
}: {
  task: ModerationTaskView;
  busy: boolean;
  failed: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const isLong = task.prompt.length > PROMPT_PREVIEW_LEN;

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/35 p-4 sm:p-5">
      <ModerationRowHeader task={task} busy={busy} onApprove={onApprove} onReject={onReject} />
      {failed && (
        <div role="status" className="mt-2 text-xs text-red-300">
          That action did not go through. The task is unchanged — try again.
        </div>
      )}

      <Disclosure label="Show prompt" openLabel="Collapse prompt" className="mt-3">
        <div className="text-sm text-neutral-300 whitespace-pre-wrap leading-[1.65] mt-2">
          {isLong ? `${task.prompt.slice(0, PROMPT_PREVIEW_LEN)}…` : task.prompt}
        </div>
      </Disclosure>

      {task.rubric.length > 0 && (
        <Disclosure label="Show rubric" openLabel="Collapse rubric" className="mt-3">
          <ul className="mt-2 space-y-1.5">
            {task.rubric.map((c) => (
              <li key={c.key} className="text-xs text-neutral-400">
                <span className="text-neutral-300 font-medium">{c.key}</span> (weight {c.weight}) — {c.description}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      <QuarantineStats task={task} />

      {task.validation_reason && (
        <div className="mt-3 text-xs text-neutral-500">Validator note: {task.validation_reason}</div>
      )}
    </div>
  );
}
