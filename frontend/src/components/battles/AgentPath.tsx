"use client";

import { BattleSubmissionView } from "./BattleVerdict";
import { ParsedStep, agentPathSteps, parseStepLine } from "./agentPathSteps";
import { Disclosure } from "@/components/battles/Disclosure";

const KIND_LABEL: Record<ParsedStep["kind"], string> = {
  tool_call: "Tool call",
  tool_result: "Tool result",
  raw: "Step",
};

function StepLine({ step }: { step: ParsedStep }) {
  if (step.kind === "raw") {
    return <div className="whitespace-pre-wrap break-words text-neutral-300">{step.text}</div>;
  }
  const value = step.kind === "tool_call" ? step.args : step.output;
  return (
    <div>
      <span className="font-mono text-neutral-200">{step.tool}</span>
      <div className="mt-0.5 whitespace-pre-wrap break-words text-neutral-500">{value}</div>
    </div>
  );
}

function StepRow({ sub }: { sub: BattleSubmissionView }) {
  if (sub.truncated && sub.error) {
    return (
      <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-amber-300">
        Step {sub.seq_no}: agent ran out of time before finishing this step
      </div>
    );
  }
  if (sub.content_withheld) {
    return (
      <div className="rounded-md border border-neutral-800 px-2.5 py-2 italic text-neutral-500">
        Step {sub.seq_no}: hidden until the battle ends
      </div>
    );
  }
  const step = parseStepLine(sub.content ?? "");
  return (
    <div className="rounded-md border border-neutral-800 px-2.5 py-2">
      <div className="mb-1 text-[10px] font-mono uppercase tracking-wider text-neutral-600">
        Step {sub.seq_no} · {KIND_LABEL[step.kind]}
      </div>
      <StepLine step={step} />
    </div>
  );
}

/**
 * The path an agentic contender took before its final answer — collapsed by
 * default so it never competes with the final answer for attention (SPEC
 * "The page renders the path", rule 4). Renders nothing for a single-row
 * (model or user-agent) side: the multi-row API is exercised by every
 * contender type, so the absence of steps — not a flag — is what tells
 * agentic and non-agentic sides apart.
 */
export function AgentPath({ submissions, side }: { submissions: BattleSubmissionView[]; side: BattleSubmissionView["side"] }) {
  const steps = agentPathSteps(submissions, side);
  if (steps.length === 0) return null;

  const anyContentVisible = steps.some((s) => !s.content_withheld);
  const label = anyContentVisible
    ? `Path · ${steps.length} ${steps.length === 1 ? "step" : "steps"}`
    : `Path · ${steps.length} ${steps.length === 1 ? "step" : "steps"} (hidden until the battle ends)`;

  return (
    <div className="mt-3 border-t border-neutral-800 pt-3">
      <Disclosure label={label} openLabel={`Hide path · ${steps.length} ${steps.length === 1 ? "step" : "steps"}`}>
        <div className="space-y-1.5 text-xs leading-[1.6]">
          {steps.map((s) => (
            <StepRow key={s.seq_no} sub={s} />
          ))}
        </div>
      </Disclosure>
    </div>
  );
}
