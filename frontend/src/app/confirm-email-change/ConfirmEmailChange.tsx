'use client';

import Link from 'next/link';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { emailChangeMessages } from '@/lib/i18n/emailChange';
import { useEmailChangeConfirm, type EmailChangeOutcome } from './useEmailChangeConfirm';

const LINK = 'inline-flex min-h-11 items-center justify-center rounded-lg border border-neutral-600 px-4 py-2 text-sm text-neutral-100 hover:bg-neutral-800';
const FAILED: EmailChangeOutcome[] = ['missing', 'invalid', 'pending', 'network'];

export function ConfirmEmailChange() {
  const tr = useTranslations(emailChangeMessages);
  const { outcome, retry } = useEmailChangeConfirm();
  const busy = outcome === 'reading' || outcome === 'working';
  return <main id="main-content" tabIndex={-1} className="mx-auto flex min-h-screen w-full max-w-md items-center px-4 py-10">
    <section aria-labelledby="confirm-email-title" aria-busy={busy} className="w-full min-w-0 space-y-4 rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-5 sm:p-6">
      <h1 id="confirm-email-title" className="text-lg font-semibold text-white">{tr('pageTitle')}</h1>
      <p role={FAILED.includes(outcome) ? 'alert' : 'status'} className="break-words text-sm leading-relaxed text-neutral-200">
        {tr(outcome)}
      </p>
      {outcome === 'needSignIn' && <Link href="/login?next=/confirm-email-change" className={LINK}>{tr('signInLink')}</Link>}
      {outcome === 'network' && <button type="button" onClick={retry} className={LINK}>{tr('retry')}</button>}
      {(outcome === 'done' || outcome === 'approved' || outcome === 'invalid' || outcome === 'pending') &&
        <Link href="/profile" className={LINK}>{tr('toProfile')}</Link>}
    </section>
  </main>;
}
