import { BattleDetail, BattleStatus } from "@/lib/api";

type StageState = "completed" | "current" | "future";

const STAGES = ["Вызов", "Принят", "Готовность", "Бой", "Реплики", "Вердикт"] as const;

const TERMINAL_LABEL: Partial<Record<BattleStatus, string>> = {
  declined: "Отклонён",
  expired: "Истёк",
  aborted: "Прерван",
};

/**
 * Maps the battle's existing status/readiness fields onto the six-stage
 * lifecycle stepper — no new data, just a read of fields already on
 * BattleDetail (status, agent_b_accepted_at, readiness.ready).
 */
function stageStates(battle: BattleDetail): StageState[] {
  const { status } = battle;
  const terminal = status === "declined" || status === "expired" || status === "aborted";

  const accepted = battle.agent_b_accepted_at !== null || (status !== "challenge_pending" && !terminal);
  const ready = battle.readiness?.ready === true || ["queued", "reserved", "running", "judging", "completed"].includes(status);
  const inFight = status === "running" || status === "judging" || status === "completed";
  const fightDone = status === "judging" || status === "completed";
  const repliesActive = status === "judging" || status === "completed";
  const repliesDone = status === "completed";
  const verdictDone = status === "completed";

  const states: StageState[] = [
    "completed", // Вызов — always completed once the battle is loaded
    terminal ? (accepted ? "completed" : "future") : accepted ? "completed" : "current",
    terminal ? "future" : ready ? "completed" : accepted ? "current" : "future",
    terminal ? "future" : fightDone ? "completed" : inFight ? "current" : ready ? "current" : "future",
    terminal ? "future" : repliesDone ? "completed" : repliesActive ? "current" : "future",
    terminal ? "future" : verdictDone ? "completed" : "future",
  ];
  return states;
}

const DOT_CLASSES: Record<StageState, string> = {
  completed: "bg-neutral-200 border-neutral-200",
  current: "bg-orange-400 border-orange-400",
  future: "border-neutral-700 bg-neutral-900",
};

export function BattleStepper({ battle }: { battle: BattleDetail }) {
  const states = stageStates(battle);
  const terminalLabel = TERMINAL_LABEL[battle.status];
  const currentIndex = Math.max(
    states.lastIndexOf("current"),
    states.includes("current") ? states.indexOf("current") : states.lastIndexOf("completed")
  );

  return (
    <div className="mb-6">
      {terminalLabel && (
        <div className="mb-2 inline-flex items-center rounded-md border border-neutral-700 bg-neutral-900/60 px-2 py-0.5 text-xs text-neutral-400">
          {terminalLabel}
        </div>
      )}

      {/* Desktop / tablet — six-dot grid */}
      <div className="hidden sm:grid grid-cols-6 gap-1">
        {STAGES.map((label, i) => (
          <div key={label} className="flex flex-col items-center">
            <div className="flex items-center w-full">
              {i > 0 && (
                <div
                  className={`battle-stepper-connector h-px flex-1 ${
                    states[i - 1] === "completed" ? "bg-neutral-500" : "bg-neutral-800"
                  }`}
                />
              )}
              <span
                data-current={states[i] === "current" || undefined}
                className={`battle-stepper-dot shrink-0 h-6 w-6 rounded-full border-2 flex items-center justify-center ${DOT_CLASSES[states[i]]}`}
                aria-current={states[i] === "current" ? "step" : undefined}
              />
              {i < STAGES.length - 1 && (
                <div className={`battle-stepper-connector h-px flex-1 ${states[i] === "completed" ? "bg-neutral-500" : "bg-neutral-800"}`} />
              )}
            </div>
            <span
              className={`mt-1.5 text-[11px] leading-4 text-center ${
                states[i] === "current" ? "text-orange-300 font-medium" : states[i] === "completed" ? "text-neutral-300" : "text-neutral-600"
              }`}
            >
              {label}
            </span>
          </div>
        ))}
      </div>

      {/* Mobile — compact label + dot row */}
      <div className="sm:hidden">
        <div className="text-xs text-neutral-400 mb-2">
          Этап {Math.max(1, currentIndex + 1)} из {STAGES.length} ·{" "}
          <span className="text-orange-300 font-medium">{STAGES[Math.max(0, currentIndex)]}</span>
        </div>
        <div className="flex items-center gap-1">
          {STAGES.map((label, i) => (
            <span
              key={label}
              data-current={states[i] === "current" || undefined}
              className={`battle-stepper-dot h-2 w-2 rounded-full border ${DOT_CLASSES[states[i]]} flex-1`}
              aria-hidden="true"
            />
          ))}
        </div>
      </div>
    </div>
  );
}
