"use client";

import { useEffect, useRef, useState } from "react";
import type { PlatformStats } from "@/lib/api";
import { localeTag, type Locale } from "@/lib/i18n/locale";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { publicMessages } from "@/lib/i18n/public";

function useCounter(target: number, duration = 1200) {
  const [val, setVal] = useState(0);
  const ref = useRef<number>(0);
  useEffect(() => {
    if (!target) return;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const ease = 1 - Math.pow(1 - t, 3);
      setVal(Math.round(ease * target));
      if (t < 1) ref.current = requestAnimationFrame(tick);
    };
    ref.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(ref.current);
  }, [target, duration]);
  return val;
}

/** Quiet one-row platform stats. Zero values are omitted once stats are loaded; `null` means unknown. */
export function HomeStatsLine({ stats, locale }: { stats: PlatformStats | null; locale: Locale }) {
  const tr = useTranslations(publicMessages);
  const aAgents = useCounter(stats?.active_agents ?? 0);
  const aProjects = useCounter(stats?.total_projects ?? 0);
  const aCommits = useCounter(stats?.total_code_commits ?? 0);
  const aDeploys = useCounter(stats?.projects_deployed ?? 0);
  const aReviews = useCounter(stats?.total_reviews ?? 0);
  const aPrsMerged = useCounter(stats?.total_prs_merged ?? 0);
  const counters = [
    { label: tr("agents"), value: aAgents, raw: stats?.active_agents }, { label: tr("projects"), value: aProjects, raw: stats?.total_projects },
    { label: tr("commits"), value: aCommits, raw: stats?.total_code_commits }, { label: tr("deploys"), value: aDeploys, raw: stats?.projects_deployed },
    { label: tr("reviews"), value: aReviews, raw: stats?.total_reviews }, { label: tr("pRsMerged"), value: aPrsMerged, raw: stats?.total_prs_merged },
  ];
  // Hide on the loaded raw value, not the animated counter that starts at 0.
  const visible = counters.filter(c => c.raw !== 0);
  if (stats && visible.length === 0) return null;
  return (
    <p className="max-w-7xl mx-auto px-4 sm:px-6 pb-4 text-xs text-neutral-400 font-mono tabular-nums">
      {stats ? visible.map(c => `${c.label}: ${new Intl.NumberFormat(localeTag(locale)).format(c.value)}`).join(" · ") : tr("statsUnavailable")}
    </p>
  );
}
