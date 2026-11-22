"use client";

interface ProjectTabsProps<K extends string> {
  tabs: { key: K; label: string }[];
  active: K;
  onSelect: (key: K) => void;
}

/** One horizontally scrollable row of project tabs; never wraps on narrow screens. */
export function ProjectTabs<K extends string>({ tabs, active, onSelect }: ProjectTabsProps<K>) {
  return (
    <div className="flex min-w-0 max-w-full flex-nowrap gap-1 overflow-x-auto mt-8 fade-up" style={{ animationDelay: "100ms" }}>
      {tabs.map(t => (
        <button key={t.key} aria-pressed={active === t.key} onClick={() => onSelect(t.key)}
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
