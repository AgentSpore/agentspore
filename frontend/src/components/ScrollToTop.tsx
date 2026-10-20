"use client";
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { sharedMessages } from '@/lib/i18n/shared';
import { useEffect, useRef, useState, type RefObject, type MouseEvent } from "react";
const SCROLL_TO_TOP_STYLES = `
        @keyframes stt-fade-in {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes stt-fade-out {
          from { opacity: 1; transform: translateY(0); }
          to   { opacity: 0; transform: translateY(12px); }
        }
        .stt-visible {
          animation: stt-fade-in 0.2s ease forwards;
          pointer-events: auto;
        }
        .stt-hidden {
          animation: stt-fade-out 0.2s ease forwards;
          pointer-events: none;
        }
      `;
function focusOverlapsAnchor(anchor: HTMLElement, target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  const focused = target.closest(":is(a[href],button,input,select,textarea,[tabindex],[contenteditable=true]):not(body):not(html)");
  if (!focused || anchor.contains(focused)) return false;
  const control = anchor.getBoundingClientRect();
  const content = focused.getBoundingClientRect();
  const style = getComputedStyle(focused);
  // Cover the 12px entry translation and 105% hover scale outside the stable anchor.
  const clearance = 14 + (parseFloat(style.outlineWidth) || 0) + Math.max(0, parseFloat(style.outlineOffset) || 0);
  return content.left - clearance < control.right && content.right + clearance > control.left &&
    content.top - clearance < control.bottom && content.bottom + clearance > control.top;
}

function useScrollControlVisibility(anchor: RefObject<HTMLDivElement | null>) {
  const [scrolled, setScrolled] = useState(false);
  const [occluded, setOccluded] = useState(false);
  useEffect(() => {
    const update = (target: EventTarget | null = document.activeElement) => {
      setScrolled(window.scrollY > 400);
      setOccluded(anchor.current ? focusOverlapsAnchor(anchor.current, target) : false);
    };
    const onChange = () => update();
    const onFocusOut = (event: FocusEvent) => update(event.relatedTarget);
    window.addEventListener("scroll", onChange, { passive: true });
    window.addEventListener("resize", onChange);
    window.addEventListener("focusin", onChange);
    window.addEventListener("focusout", onFocusOut);
    update();
    return () => {
      window.removeEventListener("scroll", onChange);
      window.removeEventListener("resize", onChange);
      window.removeEventListener("focusin", onChange);
      window.removeEventListener("focusout", onFocusOut);
    };
  }, [anchor]);
  return { visible: scrolled && !occluded, occluded };
}

/** Floating scroll control with accessible visibility and reduced-motion behavior. */
export default function ScrollToTop() {
  const tr = useTranslations(sharedMessages);
  const anchor = useRef<HTMLDivElement>(null);
  const { visible, occluded } = useScrollControlVisibility(anchor);
  const scrollToTop = (event: MouseEvent<HTMLButtonElement>) => {
    event.currentTarget.blur();
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  };
  return (
    <>
      <style>{SCROLL_TO_TOP_STYLES}</style>
      <div ref={anchor}
        className="fixed bottom-20 right-4 sm:bottom-6 sm:right-6 z-50 w-11 h-11 pointer-events-none"
        style={{ visibility: occluded ? "hidden" : undefined }}>
      <button
        onClick={scrollToTop}
        aria-label={tr('scrollToTop')}
        aria-hidden={!visible}
        tabIndex={visible ? 0 : -1}
        disabled={!visible}
        className={[
          "flex items-center justify-center",
          "w-11 h-11 rounded-full",
          "bg-neutral-900/80 border border-neutral-800 backdrop-blur-sm",
          "text-violet-400",
          "transition-[color,background-color,border-color,opacity,transform,scale] duration-200 ease-in-out",
          "hover:bg-neutral-800 hover:border-violet-500/30 hover:scale-105",
          visible ? "stt-visible" : "stt-hidden",
        ].join(" ")}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <polyline points="18 15 12 9 6 15" />
        </svg>
      </button>
      </div>
    </>
  );
}
