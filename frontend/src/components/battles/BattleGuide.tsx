"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";

/** True only once the browser snapshot is known and holds no access token. */
export function useIsAnonymous(): boolean {
  const auth = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  return auth.generation >= 0 && !auth.accessToken;
}

/** Explains why challenging needs an account instead of silently redirecting to login. */
export function AnonChallengeNotice({ next = "/battles/new" }: { next?: string }) {
  const tr = useTranslations(docsMessages);
  return (
    <section aria-labelledby="battle-anon" className="mb-6 rounded-xl border border-violet-500/30 bg-violet-500/5 p-4">
      <h2 id="battle-anon" className="text-sm font-medium text-white">{tr("bAnonTitle")}</h2>
      <p className="mt-1 text-sm leading-6 text-neutral-300">{tr("bAnonBody")}</p>
      <Link href={`/login?next=${next}`} className="mt-3 inline-flex min-h-11 items-center rounded-lg bg-violet-600 px-4 text-sm font-medium text-white hover:bg-violet-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
        {tr("bSignIn")}
      </Link>
    </section>
  );
}

const QUESTIONS = [1, 2, 3] as const;

/** Short FAQ with a pointer to the glossary. */
export function BattleFaq() {
  const tr = useTranslations(docsMessages);
  return (
    <section aria-labelledby="battle-faq" className="mb-6 rounded-xl border border-neutral-800 bg-neutral-900/30 p-4 sm:p-5">
      <h2 id="battle-faq" className="text-xs font-medium text-neutral-300">{tr("bFaqTitle")}</h2>
      <dl className="mt-3 space-y-3 text-sm">
        {QUESTIONS.map((n) => (
          <div key={n}>
            <dt className="font-medium text-neutral-100">{tr(`bQ${n}`)}</dt>
            <dd className="mt-0.5 leading-6 text-neutral-300">{tr(`bA${n}`)}</dd>
          </div>
        ))}
      </dl>
      <Link href="/docs/glossary#rated" className="mt-3 inline-flex min-h-11 min-w-11 items-center text-xs text-violet-300 underline underline-offset-2">{tr("openGlossary")}</Link>
    </section>
  );
}
