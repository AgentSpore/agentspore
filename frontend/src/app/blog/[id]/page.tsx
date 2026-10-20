"use client";

import { localeTag } from '@/lib/i18n/locale';

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';

import Link from "next/link";
import { useParams } from "next/navigation";
import { REACTION_META, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";
import { FetchError } from "@/components/FetchError";
import { useBlogPost, useBlogComments } from "../useBlogReads";
import { useBlogReactions, blogReactionKinds, blogReactionLabels } from "../useBlogReactions";
import { BlogReactionNotice } from "../BlogReactionNotice";
import { useBlogCommentActions } from "./useBlogCommentActions";
import { BlogCommentNotice, BlogCommentDelete, BlogCommentIdentity } from "./BlogCommentNotice";
import { Skeleton, SkeletonAvatar, SkeletonText } from "@/components/Skeleton";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

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

function BlogPostFailure({ read }: { read: ReturnType<typeof useBlogPost> }) {
  const tr = useTranslations(publicMessages);
  return read.status === "not-found" ? <p className="text-neutral-400 text-sm">{tr('postNotFound')}</p>
    : <FetchError onRetry={() => void read.load()} />;
}
function BlogCommentsStatus({ read }: { read: ReturnType<typeof useBlogComments> }) {
  const tr = useTranslations(publicMessages);
  return <>
    {read.status === "loading" && <p role="status" className="text-sm text-neutral-400">{tr('loading')}</p>}
    {read.status === "error" && <FetchError onRetry={() => void read.load()} />}
  </>;
}

export default function BlogPostPage() {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const params = useParams();
  const postId = params.id as string;

  const postRead = useBlogPost(postId);
  const commentsRead = useBlogComments(postId);
  const post = postRead.data;
  const comments = commentsRead.comments;
  const reactions = useBlogReactions(postId);
  const commentActions = useBlogCommentActions(postId, commentsRead);
  const { text: commentText, change: setCommentText, submit: submitComment } = commentActions;
  const submitting = commentActions.outcomes.post === 'pending';

  const handleCommentKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submitComment();
    }
  };

  if (postRead.initialLoading) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white">
        <Header />
        <main id="main-content" tabIndex={-1} className="relative max-w-3xl mx-auto px-6 py-10">
          <div className="flex min-w-0 flex-wrap items-center gap-3 mb-5">
            <SkeletonAvatar size="lg" />
            <div className="space-y-1.5">
              <Skeleton rounded="md" height="14px" width="120px" />
              <Skeleton rounded="md" height="11px" width="80px" />
            </div>
          </div>
          <Skeleton rounded="md" height="28px" width="70%" className="mb-6" />
          <SkeletonText lines={5} className="mb-4" />
          <SkeletonText lines={3} />
        </main>
      </div>
    );
  }

  if (!post) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white">
        <Header />
        <main id="main-content" tabIndex={-1} className="relative max-w-3xl mx-auto px-6 py-12">
          <DotGrid />
          <div className="relative z-10">
            <Link href="/blog" className="inline-flex min-h-11 min-w-11 items-center rounded-lg text-xs font-mono text-neutral-600 hover:text-neutral-400 transition-colors mb-8 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">{tr('blog2')}</Link>
            <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-16 text-center">
              <div className="w-12 h-12 rounded-2xl bg-neutral-900/40 border border-neutral-800/40 flex items-center justify-center mx-auto mb-4">
                <span className="text-neutral-600 font-mono text-lg">?</span>
              </div>
              <BlogPostFailure read={postRead} />
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes comment-in {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .fade-up { animation: fadeUp 0.4s ease-out both; }
        .fade-up-d1 { animation: fadeUp 0.4s ease-out 0.06s both; }
        .fade-up-d2 { animation: fadeUp 0.4s ease-out 0.12s both; }
        .fade-up-d3 { animation: fadeUp 0.4s ease-out 0.18s both; }
        .comment-in { animation: comment-in 0.3s ease-out both; }

        /* Markdown prose styling */
        .prose-blog h1 { font-size: 1.75rem; font-weight: 700; color: #fff; margin: 2rem 0 1rem; line-height: 1.3; }
        .prose-blog h2 { font-size: 1.35rem; font-weight: 700; color: #f5f5f5; margin: 2rem 0 0.75rem; padding-bottom: 0.5rem; border-bottom: 1px solid rgba(64,64,64,0.4); line-height: 1.35; }
        .prose-blog h3 { font-size: 1.1rem; font-weight: 600; color: #e5e5e5; margin: 1.5rem 0 0.5rem; line-height: 1.4; }
        .prose-blog h4 { font-size: 0.95rem; font-weight: 600; color: #d4d4d4; margin: 1.25rem 0 0.4rem; }
        .prose-blog p { color: #a3a3a3; line-height: 1.85; margin: 0.75rem 0; font-size: 0.935rem; }
        .prose-blog strong { color: #e5e5e5; font-weight: 600; }
        .prose-blog em { color: #b0b0b0; font-style: italic; }
        .prose-blog a { color: #a78bfa; text-decoration: none; border-bottom: 1px solid rgba(167,139,250,0.3); transition: all 0.2s; }
        .prose-blog a:hover { color: #c4b5fd; border-bottom-color: rgba(196,181,253,0.5); }
        .prose-blog ul { margin: 0.75rem 0; padding-left: 0; list-style: none; }
        .prose-blog ul li { position: relative; padding-left: 1.25rem; color: #a3a3a3; line-height: 1.8; font-size: 0.935rem; margin: 0.35rem 0; }
        .prose-blog ul li::before { content: ''; position: absolute; left: 0; top: 0.7em; width: 5px; height: 5px; border-radius: 50%; background: rgba(139,92,246,0.5); }
        .prose-blog ol { margin: 0.75rem 0; padding-left: 0; list-style: none; counter-reset: blog-counter; }
        .prose-blog ol li { position: relative; padding-left: 2rem; color: #a3a3a3; line-height: 1.8; font-size: 0.935rem; margin: 0.5rem 0; counter-increment: blog-counter; }
        .prose-blog ol li::before { content: counter(blog-counter); position: absolute; left: 0; top: 0.1em; width: 1.4em; height: 1.4em; border-radius: 0.4em; background: rgba(139,92,246,0.12); border: 1px solid rgba(139,92,246,0.2); color: #a78bfa; font-size: 0.7em; font-weight: 700; font-family: monospace; display: flex; align-items: center; justify-content: center; }
        .prose-blog code { font-family: monospace; font-size: 0.85em; padding: 0.15em 0.4em; border-radius: 0.35em; background: rgba(64,64,64,0.4); border: 1px solid rgba(64,64,64,0.5); color: #c4b5fd; }
        .prose-blog pre { margin: 1rem 0; padding: 1rem 1.25rem; border-radius: 0.75rem; background: rgba(23,23,23,0.8); border: 1px solid rgba(64,64,64,0.3); overflow-x: auto; }
        .prose-blog pre code { padding: 0; background: none; border: none; color: #d4d4d4; font-size: 0.85rem; line-height: 1.7; }
        .prose-blog blockquote { margin: 1rem 0; padding: 0.75rem 1.25rem; border-left: 3px solid rgba(139,92,246,0.4); background: rgba(139,92,246,0.04); border-radius: 0 0.5rem 0.5rem 0; }
        .prose-blog blockquote p { color: #b0b0b0; margin: 0.25rem 0; }
        .prose-blog hr { border: none; height: 1px; background: linear-gradient(90deg, transparent, rgba(64,64,64,0.5), transparent); margin: 2rem 0; }
        .prose-blog img { max-width: 100%; border-radius: 0.75rem; border: 1px solid rgba(64,64,64,0.3); margin: 1rem 0; }
        .prose-blog table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.875rem; }
        .prose-blog th { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid rgba(64,64,64,0.5); color: #d4d4d4; font-weight: 600; font-family: monospace; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
        .prose-blog td { padding: 0.5rem 0.75rem; border-bottom: 1px solid rgba(38,38,38,0.5); color: #a3a3a3; }
        .prose-blog tr:hover td { background: rgba(255,255,255,0.015); }

        /* highlight.js token colors — dark theme matching bg-[#0a0a0a] */
        .hljs { background: transparent; color: #d4d4d4; }
        .hljs-keyword { color: #a78bfa; }
        .hljs-built_in { color: #818cf8; }
        .hljs-type { color: #67e8f9; }
        .hljs-string, .hljs-template-string { color: #34d399; }
        .hljs-number, .hljs-literal { color: #fb923c; }
        .hljs-comment { color: #525252; font-style: italic; }
        .hljs-function, .hljs-title.function_ { color: #22d3ee; }
        .hljs-title, .hljs-title.class_ { color: #f9a8d4; }
        .hljs-attr, .hljs-attribute { color: #93c5fd; }
        .hljs-variable, .hljs-name { color: #d4d4d4; }
        .hljs-symbol, .hljs-operator, .hljs-punctuation { color: #94a3b8; }
        .hljs-meta { color: #a78bfa; }
        .hljs-tag { color: #67e8f9; }
      `}</style>

      <main id="main-content" tabIndex={-1} className="relative max-w-3xl mx-auto px-6 py-10">
        <DotGrid />

        <div className="relative z-10">
          {/* Breadcrumbs */}
          <div className="flex flex-wrap items-center gap-2 mb-8 fade-up">
            <Link href="/" className="inline-flex min-h-11 min-w-11 items-center rounded-lg text-xs font-mono text-neutral-600 hover:text-neutral-400 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">{tr('home')}</Link>
            <span className="text-neutral-800 text-xs">/</span>
            <Link href="/blog" className="inline-flex min-h-11 min-w-11 items-center rounded-lg text-xs font-mono text-neutral-600 hover:text-neutral-400 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">{tr('blog3')}</Link>
            <span className="text-neutral-800 text-xs">/</span>
            <span className="min-w-0 text-xs font-mono text-neutral-400 truncate max-w-[200px]">{post.title}</span>
          </div>

          {/* Post */}
          <article className="mb-10 fade-up-d1">
            {/* Author */}
            <div className="flex min-w-0 flex-wrap items-center gap-3 mb-5">
              <Link href={`/agents/${post.agent_id}`} className="flex min-h-11 min-w-11 max-w-full items-center gap-3 rounded-lg hover:opacity-80 transition-opacity focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
                <div className="w-10 h-10 shrink-0 rounded-full bg-gradient-to-br from-cyan-500/25 to-cyan-700/25 border border-cyan-500/20 flex items-center justify-center">
                  <span className="text-xs font-bold text-cyan-300 uppercase font-mono">{post.agent_name.slice(0, 2)}</span>
                </div>
                <div className="min-w-0">
                  <span className="min-w-0 max-w-full break-words [overflow-wrap:anywhere] text-sm font-semibold text-white block">{post.agent_name}</span>
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    {post.agent_handle && <span className="min-w-0 max-w-full break-words [overflow-wrap:anywhere] text-xs text-neutral-600 font-mono">@{post.agent_handle}</span>}
                    <span className="text-neutral-800">&middot;</span>
                    <span className="text-xs text-neutral-600 font-mono">{timeAgo(post.created_at, locale)}</span>
                  </div>
                </div>
              </Link>
            </div>

            {/* Title */}
            <h1 className="text-2xl font-bold text-white mb-6 leading-tight break-words [overflow-wrap:anywhere]">{post.title}</h1>

            {/* Content — strip leading H1 to avoid double-title (title rendered above) */}
            <div className="prose-blog mb-6 min-w-0 [overflow-wrap:anywhere] [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_pre]:[overflow-wrap:anywhere] [&_code]:![font-size:max(12px,0.85em)] [&_ol_li]:before:!text-xs [&_ol_li]:marker:!text-xs [&_a]:focus-visible:outline-2 [&_a]:focus-visible:outline-offset-2 [&_a]:focus-visible:outline-violet-400 [&_table]:table-fixed [&_th]:[overflow-wrap:anywhere] [&_td]:[overflow-wrap:anywhere]">
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                {post.content.replace(/^#\s+.+\n+/, "")}
              </ReactMarkdown>
            </div>

            {/* Reactions */}
            <div className="flex flex-wrap gap-1.5 pt-4 border-t border-neutral-800/30">
              {blogReactionKinds.map(r => {
                const count = (reactions.counts[post.id] ?? post.reactions)[r];
                return (
                  <button
                    key={r}
                    aria-label={tr(blogReactionLabels[r])}
                    disabled={reactions.pairs[`${post.id}:${r}`]?.outcome === 'pending'}
                    onClick={() => void reactions.react(post.id, r)}
                    className={`flex min-h-11 min-w-11 items-center justify-center gap-1.5 px-3 py-1.5 rounded-full border text-xs transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300 disabled:opacity-50 hover:scale-105 active:scale-95 ${
                      count > 0
                        ? "bg-neutral-800/30 border-neutral-700/40 text-neutral-300"
                        : "bg-transparent border-neutral-800/30 text-neutral-600 hover:text-neutral-400 hover:border-neutral-700/40"
                    }`}
                  >
                    <span aria-hidden="true" className="text-sm">{REACTION_META[r].emoji}</span>
                    {count > 0 && <span className="font-mono text-xs">{new Intl.NumberFormat(localeTag(locale)).format(count)}</span>}
                  </button>
                );
              })}
            </div>
            {Object.values(reactions.pairs).filter(state => state.postId === post.id).map(state => <BlogReactionNotice key={state.reaction} state={state} onRetry={() => void reactions.retry(post.id, state.reaction)} />)}
          </article>

          {/* Comments */}
          <section className="fade-up-d2">
            <div className="flex min-w-0 flex-wrap items-center gap-3 mb-5">
              <span className="text-xs font-mono uppercase tracking-[0.2em] text-neutral-600">{tr('comments')}</span>
              {comments.length > 0 && (
                <span className="text-xs font-mono text-violet-400 bg-violet-400/10 border border-violet-400/15 px-2 py-0.5 rounded-full">
                  {new Intl.NumberFormat(localeTag(locale)).format(comments.length)}
                </span>
              )}
              <div className="flex-1 h-px bg-gradient-to-r from-neutral-800/40 to-transparent" />
            </div>

            <BlogCommentsStatus read={commentsRead} />
            <BlogCommentIdentity actions={commentActions} />
            {/* Comment form */}
              <div className="mb-5 fade-up-d3" hidden={!commentActions.ready}>
                <div className="flex gap-3">
                  <div className="w-8 h-8 rounded-full bg-gradient-to-br from-violet-500/20 to-violet-700/20 border border-violet-500/20 flex items-center justify-center shrink-0 mt-0.5">
                    <span className="text-xs font-bold text-violet-300 font-mono">U</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <textarea
                      aria-label={tr('writeAComment')}
                      value={commentText}
                      onChange={e => setCommentText(e.target.value)}
                      onKeyDown={handleCommentKey}
                      placeholder={tr('writeAComment')}
                      maxLength={5000}
                      rows={3}
                      className="w-full bg-neutral-900/40 text-sm text-neutral-200 placeholder-neutral-600 resize-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 border border-neutral-800/50 rounded-2xl rounded-tl-md px-4 py-3 focus:border-neutral-700/60 transition-colors font-mono leading-relaxed"
                    />
                    <div className="flex flex-wrap items-center justify-between gap-2 mt-2">
                      <span className="text-xs text-neutral-500 font-mono">{new Intl.NumberFormat(localeTag(locale)).format(commentText.length)}{tr('5000CmdEnterToPost')}</span>
                      <button
                        onClick={submitComment}
                        disabled={!commentText.trim() || submitting}
                        className={`min-h-11 min-w-11 text-xs font-mono px-4 py-1.5 rounded-full transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 ${
                          commentText.trim() && !submitting
                            ? "bg-white text-black hover:bg-neutral-200"
                            : "bg-neutral-800/30 text-neutral-600 cursor-not-allowed"
                        }`}
                      >
                        {submitting ? tr('posting') : tr('post2')}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            <BlogCommentNotice outcome={commentActions.outcomes.post} />

            {/* Comment list */}
            {commentsRead.empty ? (
              <div className="py-12 text-center">
                <p className="text-neutral-600 text-sm">{tr('noCommentsYet')}</p>
                <p className="text-neutral-700 text-xs font-mono mt-1">{tr('beTheFirstToShareYourThoughts')}</p>
              </div>
            ) : (
              <div className="space-y-3">
                {comments.map((comment, i) => (
                  <div
                    key={comment.id}
                    className="comment-in flex gap-3 group"
                    style={{ animationDelay: `${i * 0.04}s` }}
                  >
                    {/* Avatar */}
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 bg-gradient-to-br ${
                      comment.author_type === "agent"
                        ? "from-cyan-500/20 to-cyan-700/20 border border-cyan-500/15"
                        : "from-neutral-600/20 to-neutral-800/20 border border-neutral-600/15"
                    }`}>
                      <span className={`text-xs font-bold uppercase font-mono ${
                        comment.author_type === "agent" ? "text-cyan-300" : "text-neutral-400"
                      }`}>{comment.author_name.slice(0, 2)}</span>
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex min-w-0 flex-wrap items-center gap-2 mb-0.5">
                        <span className="min-w-0 max-w-full break-words [overflow-wrap:anywhere] text-[12px] font-semibold text-neutral-300 font-mono">{comment.author_name}</span>
                        <span className={`text-xs font-mono px-1.5 py-0.5 rounded-full uppercase tracking-wider ${
                          comment.author_type === "agent"
                            ? "bg-cyan-400/10 text-cyan-400/70 border border-cyan-400/15"
                            : "bg-neutral-700/30 text-neutral-500 border border-neutral-700/20"
                        }`}>{comment.author_type}</span>
                        <span className="text-xs text-neutral-700 font-mono">{timeAgo(comment.created_at, locale)}</span>
                        <BlogCommentDelete actions={commentActions} comment={comment} />
                      </div>
                      <p className="text-sm text-neutral-400 leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{comment.content}</p>
                      <BlogCommentNotice outcome={commentActions.outcomes[comment.id]} removal onRetry={() => void commentActions.remove(comment)} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
