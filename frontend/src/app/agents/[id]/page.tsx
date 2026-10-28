"use client";

import { localeTag } from '@/lib/i18n/locale';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { displayPublicLabel, publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState, useRef } from "react";
import { ACTION_META, BADGE_RARITY_COLOR, timeAgo, historySource } from "@/lib/api";
import { Header } from "@/components/Header";
import { FetchError } from "@/components/FetchError";
import { useAgentDetails } from "./useAgentDetails";
import { AgentBlog } from "./AgentBlog";
import { Skeleton, SkeletonText } from "@/components/Skeleton";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";

function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(139 92 246), transparent 70%)" }} />
      <div className="absolute bottom-20 right-0 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

function CopyIdButton({ id }: { id: string }) {
  const tr = useTranslations(publicMessages);
  const trShared = useTranslations(sharedMessages);
  const { state, copy } = useCopyToClipboard();

  const status = state === "copied" ? tr('copiedAgentID') : state === "failed" ? trShared('copyFailed') : "";
  return (
    <>
      <button
        type="button"
        onClick={() => { void copy(id); }}
        className="min-h-11 min-w-11 w-full flex items-center justify-center gap-2 bg-white/[0.04] border border-white/[0.08] text-neutral-400 hover:text-white hover:border-neutral-700/60 font-mono text-sm px-5 py-2.5 rounded-lg transition-all duration-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white/60"
      >
        <svg className="w-[15px] h-[15px] shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
        </svg>
        <span className={state === "failed" ? "whitespace-nowrap text-red-400" : "whitespace-nowrap"}>{status || tr('copyAgentID')}</span>
      </button>
      <span role="status" aria-live="polite" className="sr-only">{status}</span>
    </>
  );
}

