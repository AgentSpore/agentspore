"use client";

import { useState } from "react";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";

type CopyState = "idle" | "copied" | "failed";

/** Copies `text`; the outcome is announced to screen readers through a polite live region. */
export function CopyPromptButton({ text }: { text: string }) {
  const tr = useTranslations(docsMessages);
  const [state, setState] = useState<CopyState>("idle");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={copy}
        className="min-h-11 rounded-lg bg-violet-600 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400"
      >
        {tr("copyPrompt")}
      </button>
      <span role="status" aria-live="polite" className="text-xs text-neutral-300">
        {state === "copied" ? tr("promptCopied") : state === "failed" ? tr("promptCopyFailed") : ""}
      </span>
    </div>
  );
}
