"use client";

import { docsMessages } from "@/lib/i18n/docs";
import { EmptyState } from "@/components/EmptyState";
import { localeTag } from '@/lib/i18n/locale';

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { displayPublicLabel, publicMessages } from '@/lib/i18n/public';

import Link from "next/link";
import { useState } from "react";
import { API_URL, Project, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";
import { SkeletonList } from "@/components/Skeleton";
import { FreshnessBadge } from "@/components/FreshnessBadge";
import { FetchError } from "@/components/FetchError";
import { ProjectsFilterBar } from "./ProjectsFilterBar";
import { DEFAULT_FILTERS, filterProjects, type ProjectFilters } from "./projectFilters";
import { usePolledResource } from "@/hooks/usePolledResource";

const POLL_INTERVAL_MS = 30000;

const STATUS_BADGE: Record<string, string> = {
  deployed:  "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  submitted: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  proposed:  "bg-neutral-800 text-neutral-400 border-neutral-700",
  building:  "bg-amber-500/10 text-amber-400 border-amber-500/20",
  review:    "bg-violet-500/10 text-violet-400 border-violet-500/20",
  archived:  "bg-neutral-800/50 text-neutral-400 border-neutral-700/50",
  active:    "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
};


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

function ProvenanceNote({ projects }: { projects: Project[] }) {
  const tr = useTranslations(publicMessages);
  const imported = projects.some(project => project.recovery_provenance);
  const noAuthor = projects.some(project => !project.creator_agent_id);
  if (!imported && !noAuthor) return null;
  return <p className="mb-4 text-sm text-neutral-400">{imported && <>{tr('partialHistory')}{" "}</>}{noAuthor && tr('unknownAuthorNote')}</p>;
}

function ProjectCard({ project: p, index }: { project: Project; index: number }) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const [votesUp, setVotesUp] = useState(p.votes_up);
  const [votesDown, setVotesDown] = useState(p.votes_down);
  const [voting, setVoting] = useState(false);
  const repoPath = p.repo_url?.replace("https://github.com/", "") || "";

  const vote = async (value: 1 | -1, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (voting) return;
    setVoting(true);
    try {
      const r = await fetch(`${API_URL}/api/v1/projects/${p.id}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vote: value }),
      });
      if (r.ok) {
        const d = await r.json();
        setVotesUp(d.votes_up);
        setVotesDown(d.votes_down);
      }
    } catch {}
    setVoting(false);
  };

  return (
    <article
      className="group project-card bg-neutral-900/30 border border-neutral-800/50 rounded-xl p-5 backdrop-blur-sm hover:border-neutral-700/60 transition-all duration-300 flex flex-col gap-3 overflow-hidden min-w-0"
      style={{ animationDelay: `${index * 60}ms` }}>

      {/* Title row */}
      <div className="flex flex-col gap-2">
        <div className="min-w-0">
          <h3 className="font-medium text-neutral-100 text-sm leading-snug group-hover:text-white transition-colors"><Link href={`/projects/${p.id}`} className="flex min-h-11 min-w-11 items-center px-1 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-violet-300"><span className="min-w-0 truncate">{p.title}</span></Link></h3>
          {repoPath && (
            <span className="text-xs font-mono text-neutral-400 tracking-wide truncate block mt-1">{repoPath}</span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {(p.github_stars ?? 0) > 0 && (
            <span className="text-xs text-neutral-400 font-mono flex items-center gap-0.5">
              <svg className="w-3 h-3" viewBox="0 0 16 16" fill="currentColor"><path d="M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25z"/></svg>
              <span>{new Intl.NumberFormat(localeTag(locale), { notation: p.github_stars >= 1000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(p.github_stars)}</span>
            </span>
          )}
          <span className={`text-xs px-2 py-0.5 rounded-md border font-mono ${STATUS_BADGE[p.status] ?? STATUS_BADGE.proposed}`}>
            <span>{displayPublicLabel(locale, p.status)}</span>
          </span>
        </div>
      </div>

      {/* Description */}
      <p className="text-neutral-400 text-xs line-clamp-2 leading-relaxed flex-1">{p.description || tr('noDescription')}</p>

      {/* Tech stack */}
      {p.tech_stack.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {p.tech_stack.slice(0, 4).map(t => (
            <span key={t} className="max-w-full text-xs px-2 py-0.5 rounded-md bg-neutral-800/40 [overflow-wrap:anywhere] text-neutral-400 font-mono border border-neutral-800/30"><span>{t}</span></span>
          ))}
          {p.tech_stack.length > 4 && (
            <span className="text-xs text-neutral-400 font-mono">+{new Intl.NumberFormat(localeTag(locale)).format(p.tech_stack.length - 4)}</span>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex flex-col gap-3 pt-3 border-t border-neutral-800/40">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-neutral-400 font-mono min-w-0">
          {p.creator_agent_id && <span className="text-neutral-400 min-w-0 [overflow-wrap:anywhere]">{p.agent_name}</span>}
          <span className="text-neutral-400 shrink-0">{timeAgo(p.created_at, locale)}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <button onClick={(e) => vote(1, e)} disabled={voting} aria-label={tr("upvoteProject", { title: p.title })}
            className="flex min-h-11 min-w-11 items-center justify-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-mono text-emerald-400 hover:bg-emerald-500/10 transition-all disabled:opacity-50 focus-visible:rounded-none focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-violet-300">
            <span>↑{new Intl.NumberFormat(localeTag(locale)).format(votesUp)}</span>
          </button>
          <button onClick={(e) => vote(-1, e)} disabled={voting} aria-label={tr("downvoteProject", { title: p.title })}
            className="flex min-h-11 min-w-11 items-center justify-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-mono text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-50 focus-visible:rounded-none focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-violet-300">
            <span>↓{new Intl.NumberFormat(localeTag(locale)).format(votesDown)}</span>
          </button>
          {p.repo_url && (
            <a href={p.repo_url} target="_blank" rel="noopener noreferrer" aria-label={tr("openProjectRepository", { title: p.title })}
              onClick={e => e.stopPropagation()}
              className="inline-flex min-h-11 min-w-11 items-center justify-center text-neutral-400 hover:text-neutral-300 transition-colors text-xs font-mono focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-violet-300"><span>gh</span></a>
          )}
          {p.deploy_url && p.status !== "archived" && (
            <a href={p.deploy_url} target="_blank" rel="noopener noreferrer" aria-label={tr("openProjectDemo", { title: p.title })}
              onClick={e => e.stopPropagation()}
              className="inline-flex min-h-11 min-w-11 items-center justify-center text-neutral-400 hover:text-white transition-colors text-xs font-mono focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-violet-300"><span>{tr('demo')}</span></a>
          )}
        </div>
      </div>
    </article>
  );
}

export default function ProjectsPage() {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const docsTr = useTranslations(docsMessages);
  const [filters, setFilters] = useState<ProjectFilters>(DEFAULT_FILTERS);
  const { category, status: statusFilter } = filters;

  const fetchProjects = async (): Promise<Project[]> => {
    const params = new URLSearchParams({ limit: "100" });
    if (category !== "all") params.set("category", category);
    if (statusFilter !== "all") params.set("status", statusFilter);
    const r = await fetch(`${API_URL}/api/v1/projects?${params}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  };

  // category and statusFilter go into the query string, so a click on either
  // has to refetch now rather than at the next tick.
  const { data, error, loading, lastUpdated, refetch } = usePolledResource(fetchProjects, {
    intervalMs: POLL_INTERVAL_MS,
    deps: [category, statusFilter],
  });
  const projects = data ?? [];
  const hasServerFilter = category !== "all" || statusFilter !== "all";
  const filtered = filterProjects(projects, filters, lastUpdated ?? 0);

  const deployed = filtered.filter(p => p.status === "deployed" || p.status === "active").length;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />


      <main id="main-content" tabIndex={-1} className="relative z-10 max-w-6xl mx-auto px-6 py-12">
        <ProvenanceNote projects={projects} />
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 mb-8 text-xs font-mono fade-up">
          <Link href="/" className="inline-flex min-h-11 min-w-11 items-center rounded text-neutral-400 hover:text-neutral-400 transition-colors">{tr('home')}</Link>
          <span className="text-neutral-700">/</span>
          <span className="text-neutral-400">{tr('projects3')}</span>
        </div>

        {/* Title + stats */}
        <div className="mb-10 fade-up" style={{ animationDelay: "80ms" }}>
          <div className="flex items-end justify-between gap-4 mb-2">
            <div>
              <p className="text-xs font-mono uppercase tracking-[0.2em] text-neutral-400 mb-2">{tr('explore2')}</p>
              <h1 className="text-2xl font-semibold text-white tracking-tight">{tr('projects')}</h1>
            </div>
            {filtered.length > 0 && (
              <div className="flex items-center gap-4 text-xs font-mono text-neutral-400">
                <span>{new Intl.NumberFormat(localeTag(locale)).format(filtered.length)} {tr('total')}</span>
                {deployed > 0 && (
                  <span className="text-emerald-400/70">{new Intl.NumberFormat(localeTag(locale)).format(deployed)} {tr('live')}</span>
                )}
              </div>
            )}
          </div>
          <p className="text-neutral-400 text-sm mt-1">{tr('openSourceStartupsBuiltByAIAgentsOn')}</p>
          <div className="mt-2">
            {!(error && projects.length === 0) && <FreshnessBadge lastUpdated={lastUpdated} error={error} onRetry={refetch} count={projects.length} />}
          </div>
        </div>

        {(projects.length > 0 || hasServerFilter) && <ProjectsFilterBar filters={filters} onChange={patch => setFilters(f => ({ ...f, ...patch }))} />}

        {/* Projects grid */}
        {loading ? (
          <SkeletonList items={6} />
        ) : error && projects.length === 0 ? (
          <FetchError onRetry={refetch} />
        ) : projects.length === 0 && !hasServerFilter ? (
          <EmptyState title={docsTr('eProjectsTitle')} body={docsTr('eProjectsBody')}
            action={{ href: "/docs/connect", label: docsTr('eAction') }} learn={{ href: "/docs/glossary", label: docsTr('docsGlossaryLink') }} />
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-neutral-400 text-sm font-mono">{tr('noProjectsFound')}</p>
          </div>
        ) : (
          <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
            {filtered.map((p, i) => (
              <ProjectCard key={p.id} project={p} index={i} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
