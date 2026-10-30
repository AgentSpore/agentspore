import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

type MarkdownNode = { type: string; value?: string; children?: MarkdownNode[] };

function inlineText(node: MarkdownNode): string {
  if (node.type === 'html' || node.type === 'image') return '';
  if (node.type === 'break') return ' ';
  return node.value ?? node.children?.map(inlineText).join('') ?? '';
}

function firstParagraph(node: MarkdownNode): string {
  if (['heading', 'code', 'table', 'html'].includes(node.type)) return '';
  if (node.type === 'paragraph') return inlineText(node).replace(/\s+/gu, ' ').trim();
  for (const child of node.children ?? []) {
    const text = firstParagraph(child);
    if (text) return text;
  }
  return '';
}

function proseExcerpt() {
  return (tree: MarkdownNode) => {
    const text = firstParagraph(tree);
    const characters = Array.from(text);
    const excerpt = characters.length > 280 ? characters.slice(0, 280).join('').replace(/\s+\S*$/u, '') + '…' : text;
    tree.children = excerpt ? [{ type: 'paragraph', children: [{ type: 'text', value: excerpt }] }] : [];
  };
}

/** Parse complete Markdown before selecting a bounded paragraph of plain text. */
export function BlogExcerpt({ content }: { content: string }) {
  return <div className="mt-3 text-base leading-7 text-neutral-300 empty:hidden [overflow-wrap:anywhere]">
    <ReactMarkdown remarkPlugins={[remarkGfm, proseExcerpt]}>{content}</ReactMarkdown>
  </div>;
}
