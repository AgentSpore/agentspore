"use client";

import { useState } from "react";
import { docsMessages } from "@/lib/i18n/docs";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { displayPublicLabel, publicMessages } from "@/lib/i18n/public";
import { activeFilterCount, type ProjectFilters } from "./projectFilters";

const CATEGORIES = ["all", "productivity", "saas", "ai", "fintech", "devtools", "social", "other"];

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
          className="min-h-11 min-w-11 rounded-lg border border-neutral-800/50 px-3.5 text-xs font-mono text-neutral-300 hover:text-white focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-300 sm:hidden">
          {tr('filtersLabel')}{activeFilters > 0 && ` (${activeFilters})`}
        </button>
      </div>
      <div id="project-filters" className={`${filtersOpen ? "flex" : "hidden"} flex-col gap-3 sm:flex sm:flex-row sm:flex-wrap sm:items-center`}>
        <div role="group" aria-label={docsTr("projectStatusFilter")} className="grid grid-cols-3 overflow-hidden rounded-lg border border-neutral-800/50 text-xs font-mono backdrop-blur-sm sm:flex">
          {["all", "deployed", "building", "review", "proposed", "archived"].map(s => (
            <button key={s} aria-pressed={filters.status === s} onClick={() => onChange({ status: s })}
              className={`min-h-11 min-w-11 whitespace-nowrap px-3 py-2 transition-colors ${filters.status === s ? "bg-neutral-800/60 text-white" : "text-neutral-400 hover:text-neutral-300"}`}>
              {displayPublicLabel(locale, s)}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-4 overflow-hidden rounded-lg border border-neutral-800/50 text-xs font-mono backdrop-blur-sm sm:flex">
          {(["all", "24h", "7d", "30d"] as const).map(t => (
            <button key={t} aria-pressed={filters.time === t} onClick={() => onChange({ time: t })}
              className={`min-h-11 min-w-11 px-3 py-2 transition-colors ${filters.time === t ? "bg-neutral-800/60 text-white" : "text-neutral-400 hover:text-neutral-300"}`}>
              {displayPublicLabel(locale, t)}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-3 overflow-hidden rounded-lg border border-neutral-800/50 text-xs font-mono backdrop-blur-sm sm:flex">
          {(["newest", "stars", "votes"] as const).map(s => (
            <button key={s} aria-pressed={filters.sort === s} onClick={() => onChange({ sort: s })}
              className={`min-h-11 min-w-11 px-3 py-2 transition-colors ${filters.sort === s ? "bg-neutral-800/60 text-white" : "text-neutral-400 hover:text-neutral-300"}`}>
              {displayPublicLabel(locale, s === "stars" ? "stars" : s === "votes" ? "votes" : "new")}
            </button>
          ))}
        </div>
        <div role="group" aria-label={docsTr("projectCategoryFilter")} className="grid grid-cols-4 gap-1.5 sm:flex sm:flex-wrap">
          {CATEGORIES.map(c => (
            <button key={c} aria-pressed={filters.category === c} onClick={() => onChange({ category: c })}
              className={`min-h-11 min-w-11 px-2.5 py-1.5 rounded-lg text-xs transition-all font-mono ${
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
