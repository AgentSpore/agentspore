import type { ReactNode } from "react";

const MARK_PATHS: Record<string, string> = {
  podmemory: "M5 4h10l4 4v12H5z M14 4v5h5 M8 12h8 M8 16h5",
  otkrytka: "M3 6h18v14H3z M3 7l9 7 9-7 M9 3l3 3 3-3",
  pereklichka: "M9 4h6v10a3 3 0 0 1-6 0z M5 11v3a7 7 0 0 0 14 0v-3 M12 21v-3",
  saascalc: "M4 4h16v16H4z M4 9h16 M9 9v11 M13 13h3 M13 16h3",
};

export function ServiceMark({ id, className = "h-6 w-6" }: { id: string; className?: string }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className}><path d={MARK_PATHS[id]} /></svg>;
}

/** Fixed-ratio preview box: the fallback glyph fills it today, a screenshot can replace it without moving the card. */
export function ServiceThumb({ children }: { children: ReactNode }) {
  return (
    <div data-thumb aria-hidden="true" className="flex aspect-[5/4] w-20 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-neutral-700 bg-neutral-950 text-violet-200">
      {children}
    </div>
  );
}
