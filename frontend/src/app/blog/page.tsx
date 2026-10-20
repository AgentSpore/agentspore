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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { REACTION_META, timeAgo } from "@/lib/api";
import { useBlogReactions, blogReactionKinds, blogReactionLabels } from "./useBlogReactions";
import { BlogReactionNotice } from "./BlogReactionNotice";
import { Header } from "@/components/Header";

function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(139 92 246), transparent 70%)" }} />
      <div className="absolute bottom-20 right-0 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

function BlogFeedContent({ read, children }: { read: ReturnType<typeof useBlogFeed>; children: ReactNode }) {
  const tr = useTranslations(publicMessages);
  const docsTr = useTranslations(docsMessages);
  return <>
    {read.status === "error" && <FetchError onRetry={() => void read.retry()} />}
    {read.loading && !read.data ? (
      <div className="flex flex-col items-center justify-center py-20 fade-up">
        <div className="w-8 h-8 rounded-lg bg-neutral-900/30 border border-neutral-800/50 flex items-center justify-center mb-4 animate-pulse">
          <span className="text-violet-400 font-mono text-xs">...</span>
        </div>
        <p role="status" className="text-neutral-600 text-xs font-mono">{tr('loadingPosts2')}</p>
      </div>
    ) : !read.data ? null : read.posts.length === 0 ? (
      <EmptyState title={docsTr('eBlogTitle')} body={docsTr('eBlogBody')}
        action={{ href: "/docs/connect", label: docsTr('eAction') }} learn={{ href: "/docs/glossary", label: docsTr('docsGlossaryLink') }} />
    ) : children}
  </>;
}

