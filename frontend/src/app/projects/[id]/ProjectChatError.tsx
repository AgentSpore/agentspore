import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { communicationMessages } from "@/lib/i18n/communication";
import { sharedMessages } from "@/lib/i18n/shared";

const ERROR_KEYS = {
  initial: "Could not load messages.",
  older: "Could not load older messages.",
  send: "Failed to send",
  edit: "Failed to edit",
  delete: "Could not delete message.",
} as const;

/** Local recovery action for the existing project discussion requests. */
export function ProjectChatError({ kind, disabled, onRetry }: {
  kind: keyof typeof ERROR_KEYS;
  disabled: boolean;
  onRetry: () => void;
}) {
  const tr = useTranslations(communicationMessages);
  const sharedTr = useTranslations(sharedMessages);
  return <div role="alert" className="my-2 flex flex-wrap items-center gap-3 text-sm text-neutral-300">
    <p>{tr(ERROR_KEYS[kind])}</p>
    <button type="button" onClick={onRetry} disabled={disabled}
      className="min-h-12 min-w-12 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-white hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 disabled:opacity-50">
      {sharedTr("retry")}
    </button>
  </div>;
}
