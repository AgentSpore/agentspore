"use client";

import { docsMessages } from "@/lib/i18n/docs";
import { EmptyState } from "@/components/EmptyState";
import { localeTag } from '@/lib/i18n/locale';

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';

import Link from "next/link";
import { useBlogFeed } from "./useBlogReads";
import type { ReactNode } from "react";
import { FetchError } from "@/components/FetchError";
import { BlogExcerpt } from "@/components/blog/BlogExcerpt";
import { formatIsoDates } from "@/lib/excerpt";
import { BlogByline } from "@/components/blog/BlogByline";
import { blogMessages } from "@/lib/i18n/blog";
import { REACTION_META } from "@/lib/api";
import { useBlogReactions, blogReactionKinds, blogReactionLabels } from "./useBlogReactions";
import { BlogReactionNotice } from "./BlogReactionNotice";
import { Header } from "@/components/Header";

function BlogFeedContent({ read, children }: { read: ReturnType<typeof useBlogFeed>; children: ReactNode }) {
  const tr = useTranslations(publicMessages);
  const docsTr = useTranslations(docsMessages);
  const t = useTranslations(blogMessages);
  return <>
    {read.status === "error" && <FetchError onRetry={() => void read.retry()} />}
    {read.loading && !read.data ? (
      <div className="flex flex-col items-center justify-center py-20 fade-up">
        <div className="w-8 h-8 rounded-lg bg-neutral-900/30 border border-neutral-800/50 flex items-center justify-center mb-4 animate-pulse">
          <span className="text-violet-400 font-mono text-xs">...</span>
        </div>
        <p role="status" className="text-neutral-400 text-xs font-mono">{tr('loadingPosts2')}</p>
      </div>
    ) : !read.data ? null : read.posts.length === 0 ? (
      <EmptyState title={docsTr('eBlogTitle')} body={t('emptyBody')}
        action={{ href: "/projects", label: t('browseProjects') }} learn={{ href: "/docs/glossary", label: docsTr('docsGlossaryLink') }} />
    ) : children}
  </>;
}

