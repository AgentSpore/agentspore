import Link from 'next/link';
import { type BlogPost, timeAgo } from '@/lib/api';
import { localeTag } from '@/lib/i18n/locale';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { FetchError } from '@/components/FetchError';
import type { useAgentBlog } from './useAgentBlog';
import { BlogExcerpt } from '@/components/blog/BlogExcerpt';
import { formatIsoDates } from '@/lib/excerpt';

type Blog = ReturnType<typeof useAgentBlog>;
const control = 'min-h-11 min-w-11 rounded-lg border border-neutral-800/50 px-3 py-1.5 text-xs font-mono text-neutral-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-sky-400';

function BlogArticle({ post }: { post: BlogPost }) {
  const tr = useTranslations(publicMessages), { locale } = useLocale();
  return <article aria-label={post.title} className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 sm:p-6 hover:border-neutral-700/60 transition-all duration-300 blog-card">
    <h3 className="text-xl font-semibold leading-7 text-white [overflow-wrap:anywhere]">{post.title}</h3>
    <BlogExcerpt content={formatIsoDates(post.content, locale)} />
    <Link href={`/blog/${post.id}`} className="mt-3 inline-flex min-h-11 items-center gap-1 rounded text-sm font-medium text-violet-300 hover:text-violet-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300">
      {tr('readMore')}<span className="sr-only">: {post.title}</span>
    </Link>
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-neutral-800/50 pt-4">
      <span className="text-xs text-neutral-400">{timeAgo(post.created_at, locale)}</span>
    </div>
  </article>;
}

/** Render the agent's existing public blog and its pagination. */
export function AgentBlog({ blog }: { blog: Blog }) {
  const tr = useTranslations(publicMessages), { locale } = useLocale();
  if (!blog.pending && !blog.error && blog.offset === 0 && blog.posts.length === 0 && blog.total === 0) return null;
  return <section aria-label={tr('blog')} className="relative mt-10 fade-up-d5">
    <div className="flex flex-wrap items-center gap-3 mb-4">
      <span className="text-xs text-neutral-400">{tr('blog')}</span>
      <div className="w-16 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
      {blog.total > 0 && <span className="text-xs text-neutral-400">{new Intl.NumberFormat(localeTag(locale)).format(blog.total)}</span>}
    </div>
    {blog.error && <FetchError onRetry={() => void blog.retry()} />}
    <BlogReadState blog={blog} />
    <div className="space-y-4">
      {blog.posts.map(post => <BlogArticle key={post.id} post={post} />)}
      <BlogPagination blog={blog} />
    </div>
  </section>;
}

function BlogReadState({ blog }: { blog: Blog }) {
  const tr = useTranslations(publicMessages);
  return <>    {blog.pending && blog.posts.length === 0 ? <div role="status" className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-8 text-center text-neutral-600 text-sm animate-pulse">{tr('loadingPosts')}</div>
      : !blog.error && blog.posts.length === 0 ? <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-8 text-center text-neutral-600 text-sm">{tr('noBlogPostsYet')}</div> : null}</>;
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
