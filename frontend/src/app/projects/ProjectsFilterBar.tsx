"use client";

import { useState } from "react";
import { docsMessages } from "@/lib/i18n/docs";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { displayPublicLabel, publicMessages } from "@/lib/i18n/public";
import { activeFilterCount, type ProjectFilters } from "./projectFilters";

const STATUSES = ["all", "deployed", "building", "review", "proposed", "archived"];
const TIMES = ["all", "24h", "7d", "30d"] as const;
const SORTS = ["newest", "stars", "votes"] as const;
const CATEGORIES = ["all", "productivity", "saas", "ai", "fintech", "devtools", "social", "other"];

interface SegmentedProps<T extends string> {
  cols: string; label?: string; value: T; onSelect: (value: T) => void; options: readonly { value: T; label: string }[];
}

function Segmented<T extends string>({ cols, label, value, onSelect, options }: SegmentedProps<T>) {
  return (
    <div role={label ? "group" : undefined} aria-label={label} className={`grid ${cols} overflow-hidden rounded-lg border border-neutral-800/50 text-xs type-label backdrop-blur-sm sm:flex`}>
      {options.map(o => (
        <button key={o.value} aria-pressed={value === o.value} onClick={() => onSelect(o.value)}
          className={`min-h-11 min-w-11 whitespace-nowrap px-3 py-2 transition-colors ${value === o.value ? "bg-neutral-800/60 text-white" : "text-neutral-400 hover:text-neutral-300"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Search stays visible; the filter groups collapse behind a toggle on narrow screens. */
export function ProjectsFilterBar({ filters, onChange }: { filters: ProjectFilters; onChange: (patch: Partial<ProjectFilters>) => void }) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const docsTr = useTranslations(docsMessages);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const activeFilters = activeFilterCount(filters);
  return (
    <div className="fade-up mb-8 flex flex-col gap-3" style={{ animationDelay: "160ms" }}>
      <div className="flex gap-3">
        <input
          value={filters.search}
          onChange={e => onChange({ search: e.target.value })}
          aria-label={tr('search')} placeholder={tr('search')}
          className="min-h-11 min-w-0 flex-1 bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-3.5 py-2 text-sm text-neutral-200 placeholder-neutral-400 focus:border-neutral-700/60 focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-300 font-mono backdrop-blur-sm sm:max-w-xs"
        />
        <button type="button" aria-expanded={filtersOpen} aria-controls="project-filters" onClick={() => setFiltersOpen(o => !o)}
          className="min-h-11 min-w-11 rounded-lg border border-neutral-800/50 px-3.5 text-xs type-label text-neutral-300 hover:text-white focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-300 sm:hidden">
          {tr('filtersLabel')}{activeFilters > 0 && ` (${activeFilters})`}
        </button>
      </div>
      <div id="project-filters" className={`${filtersOpen ? "flex" : "hidden"} flex-col gap-3 sm:flex sm:flex-row sm:flex-wrap sm:items-center`}>
        <Segmented cols="grid-cols-3" label={docsTr("projectStatusFilter")} value={filters.status} onSelect={status => onChange({ status })}
          options={STATUSES.map(v => ({ value: v, label: displayPublicLabel(locale, v) }))} />
        <Segmented cols="grid-cols-4" value={filters.time} onSelect={time => onChange({ time })}
          options={TIMES.map(v => ({ value: v, label: displayPublicLabel(locale, v) }))} />
        <Segmented cols="grid-cols-3" value={filters.sort} onSelect={sort => onChange({ sort })}
          options={SORTS.map(v => ({ value: v, label: displayPublicLabel(locale, v === "newest" ? "new" : v) }))} />
        <div role="group" aria-label={docsTr("projectCategoryFilter")} className="grid grid-cols-4 gap-1.5 sm:flex sm:flex-wrap">
          {CATEGORIES.map(c => (
            <button key={c} aria-pressed={filters.category === c} onClick={() => onChange({ category: c })}
              className={`min-h-11 min-w-11 px-2.5 py-1.5 rounded-lg text-xs transition-all type-label ${
                filters.category === c
                  ? "bg-white text-black font-medium"
                  : "text-neutral-400 hover:text-neutral-300 hover:bg-neutral-800/30"
              }`}>
              {displayPublicLabel(locale, c)}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
