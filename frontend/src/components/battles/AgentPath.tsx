"use client";

import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { battlesMessages } from "@/lib/i18n/battles";

import { BattleSubmissionView } from "./BattleVerdict";
import {
  LONG_VALUE_CHARS,
  ParsedStep,
  agentPathSteps,
  isLongValue,
  parseStepLine,
} from "./agentPathSteps";
import { Disclosure } from "@/components/battles/Disclosure";

const KIND_LABEL: Record<ParsedStep["kind"], string> = {
  tool_call: "Tool call",
  tool_result: "Tool result",
  raw: "Step",
};

function StepValue({ value }: { value: string }) {
  const tr = useTranslations(battlesMessages);
  const { locale } = useLocale();

  const className = "mt-0.5 whitespace-pre-wrap break-words text-neutral-500";
  if (!isLongValue(value)) {
    return <div className={className}>{value}</div>;
  }
  return (
    <details className="mt-0.5 group">
      <summary className="cursor-pointer list-none text-neutral-500 hover:text-neutral-300 group-open:hidden">
        <span className="whitespace-pre-wrap break-words">{value.slice(0, LONG_VALUE_CHARS)}…</span>
        <span className="ml-1 text-xs text-neutral-600">{tr("show all") + " "}{value.length.toLocaleString(locale)} {" " + tr("chars")}</span>
      </summary>
      <div className={className}>{value}</div>
    </details>
  );
}

function StepLine({ step }: { step: ParsedStep }) {

  if (step.kind === "raw") {
    return <div className="whitespace-pre-wrap break-words text-neutral-300">{step.text}</div>;
  }
  return (
    <div>
      <span className="font-mono text-neutral-200">{step.tool}</span>
      <StepValue value={step.kind === "tool_call" ? step.args : step.output} />
    </div>
  );
}

function StepRow({ sub }: { sub: BattleSubmissionView }) {
  const tr = useTranslations(battlesMessages);
  const { locale } = useLocale();
  const ui = (value: string) => Object.hasOwn(battlesMessages.en, value) ? tr(value) : value;

  if (sub.truncated && sub.error) {
    return (
      <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-amber-300">
        {" " + tr("Step") + " "}{sub.seq_no.toLocaleString(locale)}{tr(": agent ran out of time before finishing this step") + " "}</div>
    );
  }
  if (sub.content_withheld) {
    return (
      <div className="rounded-md border border-neutral-800 px-2.5 py-2 italic text-neutral-500">
        {" " + tr("Step") + " "}{sub.seq_no.toLocaleString(locale)}{tr(": hidden until the battle ends") + " "}</div>
    );
  }
  const step = parseStepLine(sub.content ?? "");
  return (
    <div className="rounded-md border border-neutral-800 px-2.5 py-2">
      <div className="mb-1 text-[10px] font-mono uppercase tracking-wider text-neutral-600">
        {" " + tr("Step") + " "}{sub.seq_no.toLocaleString(locale)} · {ui(KIND_LABEL[step.kind])}
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
  const tr = useTranslations(battlesMessages);
  const { locale } = useLocale();

  const steps = agentPathSteps(submissions, side);
  if (steps.length === 0) return null;

  const anyContentVisible = steps.some((s) => !s.content_withheld);
  const label = tr(anyContentVisible ? "Path · {count} steps" : "Path · {count} steps (hidden until the battle ends)", { count: steps.length.toLocaleString(locale) });

  return (
    <div className="mt-3 border-t border-neutral-800 pt-3">
      <Disclosure label={label} openLabel={tr("Hide path · {count} steps", { count: steps.length.toLocaleString(locale) })}>
        <div className="space-y-1.5 text-xs leading-[1.6]">
          {steps.map((s) => (
            <StepRow key={s.seq_no} sub={s} />
          ))}
        </div>
      </Disclosure>
    </div>
  );
}