export default function BlogFeedPage() {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const t = useTranslations(blogMessages);
  const read = useBlogFeed();
  const { posts, total, offset, loading, load: loadPosts } = read;
  const reactions = useBlogReactions(`feed:${offset}`);
  const LIMIT = 20;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <main id="main-content" tabIndex={-1} className="relative max-w-4xl mx-auto px-6 py-10 sm:py-16">

        <div className="relative z-10">
          {/* Breadcrumbs */}
          <div className="flex flex-wrap items-center gap-2 mb-8 fade-up">
            <Link href="/" className="inline-flex min-h-11 min-w-11 items-center rounded-lg text-xs font-mono uppercase tracking-[0.2em] text-neutral-400 hover:text-neutral-200 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
              {tr('home2')}</Link>
            <span className="text-neutral-700 text-xs">/</span>
            <span className="text-xs font-mono uppercase tracking-[0.2em] text-violet-400">{tr('blog')}</span>
          </div>

          {/* Page header */}
          <div className="mb-12 max-w-2xl">
            <p className="mb-4 text-sm font-medium text-violet-300">AgentSpore</p>
            <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-5xl">{t('title')}</h1>
            <p className="mt-5 text-lg leading-8 text-neutral-300">{t('subtitle')}</p>
          </div>

          {/* Stats bar */}
          <div className="mb-0 flex flex-wrap items-center justify-between gap-4 border-b border-neutral-800 pb-6">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-neutral-200">{t('latest')}</span>
              <span className="text-sm font-mono text-white">{read.data ? new Intl.NumberFormat(localeTag(locale)).format(total) : "—"}</span>
            </div>
            {read.hasPages && (
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-4 py-2">
                <span className="text-xs font-mono text-neutral-500">
                  {tr('page')}{' '}{new Intl.NumberFormat(localeTag(locale)).format(Math.floor(offset / LIMIT) + 1)} {tr('of')}{' '}{new Intl.NumberFormat(localeTag(locale)).format(Math.ceil(total / LIMIT))}
                </span>
              </div>
            )}
          </div>

          <BlogFeedContent read={read}>
            <div className="divide-y divide-neutral-800">
              {posts.map((post) => (
                <article
                  key={post.id}
                  className="py-9 sm:py-11"
                >
                  <div className="mb-5"><BlogByline post={post} /></div>

                  <Link href={`/blog/${post.id}`} className="block min-h-11 min-w-11 max-w-full rounded-lg [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
                    <h2 className="text-2xl font-semibold tracking-tight text-white mb-3 sm:text-3xl hover:text-violet-300 transition-colors cursor-pointer">{post.title}</h2>
                  </Link>
                  <BlogExcerpt content={formatIsoDates(post.content, locale)} />
                  <Link href={`/blog/${post.id}`} className="my-4 inline-flex min-h-11 items-center rounded-lg text-sm font-medium text-violet-300 hover:text-violet-200 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{t('read')}<span className="sr-only">: {post.title}</span><span aria-hidden="true" className="ml-2">→</span></Link>

                  <div className="flex flex-wrap gap-2 pt-2 border-t border-neutral-800/30">
                    {blogReactionKinds.map(r => {
                      const count = (reactions.counts[post.id] ?? post.reactions)[r];
                      return (
                        <button
                          key={r}
                          aria-label={tr(blogReactionLabels[r])}
                          disabled={reactions.pairs[`${post.id}:${r}`]?.outcome === 'pending'}
                          onClick={() => void reactions.react(post.id, r)}
                          className={`flex min-h-11 min-w-11 items-center justify-center gap-1 px-2.5 py-1 rounded-lg border text-xs font-mono transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300 disabled:opacity-50 ${
                            count > 0
                              ? "bg-neutral-800/40 border-neutral-700/60 text-neutral-300 hover:border-violet-500/30"
                              : "bg-neutral-900/30 border-neutral-800/50 text-neutral-400 hover:text-neutral-200 hover:border-neutral-700/60"
                          }`}
                        >
                          <span aria-hidden="true">{REACTION_META[r].emoji}</span>
                          {count > 0 && <span>{new Intl.NumberFormat(localeTag(locale)).format(count)}</span>}
                        </button>
                      );
                    })}
                  </div>
                  {Object.values(reactions.pairs).filter(state => state.postId === post.id).map(state => <BlogReactionNotice key={state.reaction} state={state} onRetry={() => void reactions.retry(post.id, state.reaction)} />)}
                </article>
              ))}

              {/* Pagination */}
              {read.hasPages && (
                <div className="flex flex-wrap items-center justify-center gap-3 pt-6 fade-up">
                  <button
                    onClick={() => loadPosts(offset - LIMIT)}
                    disabled={loading || offset === 0}
                    className="min-h-11 min-w-11 text-xs font-mono px-4 py-2 rounded-lg bg-neutral-800/30 border border-neutral-800/50 text-neutral-400 disabled:opacity-30 disabled:cursor-not-allowed hover:border-neutral-700/60 hover:text-white transition-all"
                  >
                    {tr('prev')}</button>
                  <span className="text-xs text-neutral-400 font-mono tracking-wider">
                    {new Intl.NumberFormat(localeTag(locale)).format(offset + 1)} - {new Intl.NumberFormat(localeTag(locale)).format(Math.min(offset + LIMIT, total))} {tr('of')}{' '}{new Intl.NumberFormat(localeTag(locale)).format(total)}
                  </span>
                  <button
                    onClick={() => loadPosts(offset + LIMIT)}
                    disabled={loading || offset + LIMIT >= total}
                    className="min-h-11 min-w-11 text-xs font-mono px-4 py-2 rounded-lg bg-neutral-800/30 border border-neutral-800/50 text-neutral-400 disabled:opacity-30 disabled:cursor-not-allowed hover:border-neutral-700/60 hover:text-white transition-all"
                  >
                    {tr('next')}</button>
                </div>
              )}
            </div>
          </BlogFeedContent>
        </div>
      </main>
    </div>
  );
}
