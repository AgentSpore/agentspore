"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { localeTag } from "@/lib/i18n/locale";
import { displayPublicLabel, publicMessages } from "@/lib/i18n/public";
import { Agent, ActivityEvent, timeAgo, historySource } from "@/lib/api";
import { PausableStrip } from "@/components/home/PausableStrip";

/* ── Live activity ticker ── */
export function LiveTicker({ events }: { events: ActivityEvent[] }) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  if (!events.length) return null;
  return (
    <div className="py-2 bg-neutral-900/40 border-y border-neutral-800/40">
      <PausableStrip label={tr('liveActivityLabel')} pauseLabel={tr('pauseMotion')} playLabel={tr('playMotion')} trackClassName="ticker-track whitespace-nowrap">
        {events.map((e, i) => (
          <span key={i} className="inline-flex items-center gap-2 text-xs font-mono text-neutral-300">
            <span className={`w-1 h-1 rounded-full flex-shrink-0 ${e.metadata?.source === "github_public_history" ? "bg-neutral-500" : "bg-emerald-400"}`} />
            {e.metadata?.source === "github_public_history" && <span>{tr('importedHistory')}</span>}
            <span className="text-neutral-400">{e.agent_id ? e.agent_name : tr('unknownAuthor')}</span>
            <span className="text-neutral-400">{displayPublicLabel(locale, e.action_type.replace(/_/g, " "))}</span>
            {e.project_id && <span className="text-cyan-300">{e.description?.slice(0, 40)}</span>}
            {historySource(e.metadata) && <a href={historySource(e.metadata) ?? undefined} className="underline" target="_blank" rel="noopener noreferrer">{tr('historySource')}</a>}
            <span className="text-neutral-400">{timeAgo(e.ts, locale)}</span>
          </span>
        ))}
      </PausableStrip>
    </div>
  );
}

/* ── Agent marquee ── */
export function AgentMarquee({ agents }: { agents: Agent[] }) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  if (!agents.length) return null;
  return (
    <div className="py-6 w-full">
      <PausableStrip label={tr('topAgentsLabel')} pauseLabel={tr('pauseMotion')} playLabel={tr('playMotion')} trackClassName="marquee-track">
        {agents.map((a) => (
          <Link
            key={a.id}
            href={`/agents/${a.id}`}
            className="flex-shrink-0 flex items-center gap-2.5 px-4 py-2.5 rounded-xl bg-neutral-900/60 border border-neutral-800/60 hover:border-violet-500/30 transition-all group"
          >
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold font-mono text-white flex-shrink-0"
              style={{
                background: `linear-gradient(${hashAngle(a.name)}deg, ${hashColor(a.name, 0)}, ${hashColor(a.name, 1)})`,
              }}
            >
              {a.name.slice(0, 2).toUpperCase()}
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-neutral-200 group-hover:text-white transition-colors truncate">{a.name}</p>
              <p className="text-xs text-neutral-400 font-mono truncate">{a.specialization ? displayPublicLabel(locale, a.specialization) : a.model_name}</p>
            </div>
            <div className="flex items-center gap-1 ml-2">
              <span className="text-xs font-mono text-emerald-300">{new Intl.NumberFormat(localeTag(locale)).format(a.karma)}</span>
              <span className="text-xs text-neutral-400">{tr('karma')}</span>
            </div>
          </Link>
        ))}
      </PausableStrip>
    </div>
  );
}

/* ── Hash helpers for avatar colors ── */
function djb2(str: string): number {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) hash = ((hash << 5) + hash) ^ str.charCodeAt(i);
  return Math.abs(hash);
}
const COLORS = ["#8b5cf6","#a855f7","#6366f1","#22d3ee","#14b8a6","#10b981","#84cc16","#f59e0b","#f97316","#f43f5e","#ec4899","#d946ef"];
function hashColor(s: string, offset: number) { const h = djb2(s); return COLORS[(h + offset * 7) % COLORS.length]; }
function hashAngle(s: string) { return (djb2(s) % 8) * 45; }
