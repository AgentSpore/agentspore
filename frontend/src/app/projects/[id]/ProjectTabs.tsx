"use client";

import { useRef, type KeyboardEvent } from "react";

interface ProjectTabsProps<K extends string> {
  tabs: { key: K; label: string }[];
  active: K;
  onSelect: (key: K) => void;
  label: string;
}

export const projectTabId = (key: string) => `project-tab-${key}`;
export const projectPanelId = (key: string) => `project-panel-${key}`;

/** Target index for a roving-focus key, wrapping at both ends; null for keys the pattern ignores. */
function targetIndex(key: string, from: number, count: number): number | null {
  if (key === "ArrowRight") return (from + 1) % count;
  if (key === "ArrowLeft") return (from - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

/** APG tabs (automatic activation) in one horizontally scrollable row; never wraps on narrow screens. */
export function ProjectTabs<K extends string>({ tabs, active, onSelect, label }: ProjectTabsProps<K>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const next = targetIndex(event.key, index, tabs.length);
    const target = next === null ? undefined : tabs[next];
    if (next === null || !target) return;
    event.preventDefault();
    refs.current[next]?.focus();
    onSelect(target.key);
  };

  return (
    <div role="tablist" aria-label={label} className="flex min-w-0 max-w-full flex-nowrap gap-1 overflow-x-auto scroll-px-2 pr-2 mt-8 fade-up" style={{ animationDelay: "100ms" }}>
      {tabs.map((t, i) => (
        <button key={t.key} ref={el => { refs.current[i] = el; }} type="button" role="tab" id={projectTabId(t.key)}
          aria-selected={active === t.key} aria-controls={projectPanelId(t.key)} tabIndex={active === t.key ? 0 : -1}
          onClick={() => onSelect(t.key)} onKeyDown={e => onKeyDown(e, i)}
          className={`shrink-0 px-3.5 py-1.5 text-xs font-mono whitespace-nowrap rounded-lg transition-all min-h-12 min-w-12 focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400 ${
            active === t.key
              ? "bg-white text-black font-medium"
              : "text-neutral-600 hover:text-neutral-300 hover:bg-neutral-800/30"
          }`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}
