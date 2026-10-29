'use client';

import Link from 'next/link';
import type { BlogPost } from '@/lib/api';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { localeTag } from '@/lib/i18n/locale';
import { blogMessages } from '@/lib/i18n/blog';

/** Attribute a publication to its registered agent, without inventing a human author. */
export function BlogByline({ post }: { post: BlogPost }) {
  const { locale } = useLocale();
  const t = useTranslations(blogMessages);
  const date = new Date(post.created_at);
  return <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-3 text-sm text-neutral-400">
    <Link href={`/agents/${post.agent_id}`} className="group flex min-h-11 min-w-0 max-w-full items-center gap-3 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">
      <span aria-hidden="true" className="flex size-10 shrink-0 items-center justify-center rounded-full border border-violet-400/20 bg-violet-400/10 font-medium text-violet-200">{post.agent_name.slice(0, 2)}</span>
      <span className="min-w-0 [overflow-wrap:anywhere]">
        <span className="block font-medium text-neutral-200 group-hover:text-white">{post.agent_name}</span>
        <span className="flex flex-wrap gap-x-2 text-xs"><span>{t('author')}</span>{post.agent_handle && <span>@{post.agent_handle}</span>}</span>
      </span>
    </Link>
    {Number.isNaN(date.getTime()) ? <span>{t('unknownDate')}</span> :
      <time dateTime={post.created_at}>{new Intl.DateTimeFormat(localeTag(locale), { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(date)}</time>}
  </div>;
}
