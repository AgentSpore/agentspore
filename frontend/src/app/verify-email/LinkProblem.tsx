"use client";

import Link from "next/link";
import { useState, useSyncExternalStore, type FormEvent } from "react";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { AUTH_FLOW_MESSAGES } from "@/lib/i18n/authFlow";
import { accountMessages } from "@/lib/i18n/account";
import { ResendVerification } from "../login/VerificationNotice";
import { decodeProfile, useProfileRead } from "../profile/useProfileRead";

const REASON_TEXT = { expired: "linkReason", missing: "linkMissing", unavailable: "linkUnavailable" } as const;
const TEXT_LINK = "inline-flex min-h-11 min-w-11 items-center justify-center text-sm text-neutral-300 underline underline-offset-4 hover:text-white";

export interface ResendFormProps {
  email: string;
  onEmailChange: (value: string) => void;
  loading: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

function ResendForm({ email, onEmailChange, loading, onSubmit }: ResendFormProps) {
  const tr = useTranslations(AUTH_FLOW_MESSAGES);
  const label = useTranslations(accountMessages)("emailAddress");
  return (
    <form onSubmit={onSubmit} className="space-y-2">
      <p id="link-email-hint" className="text-sm text-neutral-300">{tr("linkEmailHint")}</p>
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
        <input type="email" autoComplete="email" required value={email} onChange={e => onEmailChange(e.target.value)}
          placeholder="you@example.com" aria-label={label} aria-describedby="link-email-hint"
          className="min-h-11 min-w-0 w-full flex-1 rounded-lg border border-neutral-700 bg-neutral-900/50 px-3 py-2.5 text-sm text-white placeholder:text-neutral-500 focus:border-cyan-500/50 focus:outline-none" />
        <button type="submit" disabled={loading || !email.trim()}
          className="min-h-11 whitespace-nowrap rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-4 py-2.5 text-sm text-cyan-300 transition-colors hover:bg-cyan-500/20 disabled:opacity-50">
          {loading ? "…" : tr("linkNewLink")}
        </button>
      </div>
    </form>
  );
}

/** A confirmation link that cannot be used: tells the person why and offers the way forward that fits their session. */
export type LinkReason = "expired" | "missing" | "unavailable";

export function LinkProblem({ reason, ...form }: { reason: LinkReason } & ResendFormProps) {
  const tr = useTranslations(AUTH_FLOW_MESSAGES);
  const auth = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const me = useProfileRead("auth/me", auth.accessToken, decodeProfile);
  const [other, setOther] = useState(false);
  const signedIn = me.state.kind === "success" && !other ? me.state.data : null;

  if (signedIn?.is_verified) {
    return (
      <section data-testid="state-confirmed" className="space-y-3">
        <h1 className="text-lg font-semibold text-white wrap-anywhere">{tr("linkConfirmedTitle", { email: signedIn.email })}</h1>
        <p className="text-sm text-neutral-300">{tr("linkConfirmedBody")}</p>
        <Link href="/profile" className={TEXT_LINK}>{tr("linkToProfile")}</Link>
        <button type="button" onClick={() => setOther(true)} className={`${TEXT_LINK} text-left`}>{tr("linkOtherAccount")}</button>
      </section>
    );
  }
  return (
    <section data-testid="state-link-problem" className="space-y-4">
      <h1 className="text-lg font-semibold text-white">{tr(reason === "unavailable" ? "linkUnavailableTitle" : "linkTitle")}</h1>
      <p className="text-sm text-neutral-300">{tr(REASON_TEXT[reason])}</p>
      {me.state.kind === "loading" && !other ? <p role="status" className="text-sm text-neutral-300">{tr("linkChecking")}</p>
        : signedIn ? <div><p className="text-sm text-neutral-200 wrap-anywhere">{tr("linkSendTo", { email: signedIn.email })}</p><ResendVerification email={signedIn.email} /></div>
        : <ResendForm {...form} />}
      <Link href="/login" className={TEXT_LINK}>{tr("linkSignIn")}</Link>
    </section>
  );
}
