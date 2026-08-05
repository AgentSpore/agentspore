"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Contender answers and judge reasoning arrive as model-authored Markdown.
// react-markdown + remark-gfm is what the rest of the app already renders
// model text with (frontend/src/app/councils/[id]/page.tsx, blog). No
// rehype-raw here — without it react-markdown drops raw HTML nodes instead of
// injecting them, which is the property that makes untrusted model output safe
// to render at all. Do not add rehype-raw to this component.
const REMARK_PLUGINS = [remarkGfm];

// Every element carries its own classes because the battle surface has no
// prose stylesheet and globals.css is outside this feature's scope. Sizes are
// relative (em) so one map serves both the 14px answer body and the 13px
// judge-reasoning body.
const COMPONENTS: Components = {
  h1: ({ children }) => <h3 className="mt-4 mb-1.5 text-[1.15em] font-semibold text-neutral-100 first:mt-0">{children}</h3>,
  h2: ({ children }) => <h4 className="mt-4 mb-1.5 text-[1.08em] font-semibold text-neutral-100 first:mt-0">{children}</h4>,
  h3: ({ children }) => <h5 className="mt-3.5 mb-1 font-semibold text-neutral-100 first:mt-0">{children}</h5>,
  h4: ({ children }) => <h6 className="mt-3 mb-1 font-semibold text-neutral-200 first:mt-0">{children}</h6>,
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 first:mt-0 last:mb-0 marker:text-neutral-600">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5 first:mt-0 last:mb-0 marker:text-neutral-600">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-neutral-50">{children}</strong>,
  em: ({ children }) => <em className="italic text-neutral-100">{children}</em>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-cyan-300 underline underline-offset-2 hover:text-cyan-200">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2.5 border-l-2 border-neutral-700 pl-3 text-neutral-400">{children}</blockquote>
  ),
  hr: () => <hr className="my-3.5 border-neutral-800" />,
  code: ({ children }) => (
    <code className="rounded bg-neutral-800/70 px-1 py-0.5 font-mono text-[0.92em] text-neutral-100">{children}</code>
  ),
  // The inline-code classes above are reset inside a fenced block, so one
  // `code` override serves both without inspecting the node.
  pre: ({ children }) => (
    <pre className="my-2.5 overflow-x-auto rounded-lg border border-neutral-800 bg-neutral-950/70 p-3 font-mono text-[0.92em] leading-[1.6] text-neutral-200 [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-[1em] [&_code]:text-inherit">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-2.5 overflow-x-auto rounded-lg border border-neutral-800">
      <table className="w-full border-collapse text-[0.95em]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-neutral-800 px-2.5 py-1.5 text-left font-semibold text-neutral-200">{children}</th>
  ),
  td: ({ children }) => <td className="border-b border-neutral-800/60 px-2.5 py-1.5 align-top">{children}</td>,
};

/**
 * Renders model-authored Markdown on the battle surface.
 *
 * Collapsed to `collapsedMaxHeight` with a fade-out mask and an expander when
 * — and only when — the content actually overflows it: a fixed scrolling
 * porthole hides how much is left and traps a wheel gesture inside a card,
 * while expanding in place keeps the two answers comparable side by side and
 * leaves the section stackable (a later step-trace block just appends).
 */
export function BattleMarkdown({
  content,
  className = "",
  collapsedMaxHeight,
  expandLabel = "Read the full text",
}: {
  content: string;
  className?: string;
  /** Tailwind max-height class applied while collapsed, e.g. `max-h-[26rem]`. */
  collapsedMaxHeight?: string;
  expandLabel?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el || !collapsedMaxHeight || expanded) return;
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight + 4);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [content, collapsedMaxHeight, expanded]);

  const collapsed = Boolean(collapsedMaxHeight) && !expanded;
  const clip = collapsed
    ? `${collapsedMaxHeight} overflow-hidden ${overflows ? "[mask-image:linear-gradient(to_bottom,black_calc(100%-3.5rem),transparent)]" : ""}`
    : "";

  return (
    <div className="min-w-0">
      <div ref={bodyRef} className={`min-w-0 break-words ${className} ${clip}`}>
        <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
          {content}
        </ReactMarkdown>
      </div>
      {overflows && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="battle-press mt-1 flex min-h-11 items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider text-neutral-500 transition-colors hover:text-neutral-300"
        >
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" className="battle-chevron shrink-0" style={{ transform: expanded ? "rotate(-90deg)" : "rotate(90deg)" }}>
            <path d="M4 2l4 4-4 4" />
          </svg>
          {expanded ? "Show less" : expandLabel}
        </button>
      )}
    </div>
  );
}
