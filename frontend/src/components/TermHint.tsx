"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { docsMessages, type GlossaryTerm } from "@/lib/i18n/docs";

/** A "?" button that opens the term definition on click, tap or Enter/Space; Escape closes it. */
export function TermHint({ term }: { term: GlossaryTerm }) {
  const tr = useTranslations(docsMessages);
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <span className="relative inline-block align-middle" onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${tr("hintOpen")} ${tr(`t_${term}`)}`}
        onClick={() => setOpen((v) => !v)}
        className="ml-1 inline-flex h-5 w-5 items-center justify-center rounded-full border border-neutral-600 text-[11px] leading-none text-neutral-300 hover:border-violet-400 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400"
      >
        ?
      </button>
      {open && (
        <span id={panelId} role="note" className="absolute left-0 top-6 z-30 w-64 max-w-[80vw] rounded-lg border border-neutral-700 bg-neutral-900 p-3 text-left text-xs font-normal normal-case leading-5 tracking-normal text-neutral-200 shadow-xl">
          <span className="block font-medium text-white">{tr(`t_${term}`)}</span>
          <span className="mt-1 block">{tr(`d_${term}`)}</span>
          <Link href={`/docs/glossary#${term}`} className="mt-2 inline-block text-violet-300 underline underline-offset-2">{tr("hintMore")}</Link>
          <button type="button" onClick={() => setOpen(false)} className="ml-3 text-neutral-400 underline underline-offset-2">{tr("hintClose")}</button>
        </span>
      )}
    </span>
  );
}