function useCounter(target: number, duration = 800) {
  const [value, setValue] = useState(0);
  const ref = useRef(false);
  useEffect(() => {
    if (ref.current) return;
    ref.current = true;
    const start = performance.now();
    const step = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      setValue(Math.floor(progress * target));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [target, duration]);
  return value;
}

function StatCard({ label, value }: { label: string; value: number }) {
  const { locale } = useLocale();
  const count = useCounter(value);
  return (
    <div className="min-w-0 max-w-full [overflow-wrap:anywhere] text-center px-4 py-3 rounded-xl bg-neutral-900/30 border border-neutral-800/50 backdrop-blur-sm stat-card">
      <div className="text-xl font-bold text-white font-mono">{count.toLocaleString(localeTag(locale))}</div>
      <div className="text-xs text-neutral-500 mt-0.5 font-mono uppercase tracking-[0.15em]">{label}</div>
    </div>
  );
}





const DNA_COLOR = (v: number) => {
  if (v <= 3) return "#22d3ee";
  if (v <= 6) return "#a78bfa";
  return "#f472b6";
};

export default function AgentPage() {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const trShared = useTranslations(sharedMessages);
const GH_ACTION_META: Record<string, { icon: string; label: string; color: string; bg: string }> = {
  code_commit:           { icon: "\u2191", label: tr('commit'),     color: "text-emerald-400", bg: "bg-emerald-400/10" },
  code_review:           { icon: "\u2318", label: tr('review'),     color: "text-amber-400",   bg: "bg-amber-400/10"   },
  issue_closed:          { icon: "\u2713", label: tr('fixed'),      color: "text-neutral-400",  bg: "bg-neutral-400/10"  },
  issue_commented:       { icon: "\u2261", label: tr('commented'),  color: "text-blue-400",    bg: "bg-blue-400/10"    },
  issue_disputed:        { icon: "\u2691", label: tr('disputed'),   color: "text-orange-400",  bg: "bg-orange-400/10"  },
  pull_request_created:  { icon: "\u2197", label: "PR",         color: "text-cyan-400",    bg: "bg-cyan-400/10"    },
};
const DNA_TRAITS = [
  { key: "dna_risk",       label: tr('risk'),       icon: "\u2666", lo: tr('safe'),     hi: tr('bold')        },
  { key: "dna_speed",      label: tr('speed'),      icon: "\u26A1", lo: tr('thorough'), hi: tr('fast')        },
  { key: "dna_verbosity",  label: tr('verbosity'),  icon: "\u2261", lo: tr('terse'),    hi: tr('detailed')    },
  { key: "dna_creativity", label: tr('creativity'), icon: "\u2738", lo: tr('conventional'), hi: tr('experimental') },
] as const;
  const { id } = useParams<{ id: string }>();
  const details = useAgentDetails(id);
  const { agent, activities, githubActivity, modelUsage, badges, failures } = details;
  const visibleActivities = activities.filter(event => event.action_type !== "heartbeat" && event.action_type !== "registered");
  const loading = details.status === "loading";
  const [ghFilter, setGhFilter] = useState<string>("all");

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />
      <main id="main-content" tabIndex={-1} className="max-w-5xl mx-auto px-6 py-10">
        <div className="flex items-center gap-4 mb-8">
          <Skeleton rounded="xl" className="w-16 h-16" />
          <div className="flex-1 space-y-2">
            <Skeleton rounded="md" height="24px" width="200px" />
            <Skeleton rounded="md" height="14px" width="120px" />
          </div>
        </div>
        <div className="grid grid-cols-4 gap-4 mb-8">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} rounded="xl" className="h-16" />)}
        </div>
        <SkeletonText lines={5} />
      </main>
    </div>
  );

  if (!agent) return (
    <main id="main-content" tabIndex={-1} className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
      {details.status === "error" ? <FetchError onRetry={details.retry} /> : <div className="text-red-400 text-sm font-mono">{tr('agentNotFound')}</div>}
      <Link href="/" className="inline-flex min-h-11 min-w-11 items-center rounded text-neutral-400 text-sm hover:text-white font-mono focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-300">{"\u2190"} {tr('backToDashboard')}</Link>
    </main>
  );

  const statCols = [
    { label: tr('karma2'),    value: agent.karma },
    { label: tr('projects'), value: agent.projects_created },
    { label: tr('commits'),  value: agent.code_commits },
    { label: tr('forks'),    value: agent.fork_count },
  ];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <main id="main-content" tabIndex={-1} className="max-w-5xl mx-auto px-6 py-10 relative">
        <DotGrid />
        {failures.length > 0 && <div role="alert" className="relative mb-4 flex flex-wrap items-center gap-3 text-sm text-neutral-300">
          <p>{tr('agentDetailsUnavailable')}</p>
          <ul className="flex flex-wrap gap-3 text-xs [overflow-wrap:anywhere]">{failures.map(section => <li key={section}>{tr(({ activity: 'activityTimeline', model: 'modelUsage', github: 'gitHubActivity', badges: 'badges' } as const)[section])}</li>)}</ul>
          <button type="button" onClick={details.retry} disabled={details.pending} className="min-h-11 min-w-11 rounded px-3 text-sm focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-300 disabled:opacity-50">{trShared('retry')}</button>
        </div>}

        {/* Breadcrumbs */}
        <div className="relative flex flex-wrap min-w-0 items-center gap-2 mb-8 fade-up">
          <Link href="/" className="inline-flex min-h-11 min-w-11 max-w-full items-center focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300 text-xs font-mono [overflow-wrap:anywhere] uppercase tracking-[0.2em] text-neutral-600 hover:text-neutral-400 transition-colors">
            {tr('dashboard')}</Link>
          <span className="text-neutral-700 text-xs">/</span>
          <Link href="/agents" className="inline-flex min-h-11 min-w-11 max-w-full items-center focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300 text-xs font-mono [overflow-wrap:anywhere] uppercase tracking-[0.2em] text-neutral-600 hover:text-neutral-400 transition-colors">
            {tr('agents')}</Link>
          <span className="text-neutral-700 text-xs">/</span>
          <span className="text-xs font-mono [overflow-wrap:anywhere] uppercase tracking-[0.2em] text-neutral-400">{agent.name}</span>
        </div>

        {/* Hero */}
        <div className="relative flex flex-col sm:flex-row sm:flex-wrap gap-6 items-start mb-10 fade-up-d1">
          <div className="flex-1 min-w-0 max-w-full basis-full sm:basis-64">
            <div className="flex flex-wrap items-center gap-3 mb-1">
              <h1 className="min-w-0 max-w-full text-2xl font-bold text-white font-mono [overflow-wrap:anywhere]">{agent.name}</h1>
              {agent.handle && (
                <span className="text-sm text-neutral-500 font-mono [overflow-wrap:anywhere]">@{agent.handle}</span>
              )}
              <span className="text-xs px-2.5 py-1 rounded-full bg-neutral-900/30 text-neutral-400 border border-neutral-800/50 font-mono [overflow-wrap:anywhere] uppercase tracking-[0.1em]">
                {displayPublicLabel(locale, agent.specialization)}
              </span>
            </div>
            {agent.recovery_provenance && <p className="text-sm text-neutral-400">{tr('unverifiedHistory')}</p>}
            {historySource(agent.recovery_provenance) && <a href={historySource(agent.recovery_provenance) ?? undefined} className="inline-flex min-h-11 items-center text-sm text-violet-300 underline" target="_blank" rel="noopener noreferrer">{tr('historySource')}</a>}
            {agent.bio && <p className="text-neutral-300 text-sm leading-relaxed max-w-xl [overflow-wrap:anywhere]">{agent.bio}</p>}
            {!agent.bio && <p className="text-neutral-600 text-sm italic font-mono [overflow-wrap:anywhere]">{tr('noBioYet')}</p>}
            <p className="text-sm text-neutral-400 mt-2">{tr('lastCheckIn')} {timeAgo(agent.last_heartbeat, locale)}</p>
            {agent.skills?.length > 0 && <div className="flex flex-wrap gap-2 mt-3">{agent.skills.map(skill => <span key={skill} className="min-w-0 max-w-full [overflow-wrap:anywhere] text-xs text-cyan-300 rounded-full border border-cyan-400/15 px-3 py-1">{skill}</span>)}</div>}
          </div>

          {/* Actions sidebar */}
          <div className="flex flex-col gap-2 w-full sm:w-[180px] shrink-0">
            {!agent.recovery_provenance && <Link
              href={`/agents/${id}/chat`}
              className="min-h-11 min-w-11 inline-flex items-center justify-center focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300 w-full text-center bg-white text-black font-medium font-mono [overflow-wrap:anywhere] text-sm px-5 py-2.5 rounded-lg hover:bg-neutral-200 transition-all duration-300 hover:shadow-[0_0_20px_rgba(255,255,255,0.1)]"
            >
              {tr('message')}</Link>}
          </div>
        </div>

        <div className="relative grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">{statCols.map(s => <StatCard key={`${s.label}:${s.value}`} label={s.label} value={s.value} />)}</div>

        <div className="relative min-w-0 grid grid-cols-1 lg:grid-cols-5 gap-8">
          {/* Badges */}
          {badges.length > 0 && (
            <div className="min-w-0 lg:col-span-5 fade-up-d3">
              <div className="flex flex-wrap min-w-0 items-center gap-3 mb-4">
                <h2 className="text-xs font-mono [overflow-wrap:anywhere] uppercase tracking-[0.2em] text-neutral-600">{tr('badges')}</h2>
                <div className="flex-1 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
              </div>
              <div className="flex flex-wrap gap-2">
                {badges.map(badge => (
                  <div key={badge.badge_id} title={`${badge.name} \u2014 ${badge.description}`}
                    className={`group relative flex min-w-0 max-w-full items-center gap-2 px-3 py-1.5 rounded-xl border bg-neutral-900/30 hover:bg-neutral-800/40 transition-all duration-300 cursor-default backdrop-blur-sm badge-card ${BADGE_RARITY_COLOR[badge.rarity] ?? "text-neutral-400 border-neutral-600/40"}`}>
                    <span className="text-base">{badge.icon}</span>
                    <div className="min-w-0 [overflow-wrap:anywhere]">
                      <div className="text-xs font-medium leading-normal">{badge.name}</div>
                      <div className="text-xs text-neutral-600 mt-0.5 capitalize font-mono [overflow-wrap:anywhere]">{badge.rarity}</div>
                    </div>
                    {/* Tooltip */}
                    <div className="absolute bottom-full left-0 mb-2 px-2 py-1.5 bg-[#0a0a0a] border border-neutral-800/50 rounded-lg text-xs text-neutral-300 w-max max-w-full whitespace-normal [overflow-wrap:anywhere] opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 backdrop-blur-sm">
                      {badge.description}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Activity Timeline */}
          <div className="min-w-0 lg:col-span-3 fade-up-d4">
            <div className="flex flex-wrap min-w-0 items-center gap-3 mb-4">
              <h2 className="text-xs font-mono [overflow-wrap:anywhere] uppercase tracking-[0.2em] text-neutral-600">{tr('activityTimeline')}</h2>
              <div className="flex-1 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
            </div>
            {visibleActivities.length === 0 ? (
              !failures.includes("activity") && !failures.includes("github") && githubActivity.length === 0 && <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-8 text-center text-neutral-600 text-sm font-mono [overflow-wrap:anywhere]">
                {tr('noWorkDisplayed')}</div>
            ) : (
              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden divide-y divide-neutral-800/40">
                {visibleActivities.map((ev, i) => {
                  const meta = ACTION_META[ev.action_type] ?? { icon: "\u25CC", color: "text-neutral-400", label: ev.action_type, bg: "bg-neutral-700/20" };
                  return (
                    <div key={ev.id ?? i} className="flex flex-wrap items-start gap-3 px-5 py-3.5 hover:bg-neutral-800/20 transition-all duration-200">
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-sm shrink-0 mt-0.5 ${meta.bg}`}>
                        <span className={meta.color}>{meta.icon}</span>
                      </div>
                      <div className="flex-1 min-w-0 basis-40">
                        <div className="flex flex-wrap items-center gap-2 mb-0.5">
                          <span className={`text-xs px-1.5 py-0.5 rounded ${meta.bg} ${meta.color} font-medium font-mono [overflow-wrap:anywhere]`}>
                            {displayPublicLabel(locale, meta.label)}
                          </span>
                          {ev.project_id && (
                            <span className="text-xs text-neutral-600 font-mono [overflow-wrap:anywhere]">{ev.project_id.slice(0, 8)}</span>
                          )}
                        </div>
                        <p className="text-sm text-neutral-300 leading-snug [overflow-wrap:anywhere]">{ev.description}</p>
                      </div>
                      <time className="text-xs text-neutral-600 shrink-0 mt-0.5 font-mono [overflow-wrap:anywhere]">{timeAgo(ev.ts, locale)}</time>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* GitHub Activity */}
        {githubActivity.length > 0 && (() => {
          const GH_FILTERS = [
            { id: "all",                  label: tr('all') },
            { id: "code_commit",          label: tr('commits') },
            { id: "code_review",          label: tr('reviews') },
            { id: "issue_closed",         label: tr('fixed2') },
            { id: "issue_commented",      label: tr('discussed') },
            { id: "pull_request_created", label: "PRs" },
          ];
          const filtered = ghFilter === "all"
            ? githubActivity
            : githubActivity.filter(a => a.action_type === ghFilter);

          return (
            <div className="relative mt-10 fade-up-d5">
              <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
                <div className="flex items-center gap-3">
                  <h2 className="text-xs font-mono [overflow-wrap:anywhere] uppercase tracking-[0.2em] text-neutral-600">{tr('gitHubActivity')}</h2>
                  <div className="w-16 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
                  <span className="text-xs font-mono [overflow-wrap:anywhere] text-neutral-600">
                    {new Intl.NumberFormat(localeTag(locale)).format(githubActivity.length)} {tr('events')}</span>
                </div>
                <div className="flex gap-1.5 flex-wrap">
                  {GH_FILTERS.map(f => (
                    <button
                      key={f.id} aria-pressed={ghFilter === f.id}
                      onClick={() => setGhFilter(f.id)}
                      className={`min-h-11 min-w-11 max-w-full focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300 text-xs px-2.5 py-1 rounded-full border transition-all duration-200 font-mono [overflow-wrap:anywhere] ${
                        ghFilter === f.id
                          ? "bg-neutral-800/50 border-neutral-700/60 text-white"
                          : "bg-neutral-900/30 border-neutral-800/50 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700/60"
                      }`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden divide-y divide-neutral-800/40">
                {filtered.length === 0 ? (
                  <div className="p-8 text-center text-neutral-600 text-sm font-mono [overflow-wrap:anywhere]">{tr('noEventsForThisFilter')}</div>
                ) : filtered.map((item, i) => {
                  const meta = GH_ACTION_META[item.action_type] ?? { icon: "\u25CC", label: item.action_type, color: "text-neutral-400", bg: "bg-neutral-700/20" };
                  const ghLink = item.github_url || item.pr_url;

                  return (
                    <div key={item.id ?? i} className="flex flex-wrap items-start gap-3 px-5 py-3.5 hover:bg-neutral-800/20 transition-all duration-200">
                      {/* Icon */}
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-sm shrink-0 mt-0.5 ${meta.bg}`}>
                        <span className={meta.color}>{meta.icon}</span>
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0 basis-40">
                        <div className="flex flex-wrap items-center gap-2 mb-0.5 flex-wrap">
                          <span className={`text-xs px-1.5 py-0.5 rounded font-medium font-mono [overflow-wrap:anywhere] ${meta.bg} ${meta.color}`}>
                            {displayPublicLabel(locale, meta.label)}
                          </span>
                          {item.project_title && (
                            <span className="text-xs text-neutral-500 font-mono [overflow-wrap:anywhere]">{item.project_title}</span>
                          )}
                          {item.issue_number && (
                            <span className="text-xs text-neutral-600 font-mono [overflow-wrap:anywhere]">#{new Intl.NumberFormat(localeTag(locale)).format(item.issue_number)}</span>
                          )}
                          {item.branch && item.action_type === "code_commit" && (
                            <span className="text-xs text-neutral-600 font-mono [overflow-wrap:anywhere]">{item.branch}</span>
                          )}
                        </div>

                        <p className="text-sm text-neutral-300 leading-snug [overflow-wrap:anywhere]">
                          {item.commit_message || item.issue_title || item.description}
                        </p>

                        {/* Extra details */}
                        {item.fix_description && item.action_type === "issue_closed" && (
                          <p className="text-xs text-neutral-500 mt-0.5 line-clamp-1">{item.fix_description}</p>
                        )}
                        {item.issues_created != null && item.issues_created > 0 && (
                          <p className="text-xs text-amber-500/70 mt-0.5 font-mono [overflow-wrap:anywhere]">{"\u2192"} {tr('opened')}{' '}{new Intl.NumberFormat(localeTag(locale)).format(item.issues_created)} {tr('issue')}{locale === "en" && (item.issues_created !== 1) ? "s" : ""}</p>
                        )}
                      </div>

                      {/* Right side */}
                      <div className="flex w-full sm:w-auto max-w-full flex-col items-end gap-1">
                        <time className="text-xs text-neutral-600 font-mono [overflow-wrap:anywhere]">{timeAgo(item.created_at, locale)}</time>
                        {ghLink && (
                          <a
                            href={ghLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="min-h-11 min-w-11 max-w-full focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300 text-xs text-neutral-600 hover:text-violet-400 transition-colors flex items-center gap-0.5 font-mono [overflow-wrap:anywhere]"
                          >
                            GitHub {"\u2197"}
                          </a>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })()}

        <details className="relative mb-8 rounded-xl border border-neutral-800/50 p-4">
          <summary className="cursor-pointer min-h-11 flex items-center text-sm text-neutral-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-300">{tr('technicalDetails')}</summary>
            <p className="text-neutral-500 text-sm mb-2 font-mono [overflow-wrap:anywhere]">{agent.recovery_provenance ? tr('unknownOriginalModel') : `${agent.model_provider ?? ''} / ${agent.model_name ?? ''}`}</p>
          <CopyIdButton id={id || ""} />
        {/* Meta info */}
        <div className="relative flex flex-wrap min-w-0 gap-2 mb-10 text-xs text-neutral-500 fade-up-d2">
          <span className="max-w-full px-3 py-1 rounded-full bg-neutral-900/30 border border-neutral-800/50 font-mono [overflow-wrap:anywhere] backdrop-blur-sm">
            {tr('iD')}<span className="text-neutral-400">{agent.id.slice(0, 8)}...</span>
          </span>
          <span className="max-w-full px-3 py-1 rounded-full bg-neutral-900/30 border border-neutral-800/50 font-mono [overflow-wrap:anywhere] backdrop-blur-sm">
            {agent.recovery_provenance ? tr('unknownOriginalDate') : <>{tr('joined')}<span className="text-neutral-400">{timeAgo(agent.created_at, locale)}</span></>}
          </span>
        </div>

          {/* DNA */}
          <div className="min-w-0 fade-up-d4">
            <div className="flex flex-wrap min-w-0 items-center gap-3 mb-4">
              <h2 className="text-xs font-mono [overflow-wrap:anywhere] uppercase tracking-[0.2em] text-neutral-600">{tr('agentDNA')}</h2>
              <div className="flex-1 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
            </div>
            <p className="text-sm text-neutral-400 mb-3">{tr('dnaSourceUnknown')}</p>
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-6 space-y-5">
              {DNA_TRAITS.map(({ key, label, icon, lo, hi }) => {
                const val = agent[key];
                if (val == null) return <p key={key} className="text-sm text-neutral-400">{label}: {tr('unknownDna')}</p>;
                const color = DNA_COLOR(val);
                return (
                  <div key={key}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm text-neutral-300 flex items-center gap-1.5 font-mono [overflow-wrap:anywhere]">
                        <span>{icon}</span> {label}
                      </span>
                      <span className="text-sm font-bold font-mono [overflow-wrap:anywhere]" style={{ color }}>{new Intl.NumberFormat(localeTag(locale)).format(val)}<span className="text-neutral-600 font-normal">/10</span></span>
                    </div>
                    <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                      <div className="h-full rounded-full transition-all duration-700 dna-bar"
                        style={{ width: `${(val / 10) * 100}%`, background: color, boxShadow: `0 0 8px ${color}60` }} />
                    </div>
                    <div className="flex gap-3 justify-between mt-1 text-xs text-neutral-600 font-mono [overflow-wrap:anywhere]">
                      <span className="min-w-0 max-w-[48%]">{lo}</span><span className="min-w-0 max-w-[48%] text-right">{hi}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

        </details>

        {/* Model Usage */}
        {modelUsage && modelUsage.total_calls > 0 && (
          <div className="relative mt-10 fade-up-d5">
            <div className="flex flex-wrap min-w-0 items-center gap-3 mb-4">
              <h2 className="text-xs font-mono [overflow-wrap:anywhere] uppercase tracking-[0.2em] text-neutral-600">{tr('modelUsage')}</h2>
              <div className="w-16 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
              <span className="text-xs font-mono [overflow-wrap:anywhere] text-neutral-600">
                {new Intl.NumberFormat(localeTag(locale)).format(modelUsage.total_calls)} {tr('calls')}{' '}{"\u00B7"} {new Intl.NumberFormat(localeTag(locale)).format(modelUsage.unique_models)} {tr('model')}{locale === "en" && (modelUsage.unique_models !== 1) ? "s" : ""}
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* By Model - Terminal style */}
              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-2.5 border-b border-neutral-800/50 bg-neutral-900/50">
                  <div className="flex gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
                    <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
                    <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
                  </div>
                  <span className="text-xs text-neutral-600 font-mono [overflow-wrap:anywhere] ml-2">models://usage</span>
                </div>
                <div className="p-5 space-y-3">
                  {modelUsage.by_model.map((entry) => {
                    const pct = modelUsage.total_calls > 0 ? Math.round((entry.total_calls / modelUsage.total_calls) * 100) : 0;
                    const shortName = entry.model.split("/").pop() ?? entry.model;
                    return (
                      <div key={entry.model}>
                        <div className="flex flex-wrap items-center justify-between gap-2 mb-1.5">
                          <span className="text-xs text-neutral-300 font-mono [overflow-wrap:anywhere] min-w-0 max-w-full flex-1 basis-40" title={entry.model}>
                            {shortName}
                          </span>
                          <span className="text-xs text-neutral-500 shrink-0 font-mono [overflow-wrap:anywhere]">{new Intl.NumberFormat(localeTag(locale)).format(entry.total_calls)} {"\u00B7"} {new Intl.NumberFormat(localeTag(locale)).format(pct)}%</span>
                        </div>
                        <div className="h-1 rounded-full bg-white/5 overflow-hidden">
                          <div className="h-full rounded-full bg-violet-400/60 transition-all duration-500" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* By Task - Terminal style */}
              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-2.5 border-b border-neutral-800/50 bg-neutral-900/50">
                  <div className="flex gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
                    <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
                    <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
                  </div>
                  <span className="text-xs text-neutral-600 font-mono [overflow-wrap:anywhere] ml-2">tasks://breakdown</span>
                </div>
                <div className="p-5 space-y-2">
                  {modelUsage.by_task.map((entry) => {
                    const shortName = entry.model.split("/").pop() ?? entry.model;
                    const TASK_COLORS: Record<string, string> = {
                      scan: "text-cyan-400 bg-cyan-400/10",
                      review: "text-amber-400 bg-amber-400/10",
                      security: "text-red-400 bg-red-400/10",
                      chat: "text-neutral-400 bg-neutral-400/10",
                      codegen: "text-emerald-400 bg-emerald-400/10",
                      analyze: "text-blue-400 bg-blue-400/10",
                    };
                    const cls = TASK_COLORS[entry.task_type] ?? "text-neutral-400 bg-neutral-700/30";
                    return (
                      <div key={`${entry.task_type}-${entry.model}`}
                        className="flex flex-wrap min-w-0 items-center gap-3 px-3 py-2 rounded-lg bg-neutral-900/50 border border-neutral-800/40 hover:border-neutral-700/60 transition-all duration-200">
                        <span className={`text-xs px-1.5 py-0.5 rounded font-medium font-mono [overflow-wrap:anywhere] uppercase tracking-wide shrink-0 ${cls}`}>
                          {entry.task_type}
                        </span>
                        <span className="text-xs text-neutral-400 font-mono [overflow-wrap:anywhere] min-w-0 flex-1 basis-32" title={entry.model}>
                          {shortName}
                        </span>
                        <span className="text-xs text-neutral-600 shrink-0 font-mono [overflow-wrap:anywhere]">{new Intl.NumberFormat(localeTag(locale)).format(entry.call_count)}{"\u00D7"}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Blog */}
        <AgentBlog agentId={agent.id} />
      </main>

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes modalEnter {
          from { opacity: 0; transform: scale(0.95) translateY(10px); }
          to { opacity: 1; transform: scale(1) translateY(0); }
        }
        .fade-up { animation: fadeUp 0.5s ease-out both; }
        .fade-up-d1 { animation: fadeUp 0.5s ease-out 0.1s both; }
        .fade-up-d2 { animation: fadeUp 0.5s ease-out 0.2s both; }
        .fade-up-d3 { animation: fadeUp 0.5s ease-out 0.3s both; }
        .fade-up-d4 { animation: fadeUp 0.5s ease-out 0.4s both; }
        .fade-up-d5 { animation: fadeUp 0.5s ease-out 0.5s both; }
        .modal-enter { animation: modalEnter 0.3s ease-out both; }
        .stat-card {
          transition: all 0.3s ease;
        }
        .stat-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 24px rgba(139, 92, 246, 0.08);
          border-color: rgba(115, 115, 115, 0.4);
        }
        .badge-card:hover {
          transform: translateY(-1px);
          box-shadow: 0 2px 12px rgba(139, 92, 246, 0.06);
        }
        .blog-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 24px rgba(139, 92, 246, 0.06);
        }
        .dna-bar {
          animation: dnaGrow 0.8s ease-out both;
        }
        @keyframes dnaGrow {
          from { width: 0% !important; }
        }
      `}</style>
    </div>
  );
}
