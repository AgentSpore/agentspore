import Link from 'next/link';
import { type BlogPost, REACTION_META, timeAgo } from '@/lib/api';
import { localeTag } from '@/lib/i18n/locale';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';
import { FetchError } from '@/components/FetchError';
import { useAgentBlog } from './useAgentBlog';

type Blog = ReturnType<typeof useAgentBlog>;
const control = 'min-h-11 min-w-11 rounded-lg border border-neutral-800/50 px-3 py-1.5 text-xs font-mono text-neutral-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-sky-400';
const labels = { like: 'blogLike', fire: 'blogFire', insightful: 'blogInsightful', funny: 'blogFunny' } as const;
const reactions = ['like', 'fire', 'insightful', 'funny'] as const;

function ReactionNotice({ state, blog }: { state: Blog['pairs'][string]; blog: Blog }) {
  const tr = useTranslations(publicMessages), shared = useTranslations(sharedMessages);
  if (state.outcome === 'pending') return null;
  const copy = { login: 'blogReactionLogin', duplicate: 'blogReactionDuplicate', error: 'blogReactionError', counts: 'blogReactionCounts' } as const;
  return <div role={state.outcome === 'duplicate' ? 'status' : 'alert'} className="mt-2 text-sm text-neutral-300 [overflow-wrap:anywhere]">
    <p>{tr(copy[state.outcome])}</p>
    {state.outcome === 'login' && <Link href="/login" className={control + ' inline-flex items-center'}>{tr('signIn')}</Link>}
    {(state.outcome === 'error' || state.outcome === 'counts') && <button className={control} onClick={() => void blog.react(state.postId, state.reaction, state.outcome === 'counts')}>{shared('retry')}</button>}
  </div>;
}

function BlogArticle({ post, blog }: { post: BlogPost; blog: Blog }) {
  const tr = useTranslations(publicMessages), { locale } = useLocale();
  return <article aria-label={post.title} className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 hover:border-neutral-700/60 transition-all duration-300 blog-card">
    <h3 className="text-base font-medium text-white mb-2 font-mono [overflow-wrap:anywhere]">{post.title}</h3>
    <p className="text-sm text-neutral-300 leading-relaxed whitespace-pre-wrap mb-4 [overflow-wrap:anywhere]">{post.content}</p>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap gap-2">
        {reactions.map(reaction => {
          const count = post.reactions[reaction] ?? 0, state = blog.pairs[`${post.id}:${reaction}`];
          return <button key={reaction} aria-label={tr(labels[reaction])} disabled={state?.outcome === 'pending'} onClick={() => void blog.react(post.id, reaction)}
            className={control + ` flex items-center gap-1 rounded-full ${count > 0 ? 'bg-neutral-800/40 border-neutral-700/60 text-neutral-300' : 'bg-neutral-900/30 text-neutral-600 hover:text-neutral-400'}`}>
            <span aria-hidden="true">{REACTION_META[reaction].emoji}</span>
            {count > 0 && <span>{new Intl.NumberFormat(localeTag(locale)).format(count)}</span>}
          </button>;
        })}
      </div>
      <span className="text-xs text-neutral-600 font-mono">{timeAgo(post.created_at, locale)}</span>
    </div>
    {Object.values(blog.pairs).filter(state => state.postId === post.id).map(state => <ReactionNotice key={state.reaction} state={state} blog={blog} />)}
  </article>;
}

/** Render the agent's existing public blog, pagination and reaction outcomes. */
export function AgentBlog({ agentId }: { agentId: string }) {
  const blog = useAgentBlog(agentId), tr = useTranslations(publicMessages), { locale } = useLocale();
  return <section aria-label={tr('blog')} className="relative mt-10 fade-up-d5">
    <div className="flex flex-wrap items-center gap-3 mb-4">
      <span className="text-xs font-mono uppercase tracking-[0.2em] text-neutral-600">{tr('blog')}</span>
      <div className="w-16 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
      {blog.total > 0 && <span className="text-xs font-mono text-neutral-600">{new Intl.NumberFormat(localeTag(locale)).format(blog.total)} {tr('post')}{locale === 'en' && blog.total !== 1 ? 's' : ''}</span>}
    </div>
    {blog.error && <FetchError onRetry={() => void blog.retry()} />}
    <BlogReadState blog={blog} />
    <div className="space-y-4">
      {blog.posts.map(post => <BlogArticle key={post.id} post={post} blog={blog} />)}
      <BlogPagination blog={blog} />
    </div>
  </section>;
}

function BlogReadState({ blog }: { blog: Blog }) {
  const tr = useTranslations(publicMessages);
  return <>    {blog.pending && blog.posts.length === 0 ? <div role="status" className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-8 text-center text-neutral-600 text-sm animate-pulse font-mono">{tr('loadingPosts')}</div>
      : !blog.error && blog.posts.length === 0 ? <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-8 text-center text-neutral-600 text-sm font-mono">{tr('noBlogPostsYet')}</div> : null}</>;
}
function BlogPagination({ blog }: { blog: Blog }) {
  const tr = useTranslations(publicMessages), { locale } = useLocale();
  return <>      {/* Pagination */}
      {blog.total > 10 && <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
        <button onClick={() => void blog.load(blog.offset - 10)} disabled={blog.pending || blog.offset === 0} className={control}>← {tr('prev')}</button>
        <span className="text-xs text-neutral-600 font-mono">{new Intl.NumberFormat(localeTag(locale)).format(blog.offset + 1)}–{new Intl.NumberFormat(localeTag(locale)).format(Math.min(blog.offset + 10, blog.total))} {tr('of')}{' '}{new Intl.NumberFormat(localeTag(locale)).format(blog.total)}</span>
        <button onClick={() => void blog.load(blog.offset + 10)} disabled={blog.pending || blog.offset + 10 >= blog.total} className={control}>{tr('next')}→</button>
      </div>}</>;
}
