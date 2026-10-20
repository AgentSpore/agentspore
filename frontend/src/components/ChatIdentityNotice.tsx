"use client";

import type { ChatIdentityState } from "@/lib/useChatIdentity";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { communicationMessages } from "@/lib/i18n/communication";
import { sharedMessages } from "@/lib/i18n/shared";

/** Show verification progress or recovery without implying that the user signed out. */
export function ChatIdentityNotice({ state }: { state: ChatIdentityState }) {
  const tr = useTranslations(communicationMessages);
  const sharedTr = useTranslations(sharedMessages);
  if (state.status === "checking") return (
    <p role="status" className="min-h-12 flex items-center justify-center text-sm text-neutral-300">
      {tr("Checking your account…")}
    </p>
  );
  if (state.status !== "error") return null;
  return (
    <div role="alert" className="flex flex-wrap items-center justify-center gap-3 text-sm text-neutral-300">
      <p>{tr("Could not verify your account.")}</p>
      <button type="button" onClick={state.retry}
        className="min-h-12 min-w-12 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-white hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
        {sharedTr("retry")}
      </button>
    </div>
  );
}
