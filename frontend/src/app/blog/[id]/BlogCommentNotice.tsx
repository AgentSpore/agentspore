"use client";

import Link from 'next/link';
import type { BlogComment } from '@/lib/api';
import { ChatIdentityNotice } from '@/components/ChatIdentityNotice';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';
import type { useBlogCommentActions } from './useBlogCommentActions';

type Actions = ReturnType<typeof useBlogCommentActions>;
const control = 'min-h-11 min-w-11 rounded-lg px-3 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400';

/** Present current comment outcomes without exposing HTTP or exception details. */
export function BlogCommentNotice({ outcome, removal = false, onRetry }: {
  outcome: Actions['outcomes'][string] | undefined; removal?: boolean; onRetry?: () => void;
}) {
  const tr = useTranslations(publicMessages), sharedTr = useTranslations(sharedMessages);
  if (!outcome) return null;
  if (outcome === 'pending') return <p role="status" className="text-sm text-neutral-400">{tr('loading')}</p>;
  const message = outcome === 'login' ? 'blogCommentLogin' : removal ? 'blogCommentDeleteError' : 'blogCommentError';
  return <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-neutral-300 break-words [overflow-wrap:anywhere]">
    <p>{tr(message)}</p>
    {outcome === 'login' && <Link href="/login" className={`inline-flex items-center justify-center ${control}`}>{tr('signIn')}</Link>}
    {outcome === 'error' && onRetry && <button type="button" onClick={onRetry} className={control}>{sharedTr('retry')}</button>}
  </div>;
}

/** Expose deletion only for the current confirmed user author. */
export function BlogCommentDelete({ actions, comment }: { actions: Actions; comment: BlogComment }) {
  const tr = useTranslations(publicMessages);
  if (!actions.canDelete(comment)) return null;
  return <button type="button" onClick={() => void actions.remove(comment)} disabled={actions.outcomes[comment.id] === 'pending'}
    title={tr('delete')} aria-label={tr('delete')}
    className={`${control} ml-auto shrink-0 text-neutral-400 hover:text-red-400 hover:bg-red-400/10 disabled:opacity-50`}>&times;</button>;
}

/** Keep profile verification truthful without discarding the retained composer. */
export function BlogCommentIdentity({ actions }: { actions: Actions }) {
  const tr = useTranslations(publicMessages);
  return <><ChatIdentityNotice state={actions.identity} />
    {actions.identity.status === 'signed-out' && <p className="text-sm text-neutral-400"><Link href="/login" className={`inline-flex items-center ${control}`}>{tr('blogCommentLogin')}</Link></p>}
  </>;
}
