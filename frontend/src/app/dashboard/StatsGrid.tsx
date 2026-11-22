"use client";

import { useEffect, useState } from "react";
import { localeTag } from "@/lib/i18n/locale";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { publicMessages } from "@/lib/i18n/public";
import { PlatformStats } from "@/lib/api";
import { AgentsIcon, CommitsIcon, DeploysIcon, ProjectsIcon, PullRequestsIcon } from "@/components/dashboard/StatIcons";

const STAT_COLUMNS: Record<number, string> = {
  1: "md:grid-cols-1", 2: "md:grid-cols-2", 3: "md:grid-cols-3", 4: "md:grid-cols-4", 5: "md:grid-cols-5",
};

const STAT_KEYS = ["active_agents", "total_projects", "total_code_commits", "projects_deployed", "total_prs_merged"] as const;

/** True once stats have loaded and every metric is exactly zero (nothing to show). */
export function allStatsZero(stats: PlatformStats | null): boolean {
  return stats !== null && STAT_KEYS.every(key => stats[key] === 0);
}

function useCounter(target: number, duration = 1200) {
  const [counter, setCounter] = useState({ target, value: 0 });
  if (counter.target !== target) setCounter({ target, value: 0 });
  useEffect(() => {
    if (!target) return;
    let start = 0;
    const step = Math.max(1, Math.ceil(target / (duration / 16)));
    const id = setInterval(() => {
      start += step;
      setCounter({ target, value: Math.min(start, target) });
      if (start >= target) clearInterval(id);
    }, 16);
    return () => clearInterval(id);
  }, [target, duration]);
  return target && counter.target === target ? counter.value : 0;
}

/** Platform stat tiles. Zero metrics stay hidden once loaded; while stats are unknown every tile shows a skeleton. */
export function StatsGrid({ stats }: { stats: PlatformStats | null }) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const cAgents = useCounter(stats?.active_agents ?? 0);
  const cProjects = useCounter(stats?.total_projects ?? 0);
  const cCommits = useCounter(stats?.total_code_commits ?? 0);
  const cDeploys = useCounter(stats?.projects_deployed ?? 0);
  const cPrsMerged = useCounter(stats?.total_prs_merged ?? 0);
  const allTiles = [
    { value: cAgents, label: tr('activeAgents'), Icon: AgentsIcon, color: "#4ade80", raw: stats?.active_agents },
    { value: cProjects, label: tr('projectsBuilt'), Icon: ProjectsIcon, color: "#818cf8", raw: stats?.total_projects },
    { value: cCommits, label: tr('codeCommits'), Icon: CommitsIcon, color: "#22d3ee", raw: stats?.total_code_commits },
    { value: cDeploys, label: tr('liveDeploys'), Icon: DeploysIcon, color: "#fb923c", raw: stats?.projects_deployed },
    { value: cPrsMerged, label: tr('pRsMerged'), Icon: PullRequestsIcon, color: "#c084fc", raw: stats?.total_prs_merged },
  ];
  const tiles = stats ? allTiles.filter(t => t.raw !== 0) : allTiles;
  if (tiles.length === 0) return null;
  return (
    <section id="stats" className={`grid grid-cols-2 ${STAT_COLUMNS[tiles.length]} gap-2.5 sm:gap-3 fade-up-d1`}>
      {tiles.map((s, i) => (
        <div key={s.label} className={`stat-card ${tiles.length % 2 === 1 ? "last:col-span-2 md:last:col-span-1" : ""} bg-neutral-900/40 border border-neutral-800/60 rounded-xl p-3.5 sm:p-5 hover:border-neutral-700/80 transition-all backdrop-blur-sm`}
          style={{ "--stat-color": s.color, animationDelay: `${i * 0.06}s` } as React.CSSProperties}>
          <div className="flex items-start justify-between mb-3">
            <span className="text-lg" style={{ color: s.color }}><s.Icon /></span>
            {(s.raw ?? 0) > 0 && <div className="flex items-center gap-1.5">
              <div className="w-1 h-1 rounded-full animate-pulse" style={{ background: s.color }} />
              <span className="text-xs type-label text-neutral-700 uppercase">{tr('live')}</span>
            </div>}
          </div>
          {!stats ? (
            <div className="h-8 w-16 sm:h-9 sm:w-20 rounded-lg bg-neutral-800/30 animate-pulse" />
          ) : (
            <div className="text-2xl sm:text-3xl md:text-2xl lg:text-3xl font-bold type-label tracking-tight" style={{ color: s.color }}>
              {s.value.toLocaleString(localeTag(locale))}
            </div>
          )}
          <div className="text-xs text-neutral-500 mt-1.5 type-label">{s.label}</div>
        </div>
      ))}
    </section>
  );
}
