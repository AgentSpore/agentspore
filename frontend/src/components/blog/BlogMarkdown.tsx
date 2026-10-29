'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { blogMessages } from '@/lib/i18n/blog';

type MarkdownNode = { type: string; depth?: number; value?: string; children?: MarkdownNode[] };
function headingText(node: MarkdownNode): string {
  return node.value ?? node.children?.map(headingText).join('') ?? '';
}
function removeDuplicateTitle({ title }: { title: string }) {
  return (tree: MarkdownNode) => {
    const first = tree.children?.[0];
    if (first?.type === 'heading' && first.depth === 1 && headingText(first).trim() === title.trim()) tree.children?.shift();
  };
}

/** Render an intact article with readable prose and contained code/table scrolling. */
export function BlogMarkdown({ content, title }: { content: string; title: string }) {
  const t = useTranslations(blogMessages);
  return <div className="min-w-0 text-base leading-8 text-neutral-300 [overflow-wrap:anywhere] [&_p]:my-5 [&_p]:whitespace-pre-line [&_strong]:font-semibold [&_strong]:text-neutral-100 [&_a]:text-violet-300 [&_a]:underline [&_a]:decoration-violet-400/40 [&_a]:underline-offset-4 [&_a]:focus-visible:outline-2 [&_a]:focus-visible:outline-offset-4 [&_a]:focus-visible:outline-violet-300 [&_h2]:mt-12 [&_h2]:mb-4 [&_h2]:text-2xl [&_h2]:font-semibold [&_h2]:tracking-tight [&_h2]:text-white [&_h3]:mt-8 [&_h3]:mb-3 [&_h3]:text-xl [&_h3]:font-semibold [&_h3]:text-neutral-100 [&_h4]:mt-6 [&_h4]:font-semibold [&_ul]:my-5 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:my-5 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:my-2 [&_li]:pl-1 [&_li]:marker:text-neutral-500 [&_blockquote]:my-7 [&_blockquote]:border-l-2 [&_blockquote]:border-violet-400 [&_blockquote]:pl-5 [&_blockquote]:text-neutral-400 [&_hr]:my-10 [&_hr]:border-neutral-800 [&_img]:my-8 [&_img]:max-w-full [&_img]:rounded-xl [&_code]:rounded [&_code]:bg-neutral-900 [&_code]:px-1 [&_code]:font-mono [&_code]:text-sm [&_code]:text-violet-200 [&_pre]:my-7 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-neutral-800 [&_pre]:bg-neutral-900 [&_pre]:p-5 [&_pre]:whitespace-pre [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-neutral-200">
    <ReactMarkdown remarkPlugins={[remarkGfm, [removeDuplicateTitle, { title }]]} rehypePlugins={[rehypeHighlight]} components={{
      h1: ({ children }) => <h2>{children}</h2>,
      table: ({ children }) => <div role="region" aria-label={t('table')} tabIndex={0} className="my-7 max-w-full overflow-x-auto rounded-xl border border-neutral-800 focus-visible:outline-2 focus-visible:outline-violet-300"><table className="w-full min-w-lg border-collapse text-left text-sm">{children}</table></div>,
      th: ({ children, style }) => <th style={style} className="border-b border-neutral-700 bg-neutral-900 px-4 py-3 font-semibold text-neutral-100">{children}</th>,
      td: ({ children, style }) => <td style={style} className="border-b border-neutral-800 px-4 py-3 align-top">{children}</td>,
    }}>{content}</ReactMarkdown>
  </div>;
}
