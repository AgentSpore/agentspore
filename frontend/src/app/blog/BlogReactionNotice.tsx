"use client";

import Link from 'next/link';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';
import type { BlogReactionState } from './useBlogReactions';

const control = 'inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-neutral-700 px-3 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300';

/** Display one global reaction outcome and its explicit recovery action. */
export function BlogReactionNotice({ state, onRetry }: { state: BlogReactionState; onRetry: () => void }) {
  const tr = useTranslations(publicMessages), shared = useTranslations(sharedMessages);
  const key = state.outcome === 'login' ? 'blogReactionLogin' : state.outcome === 'counts' ? 'blogReactionCounts' : 'blogReactionError';
  return <div role={state.outcome === 'pending' ? 'status' : 'alert'} className="mt-2 flex min-w-0 max-w-full flex-wrap items-center gap-2 text-sm text-neutral-300 [overflow-wrap:anywhere]">
    <p>{state.outcome === 'pending' ? tr('loading') : tr(key)}</p>
    {state.outcome === 'login' && <Link href="/login" className={control}>{tr('signIn')}</Link>}
    {(state.outcome === 'error' || state.outcome === 'counts') && <button type="button" className={control} onClick={onRetry}>{shared('retry')}</button>}
  </div>;
}
