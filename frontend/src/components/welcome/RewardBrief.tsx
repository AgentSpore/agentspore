'use client';

import Link from 'next/link';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { WELCOME_FOCUS } from './focus';
import { tokenMessages } from '@/lib/i18n/tokenPage';

const LINK = `inline-flex min-h-11 items-center text-sm text-violet-300 underline underline-offset-2 ${WELCOME_FOCUS}`;
const LINES = ['briefNeeds', 'briefOne', 'briefReview', 'briefPayout'] as const;

/** The reward promise: amount, requirements, one-per rule, 48 h review, automatic payout. `compact` shows a two-line banner with the rules in a disclosure. */
export function RewardBrief({ headingId, level = 2, compact = false }: { headingId: string; level?: 2 | 3; compact?: boolean }) {
  const tr = useTranslations(tokenMessages);
  const Heading = `h${level}` as const;
  const rules = <ul className="list-disc space-y-1 pl-5">{LINES.map(key => <li key={key}>{tr(key)}</li>)}</ul>;
  const link = <Link href="/token" className={LINK}>{tr('briefLink')}</Link>;
  if (compact) {
    return <section aria-labelledby={headingId} className="rounded-xl border border-violet-500/20 bg-violet-500/10 px-4 py-3 text-sm leading-6 text-neutral-200">
      <Heading id={headingId} className="text-sm font-semibold text-white">{tr('briefTitle')}</Heading>
      <p>{tr('briefCompact')}</p>
      <details>
        <summary className={`${LINK} cursor-pointer`}>{tr('briefDetails')}</summary>
        <div className="space-y-2 pb-2">{rules}{link}</div>
      </details>
    </section>;
  }
  return <section aria-labelledby={headingId} className="space-y-2 rounded-xl border border-violet-500/20 bg-violet-500/10 p-4 text-sm leading-6 text-neutral-200">
    <Heading id={headingId} className="text-base font-semibold text-white">{tr('briefTitle')}</Heading>
    <p>{tr('briefIntro')}</p>
    {rules}
    {link}
  </section>;
}
