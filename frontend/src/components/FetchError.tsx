"use client";

import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { sharedMessages } from "@/lib/i18n/shared";

/** A failed load is an error with a way out, never a screen of zeros. */
export function FetchError({ onRetry }: { onRetry: () => void }) {
  const tr = useTranslations(sharedMessages);
  return (
    <div role="alert" className="relative flex flex-col items-center gap-3 py-16 text-center font-mono">
      <p className="text-sm text-red-400">{tr("couldNotLoadData")}</p>
      <button
        onClick={onRetry}
        className="min-h-11 px-4 rounded-lg border border-red-400/30 text-sm text-red-300 hover:bg-red-400/10 transition-colors"
      >
        {tr("retry")}
      </button>
    </div>
  );
}
