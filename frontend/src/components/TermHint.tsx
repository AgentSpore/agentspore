"use client";

import Link from "next/link";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { docsMessages, type GlossaryTerm } from "@/lib/i18n/docs";

function useHintPosition(open: boolean) {
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const updatePosition = () => {
      const button = trigger.current;
      const hint = panel.current;
      if (!button || !hint) return;
      const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
      const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
      const anchor = button.getBoundingClientRect();
      const bounds = hint.getBoundingClientRect();
      const below = anchor.bottom + 8;
      const top = below + bounds.height <= viewportHeight - 16 ? below : anchor.top - bounds.height - 8;
      hint.style.left = `${Math.max(16, Math.min(anchor.left, viewportWidth - bounds.width - 16))}px`;
      hint.style.top = `${Math.max(16, Math.min(top, viewportHeight - bounds.height - 16))}px`;
      hint.style.visibility = "visible";
    };
    updatePosition();
    panel.current?.querySelector<HTMLAnchorElement>("a")?.focus({ preventScroll: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updatePosition);
    if (panel.current) observer?.observe(panel.current);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open]);
  return { trigger, panel };
}

/** A "?" button that opens the term definition on click, tap or Enter/Space; Escape, Close or a press outside closes it. */
export function TermHint({ term }: { term: GlossaryTerm }) {
  const tr = useTranslations(docsMessages);
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const root = useRef<HTMLSpanElement>(null);
  const { trigger, panel } = useHintPosition(open);
  const dismiss = () => { setOpen(false); trigger.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    const onPress = (e: PointerEvent) => {
      if (e.target instanceof Node && !root.current?.contains(e.target) && !panel.current?.contains(e.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPress);
    return () => document.removeEventListener("pointerdown", onPress);
  }, [open, panel]);
  return (
    <span ref={root} className="relative inline-block align-middle" onKeyDown={(e) => e.key === "Escape" && open && dismiss()}>
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${tr("hintOpen")} ${tr(`t_${term}`)}`}
        onClick={() => setOpen((v) => !v)}
        className="ml-2 inline-flex h-11 w-11 items-center justify-center rounded-full border border-neutral-600 text-xs leading-none text-neutral-300 hover:border-violet-400 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400"
      >
        ?
      </button>
      {open && createPortal(
        <span ref={panel} id={panelId} role="note" style={{ visibility: "hidden" }} className="fixed left-0 top-0 z-[100] w-64 max-w-[calc(100vw-32px)] max-h-[calc(100dvh-32px)] overflow-y-auto rounded-lg border border-neutral-700 bg-neutral-900 p-3 text-left text-xs font-normal normal-case leading-5 tracking-normal text-neutral-200 shadow-xl">
          <span className="block font-medium text-white">{tr(`t_${term}`)}</span>
          <span className="mt-1 block">{tr(`d_${term}`)}</span>
          <Link href={`/docs/glossary#${term}`} className="mt-2 inline-flex min-h-11 min-w-11 items-center text-violet-300 underline underline-offset-2">{tr("hintMore")}</Link>
          <button type="button" onClick={dismiss} className="ml-3 inline-flex min-h-11 min-w-11 items-center justify-center text-neutral-400 underline underline-offset-2">{tr("hintClose")}</button>
        </span>,
        document.body
      )}
    </span>
  );
}
