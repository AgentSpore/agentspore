"use client";

import { useState } from "react";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { AUTH_FLOW_MESSAGES } from "@/lib/i18n/authFlow";
import { resendVerification } from "./resendVerification";

/** Inline "send the link again" action with an account-neutral confirmation. */
export function ResendVerification({ email }: { email: string }) {
  const tr = useTranslations(AUTH_FLOW_MESSAGES);
  const [state, setState] = useState<"idle" | "sending" | "done">("idle");

  const resend = async () => {
    setState("sending");
    await resendVerification(email);
    setState("done");
  };

  return (
    <div className="mt-3 space-y-2">
      <button type="button" onClick={resend} disabled={state === "sending"}
        className="min-h-11 rounded-lg border border-violet-500/30 bg-violet-500/10 px-4 py-2 text-sm text-violet-200 transition-colors hover:bg-violet-500/20 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300">
        {state === "sending" ? tr("resending") : tr("resendAction")}
      </button>
      <p role="status" className="text-xs leading-5 text-neutral-300">{state === "done" ? tr("resendDone") : ""}</p>
    </div>
  );
}

/** Persistent state after registration: stays until the person leaves or goes back to sign-in. */
export function ConfirmationSent({ email, onBack }: { email: string; onBack: () => void }) {
  const tr = useTranslations(AUTH_FLOW_MESSAGES);
  return (
    <section aria-labelledby="auth-sent-title" className="space-y-2">
      <h2 id="auth-sent-title" className="text-lg font-semibold text-white">{tr("sentTitle")}</h2>
      <p className="text-sm leading-6 text-neutral-200 wrap-anywhere">{tr("sentTo", { email })}</p>
      <p className="text-sm leading-6 text-neutral-400">{tr("sentHint")}</p>
      <ResendVerification email={email} />
      <button type="button" onClick={onBack}
        className="min-h-11 text-sm text-neutral-400 underline underline-offset-4 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300">
        {tr("backToSignIn")}
      </button>
    </section>
  );
}
