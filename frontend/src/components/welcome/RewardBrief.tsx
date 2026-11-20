'use client';

import Link from 'next/link';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { WELCOME_FOCUS } from './focus';
import { tokenMessages } from '@/lib/i18n/tokenPage';

const LINK = `inline-flex min-h-11 items-center text-sm text-violet-300 underline underline-offset-2 ${WELCOME_FOCUS}`;
const LINES = ['briefNeeds', 'briefOne', 'briefReview', 'briefPayout'] as const;

/** The reward promise in a few lines: amount, requirements, one-per rule, 48 h review, automatic payout. */
export function RewardBrief({ headingId, level = 2 }: { headingId: string; level?: 2 | 3 }) {
  const tr = useTranslations(tokenMessages);
  const Heading = `h${level}` as const;
  return <section aria-labelledby={headingId} className="space-y-2 rounded-xl border border-violet-500/20 bg-violet-500/10 p-4 text-sm leading-6 text-neutral-200">
    <Heading id={headingId} className="text-base font-semibold text-white">{tr('briefTitle')}</Heading>
    <p>{tr('briefIntro')}</p>
    <ul className="list-disc space-y-1 pl-5">{LINES.map(key => <li key={key}>{tr(key)}</li>)}</ul>
    <Link href="/token" className={LINK}>{tr('briefLink')}</Link>
  </section>;
}
