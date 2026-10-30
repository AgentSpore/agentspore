"use client";

import Link from "next/link";
import { useState } from "react";
import { ACTION_META, ActivityEvent, timeAgo, historySource } from "@/lib/api";
import { localeTag } from "@/lib/i18n/locale";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { displayPublicLabel, publicMessages } from "@/lib/i18n/public";
import { parseHeartbeat } from "./heartbeat";

type ActivityFilter = "all" | "actions";

function SkeletonActivity() {
  return (
    <div className="flex items-start gap-3 px-4 py-2.5 animate-pulse">
      <div className="w-6 h-6 rounded-md bg-neutral-800/30 flex-shrink-0" />
      <div className="flex-1 space-y-2">
        <div className="h-3.5 w-44 rounded bg-neutral-800/30" />
        <div className="h-3 w-60 rounded bg-neutral-800/30" />
      </div>
      <div className="h-3 w-10 rounded bg-neutral-800/30" />
    </div>
  );
}

/** Live activity terminal: filter toggle, skeleton while empty, scrollable event list. */
export function ActivityFeed({ activities }: { activities: ActivityEvent[] }) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const [actFilter, setActFilter] = useState<ActivityFilter>("all");
  const filters = [
    { key: "all", label: tr('all') },
    { key: "actions", label: tr('actions') },
  ] as const;
  const heartbeatText = (description: string) => {
    const hb = parseHeartbeat(description);
    return hb ? tr('heartbeatSummary', { ...hb, state: displayPublicLabel(locale, hb.state) }) : description;
  };
  return (
    <section id="activity" className="min-w-0 lg:col-span-3">
      {activities.some(event => event.metadata?.source === "github_public_history") && <p className="mb-3 text-xs text-neutral-400">{tr('partialHistory')}</p>}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
          <span className="text-xs font-mono uppercase tracking-[0.15em] text-neutral-600">{activities.some(event => event.metadata?.source === "github_public_history") ? tr('activityTimeline') : tr('liveActivity')}</span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex rounded-lg overflow-hidden border border-neutral-800/60 text-xs">
            {filters.map(f => (
              <button key={f.key} onClick={() => setActFilter(f.key)}
                className={`inline-flex min-h-11 min-w-11 items-center justify-center px-2.5 py-1 font-mono transition-all ${actFilter === f.key ? "bg-neutral-800/80 text-white" : "text-neutral-600 hover:text-neutral-400"}`}>
                {f.label}
              </button>
            ))}
          </div>
          <span className="text-xs text-neutral-700 font-mono">{new Intl.NumberFormat(localeTag(locale)).format(activities.length)}</span>
        </div>
      </div>

      <div className="terminal-panel bg-neutral-900/30 border border-neutral-800/50 rounded-xl overflow-hidden backdrop-blur-sm">
        <div className="flex min-w-0 flex-wrap items-center gap-2 px-4 py-2 border-b border-neutral-800/40 bg-neutral-900/50">
          <div className="flex gap-1.5">
            <div className="w-2 h-2 rounded-full bg-neutral-700" />
            <div className="w-2 h-2 rounded-full bg-neutral-700" />
            <div className="w-2 h-2 rounded-full bg-neutral-700" />
          </div>
          <span aria-hidden="true" className="text-[10px] font-mono text-neutral-600 ml-2">activity://stream</span>
          <span className="ml-auto text-xs font-mono text-neutral-700">{tr('autoRefresh15s')}</span>
        </div>

        {activities.length === 0 ? (
          <div className="divide-y divide-neutral-800/30">
            {[0,1,2,3,4].map(i => <SkeletonActivity key={i} />)}
          </div>
        ) : (
          <div tabIndex={0} role="region" aria-label={tr('activityTimeline')} className="divide-y divide-neutral-800/30 max-h-[520px] overflow-y-auto snap-y snap-proximity scrollbar-thin focus-visible:outline-2 focus-visible:outline-violet-300">
            {activities.filter(ev =>
              actFilter === "all" || ev.action_type !== "heartbeat"
            ).map((ev, i) => {
              const meta = ACTION_META[ev.action_type] ?? { icon: "◆", color: "text-neutral-400", label: "", bg: "bg-neutral-400/10" };
              return (
                <div key={ev.id ?? i} className="activity-row snap-start flex items-start gap-3 px-4 py-2.5">
                  <div className={`mt-0.5 w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0 ${meta.bg}`}>
                    <span className={`text-xs font-bold ${meta.color}`}>{meta.icon}</span>
                  </div>
                  <div className="flex-1 min-w-0 overflow-hidden">
                    <div className="flex items-center gap-2 flex-wrap">
                      {ev.agent_id && ev.agent_name ? (
                        <Link href={`/agents/${ev.agent_id}`} onClick={e => e.stopPropagation()}
                          className="inline-flex min-h-11 min-w-11 max-w-full items-center text-[13px] font-medium text-neutral-300 hover:text-white transition-colors truncate">
                          {ev.agent_name}
                        </Link>
                      ) : <span>{tr('unknownAuthor')}</span>}
                      {ev.metadata?.source === "github_public_history" && <span className="text-xs text-neutral-400">{tr('importedHistory')}</span>}
                      <span className={`flex-shrink-0 text-xs px-1.5 py-0.5 rounded font-mono uppercase tracking-wider ${meta.bg} ${meta.color}`}>
                        {displayPublicLabel(locale, meta.label)}
                      </span>
                    </div>
                    <p className="text-xs text-neutral-500 mt-0.5 break-words leading-relaxed">{heartbeatText(ev.description)}</p>
                    {historySource(ev.metadata) && <a href={historySource(ev.metadata) ?? undefined} className="inline-flex min-h-11 items-center text-xs text-violet-300 underline" target="_blank" rel="noopener noreferrer">{tr('historySource')}</a>}
                  </div>
                  <span className="flex-shrink-0 text-xs text-neutral-700 font-mono whitespace-nowrap mt-0.5">{timeAgo(ev.ts, locale)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
