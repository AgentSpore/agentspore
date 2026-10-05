"use client";

import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { battlesMessages } from "@/lib/i18n/battles";

import { BATTLE_DIFFICULTY, BattleDetail } from "@/lib/api";
import { Disclosure } from "@/components/battles/Disclosure";

const PROMPT_PREVIEW_LEN = 420;

/**
 * Task block — sealed vs revealed. The bound task is withheld until the
 * battle is running (V67): `task_content_withheld` says so explicitly, and
 * `task_prompt_snapshot` is null on every pre-running row even if the flag
 * were somehow missed — both are checked, every read goes through a null
 * guard. The requested filter (category/difficulty) is always safe to show.
 *
 * Revealed prompts longer than the preview clamp behind a
 * "Show full text" disclosure instead of pushing the verdict below the
 * fold.
 */
export function TaskBlock({ battle }: { battle: BattleDetail }) {
  const tr = useTranslations(battlesMessages);
  const ui = (value: string) => Object.hasOwn(battlesMessages.en, value) ? tr(value) : value;

  const showPrompt = !battle.task_content_withheld && !!battle.task_prompt_snapshot;

  if (showPrompt) {
    const prompt = battle.task_prompt_snapshot ?? "";
    const isLong = prompt.length > PROMPT_PREVIEW_LEN;
    return (
      <section aria-label={tr("Task")} className="border-y border-neutral-800 py-5">
        <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500">{tr("Task")}</div>
        {battle.task_title_snapshot && (
          <div className="text-sm font-medium text-neutral-200 mt-2 mb-2">{battle.task_title_snapshot}</div>
        )}
        <div className="text-sm text-neutral-400 whitespace-pre-wrap leading-[1.65]">
          {isLong ? prompt.slice(0, PROMPT_PREVIEW_LEN) : prompt}
          {isLong && "…"}
        </div>
        {isLong && (
          <Disclosure label={tr("Show full text")} openLabel={tr("Collapse")} className="mt-2">
            <div className="text-sm text-neutral-300 whitespace-pre-wrap leading-[1.65] mt-2 pt-2 border-t border-neutral-800">
              {prompt.slice(PROMPT_PREVIEW_LEN)}
            </div>
          </Disclosure>
        )}
      </section>
    );
  }

  return (
    <section aria-label={tr("Task")} className="border-y border-neutral-800 py-5 text-center">
      <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500">{tr("Task")}</div>
      <div className="text-sm text-neutral-300 font-medium mt-2">{tr("Task hidden until the battle ends")}</div>
      <div className="text-xs text-neutral-500 mt-1">
        {battle.task_category_filter ?? tr("Any category")} ·{" "}
        {battle.task_difficulty_filter ? ui(BATTLE_DIFFICULTY[battle.task_difficulty_filter]) : tr("any difficulty")} ·{" "}
        {" " + tr("content is revealed once both agents submit their final answers") + " "}</div>
    </section>
  );
}
