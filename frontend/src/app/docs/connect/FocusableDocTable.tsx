"use client";

import type { FocusEvent, KeyboardEvent, ReactNode } from "react";

export function FocusableDocTable({ label, children }: { label: string; children: ReactNode }) {
  function keepKeyboardFocusVisible(event: FocusEvent<HTMLDivElement>) {
    const region = event.currentTarget;
    if (event.target !== region || !region.matches(":focus-visible")) return;
    requestAnimationFrame(() => {
      if (document.activeElement !== region) return;
      const header = document.querySelector("[data-agent-header]");
      if (!header) return;
      const overlap = header.getBoundingClientRect().bottom - region.getBoundingClientRect().top;
      if (overlap > 0) window.scrollBy({ top: -overlap - 8, behavior: "instant" });
    });
  }

  function scrollHorizontally(event: KeyboardEvent<HTMLDivElement>) {
    const region = event.currentTarget;
    if (event.target !== region || document.activeElement !== region || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    if (region.scrollWidth <= region.clientWidth) return;
    const previous = region.scrollLeft;
    region.scrollBy({ left: event.key === "ArrowRight" ? 48 : -48, behavior: "instant" });
    if (region.scrollLeft !== previous) event.preventDefault();
  }

  return (
    <div data-doc-table role="region" aria-label={label} tabIndex={0} onFocus={keepKeyboardFocusVisible} onKeyDown={scrollHorizontally} className="mt-3 max-h-[max(4rem,calc(100dvh-10rem))] overflow-auto focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 sm:max-h-none">
      {children}
    </div>
  );
}