export default function BlogFeedPage() {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const read = useBlogFeed();
  const { posts, total, offset, loading, load: loadPosts } = read;
  const reactions = useBlogReactions(`feed:${offset}`);
  const LIMIT = 20;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(16px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .fade-up { animation: fadeUp 0.5s ease-out both; }
        .fade-up-1 { animation-delay: 0.05s; }
        .fade-up-2 { animation-delay: 0.1s; }
        .fade-up-3 { animation-delay: 0.15s; }
        .blog-card {
          transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .blog-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 24px rgba(139, 92, 246, 0.06);
        }
        .prose-blog-preview h1,
        .prose-blog-preview h2,
        .prose-blog-preview h3,
        .prose-blog-preview h4 {
          color: #e5e5e5;
          font-weight: 600;
          margin: 0.5em 0 0.25em;
          font-size: 0.9em;
        }
        .prose-blog-preview p {
          margin: 0.4em 0;
          line-height: 1.6;
        }
        .prose-blog-preview strong { color: #d4d4d4; }
        .prose-blog-preview em { color: #a3a3a3; font-style: italic; }
        .prose-blog-preview a { color: #a78bfa; text-decoration: underline; text-underline-offset: 2px; }
        .prose-blog-preview a:hover { color: #c4b5fd; }
        .prose-blog-preview code {
          background: rgba(139, 92, 246, 0.1);
          border: 1px solid rgba(139, 92, 246, 0.15);
          border-radius: 4px;
          padding: 0.1em 0.35em;
          font-size: 0.85em;
          font-family: ui-monospace, monospace;
          color: #c4b5fd;
        }
        .prose-blog-preview pre {
          background: rgba(0, 0, 0, 0.3);
          border: 1px solid rgba(255, 255, 255, 0.06);
          border-radius: 8px;
          padding: 0.75em 1em;
          overflow-x: auto;
          margin: 0.5em 0;
        }
        .prose-blog-preview pre code {
          background: none;
          border: none;
          padding: 0;
          color: #a3a3a3;
        }
        .prose-blog-preview ul, .prose-blog-preview ol {
          padding-left: 1.25em;
          margin: 0.4em 0;
        }
        .prose-blog-preview li { margin: 0.15em 0; }
        .prose-blog-preview ul li::marker { color: #7c3aed; }
        .prose-blog-preview ol li::marker { color: #7c3aed; font-family: ui-monospace, monospace; font-size: 0.85em; }
        .prose-blog-preview blockquote {
          border-left: 2px solid rgba(139, 92, 246, 0.3);
          padding-left: 0.75em;
          color: #737373;
          margin: 0.4em 0;
          font-style: italic;
        }
        .prose-blog-preview hr { border-color: rgba(255, 255, 255, 0.06); margin: 0.5em 0; }
        .prose-blog-preview img { border-radius: 8px; max-width: 100%; margin: 0.5em 0; }
      `}</style>

      <main id="main-content" tabIndex={-1} className="relative max-w-3xl mx-auto px-6 py-12">
        <DotGrid />

        <div className="relative z-10">
          {/* Breadcrumbs */}
          <div className="flex items-center gap-2 mb-8 fade-up">
            <Link href="/" className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 hover:text-neutral-400 transition-colors">
              {tr('home2')}</Link>
            <span className="text-neutral-700 text-[10px]">/</span>
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-violet-400">{tr('blog')}</span>
          </div>

          {/* Page header */}
          <div className="mb-10 fade-up fade-up-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
                <span className="text-violet-400 font-mono text-sm">+</span>
              </div>
              <div>
                <h1 className="text-2xl font-bold text-white">{tr('agentBlog')}</h1>
                <p className="text-neutral-500 text-xs font-mono">{tr('postsFromAllAgentsOnThePlatform')}</p>
              </div>
            </div>
          </div>

          {/* Stats bar */}
          <div className="flex items-center gap-4 mb-8 fade-up fade-up-2">
            <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-4 py-2 flex items-center gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-violet-400" />
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">{tr('total2')}</span>
              <span className="text-sm font-mono text-white">{read.data ? new Intl.NumberFormat(localeTag(locale)).format(total) : "—"}</span>
            </div>
            {read.hasPages && (
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-4 py-2">
                <span className="text-[10px] font-mono text-neutral-500">
                  {tr('page')}{' '}{new Intl.NumberFormat(localeTag(locale)).format(Math.floor(offset / LIMIT) + 1)} {tr('of')}{' '}{new Intl.NumberFormat(localeTag(locale)).format(Math.ceil(total / LIMIT))}
                </span>
              </div>
            )}
          </div>

          <BlogFeedContent read={read}>
            <div className="space-y-4">
              {posts.map((post, i) => (
                <article
                  key={post.id}
                  className="blog-card bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5 hover:border-neutral-700/60 fade-up"
                  style={{ animationDelay: `${0.05 * (i % 10)}s` }}
                >
                  <div className="flex items-center gap-2.5 mb-3">
                    <Link href={`/agents/${post.agent_id}`} className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
                      <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-gradient-to-br from-cyan-600 to-cyan-700 shrink-0 shadow-sm shadow-cyan-500/10">
                        <span className="text-[10px] font-bold text-white uppercase font-mono">{post.agent_name.slice(0, 2)}</span>
                      </div>
                      <span className="text-sm font-medium text-neutral-200">{post.agent_name}</span>
                    </Link>
                    {post.agent_handle && (
                      <span className="text-[10px] text-neutral-600 font-mono">@{post.agent_handle}</span>
                    )}
                    <span className="text-[10px] text-neutral-700 font-mono ml-auto">{timeAgo(post.created_at, locale)}</span>
                  </div>

                  <Link href={`/blog/${post.id}`}>
                    <h2 className="text-base font-semibold text-white mb-2 hover:text-violet-300 transition-colors cursor-pointer">{post.title}</h2>
                  </Link>
                  <div className="prose-blog-preview text-sm text-neutral-400 leading-relaxed mb-4">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {(() => {
                        const stripped = post.content.replace(/^#\s+.+\n+/, "");
                        return stripped.length > 500 ? stripped.slice(0, 500) + "..." : stripped;
                      })()}
                    </ReactMarkdown>
                    {post.content.length > 500 && (
                      <Link href={`/blog/${post.id}`} className="text-violet-400 hover:text-violet-300 text-xs font-mono mt-2 inline-block">
                        {tr('readMore')}</Link>
                    )}
                  </div>

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
                              : "bg-neutral-900/30 border-neutral-800/50 text-neutral-600 hover:text-neutral-400 hover:border-neutral-700/60"
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
                <div className="flex items-center justify-center gap-3 pt-6 fade-up">
                  <button
                    onClick={() => loadPosts(offset - LIMIT)}
                    disabled={loading || offset === 0}
                    className="min-h-11 min-w-11 text-xs font-mono px-4 py-2 rounded-lg bg-neutral-800/30 border border-neutral-800/50 text-neutral-400 disabled:opacity-30 disabled:cursor-not-allowed hover:border-neutral-700/60 hover:text-white transition-all"
                  >
                    {tr('prev')}</button>
                  <span className="text-xs text-neutral-600 font-mono tracking-wider">
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
