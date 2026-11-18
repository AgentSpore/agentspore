"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { AUTH_FLOW_MESSAGES } from "@/lib/i18n/authFlow";
import { resendVerification } from "./resendVerification";

/** The server skips a repeat verification email requested within a minute. */
const RESEND_COOLDOWN_MS = 60_000;

const lastSentAt = new Map<string, number>();
const cooldownLeft = (key: string) => Math.max(0, (lastSentAt.get(key) ?? 0) + RESEND_COOLDOWN_MS - Date.now());

/** Inline "send the link again" action with an account-neutral confirmation and a one-minute cooldown. */
export function ResendVerification({ email }: { email: string }) {
  const tr = useTranslations(AUTH_FLOW_MESSAGES);
  const key = email.trim().toLowerCase();
  const [state, setState] = useState<"idle" | "sending" | "cooldown">(() => cooldownLeft(key) > 0 ? "cooldown" : "idle");

  useEffect(() => {
    if (state !== "cooldown") return;
    const timer = setTimeout(() => setState("idle"), cooldownLeft(key));
    return () => clearTimeout(timer);
  }, [state, key]);

  const resend = async () => {
    setState("sending");
    await resendVerification(email);
    lastSentAt.set(key, Date.now());
    setState("cooldown");
  };

  return (
    <div className="mt-3 space-y-2">
      <button type="button" onClick={resend} disabled={state !== "idle"}
        className="min-h-11 rounded-lg border border-violet-500/30 bg-violet-500/10 px-4 py-2 text-sm text-violet-200 transition-colors hover:bg-violet-500/20 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300">
        {state === "sending" ? tr("resending") : tr("resendAction")}
      </button>
      <p role="status" className="text-xs leading-5 text-neutral-300">
        {state === "cooldown" ? `${tr("resendDone")} ${tr("resendWait")}` : ""}
      </p>
    </div>
  );
}

/** Persistent state after registration: stays until the person leaves or goes back to sign-in. */
export function ConfirmationSent({ email, onBack }: { email: string; onBack: () => void }) {
  const tr = useTranslations(AUTH_FLOW_MESSAGES);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);

  return (
    <section aria-labelledby="auth-sent-title" className="space-y-2">
      <h2 ref={heading} tabIndex={-1} id="auth-sent-title" className="text-lg font-semibold text-white focus:outline-none">{tr("sentTitle")}</h2>
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
