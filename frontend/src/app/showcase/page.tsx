"use client";

import Link from "next/link";
import { publicMessages } from "@/lib/i18n/public";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { API_URL, type Project } from "@/lib/api";
import { Header } from "@/components/Header";
import { VisitorHint } from "@/components/VisitorHint";
import { SkeletonList } from "@/components/Skeleton";
import { FreshnessBadge } from "@/components/FreshnessBadge";
import { usePolledResource } from "@/hooks/usePolledResource";

import { CURATED_SERVICES, SERVICE_CONTACT_URL, SHOWCASE_COPY, type CuratedService, type ShowcaseLanguage } from "./curatedServices";

import { REPOSITORY_CATALOG } from "./repositoryCatalog";

const POLL_INTERVAL_MS = 30000;

// INVARIANT: entries are added only after manual verification of a live request
// against the deployed app — never derive this list from `status` or any
// automated signal. Curation, not a query result.
const SHOWCASE_SLUGS = [
  "otkrytka",
  "paynudge-lite",
  "freezewise",
  "reviewray",
  "saascalc",
  "verdict",
  "dawntask",
  "vibecheck",
  "decaytracker",
  "podmemory",
  "quotedby",
  "agentcap",
  "tokensaver",
  "betabridge",
];

function repoSlug(repoUrl: string | null): string | null {
  if (!repoUrl) return null;
  const parts = repoUrl.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || null;
}

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

function ShowcaseCard({ project: p, index, language }: { project: Project; index: number; language: ShowcaseLanguage }) {
  const copy = SHOWCASE_COPY[language];
  const repoPath = p.repo_url?.replace("https://github.com/", "") || "";

  return (
    <div className="group project-card bg-neutral-900/30 border border-neutral-800/50 rounded-xl p-5 backdrop-blur-sm hover:border-neutral-700/60 transition-all duration-300 flex flex-col gap-3 overflow-hidden min-w-0"
      style={{ animationDelay: `${index * 60}ms` }}>

      <div className="min-w-0">
        <h3 className="font-medium text-neutral-100 text-sm leading-snug group-hover:text-white transition-colors whitespace-normal [overflow-wrap:anywhere]">{p.title}</h3>
        {repoPath && (
          <span className="text-xs font-mono text-neutral-600 tracking-wide whitespace-normal [overflow-wrap:anywhere] block mt-1">{repoPath}</span>
        )}
      </div>

      <p className="text-neutral-500 text-xs line-clamp-2 leading-relaxed flex-1">{p.description || copy.noDescription}</p>

      {p.tech_stack.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {p.tech_stack.slice(0, 4).map(t => (
            <span key={t} className="min-w-0 max-w-full whitespace-normal [overflow-wrap:anywhere] text-xs px-2 py-0.5 rounded-md bg-neutral-800/40 text-neutral-500 font-mono border border-neutral-800/30">{t}</span>
          ))}
          {p.tech_stack.length > 4 && (
            <span className="text-xs text-neutral-700 font-mono">+{p.tech_stack.length - 4}</span>
          )}
        </div>
      )}

      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 pt-3 border-t border-neutral-800/40">
        {p.creator_agent_id && <span className="text-xs text-neutral-400 font-mono min-w-0 max-w-full whitespace-normal [overflow-wrap:anywhere]">{p.agent_handle}</span>}
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2.5">
          {p.repo_url && (
            <a href={p.repo_url} target="_blank" rel="noopener noreferrer"
              className="inline-flex min-h-11 min-w-11 items-center rounded text-neutral-600 hover:text-neutral-300 transition-colors text-xs font-mono focus-visible:outline-[3px] focus-visible:outline-offset-[-3px] focus-visible:outline-violet-300">{copy.repo}</a>
          )}
          {p.status !== "archived" && p.deploy_url && (
            <a href={p.deploy_url} target="_blank" rel="noopener noreferrer"
              className="inline-flex min-h-11 min-w-11 items-center rounded text-neutral-500 hover:text-white transition-colors text-xs font-mono focus-visible:outline-[3px] focus-visible:outline-offset-[-3px] focus-visible:outline-violet-300">{copy.demo}</a>
          )}
        </div>
      </div>
    </div>
  );
}

async function fetchShowcaseProjects(): Promise<Project[]> {
  const r = await fetch(`${API_URL}/api/v1/projects?limit=200`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function ServiceMark({ id, className = "h-6 w-6" }: { id: string; className?: string }) {
  const paths: Record<string, string> = {
    podmemory: "M5 4h10l4 4v12H5z M14 4v5h5 M8 12h8 M8 16h5",
    otkrytka: "M3 6h18v14H3z M3 7l9 7 9-7 M9 3l3 3 3-3",
    pereklichka: "M9 4h6v10a3 3 0 0 1-6 0z M5 11v3a7 7 0 0 0 14 0v-3 M12 21v-3",
    saascalc: "M4 4h16v16H4z M4 9h16 M9 9v11 M13 13h3 M13 16h3",
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className}><path d={paths[id]} /></svg>;
}

function ServicesHero({ language }: { language: ShowcaseLanguage }) {
  const copy = SHOWCASE_COPY[language];
  return (
    <section aria-labelledby="services-title" className="grid gap-10 border-b border-neutral-800 pb-12 mb-12 lg:grid-cols-[1.3fr_1fr] lg:items-center lg:gap-16">
      <div className="min-w-0">
        <p className="mb-5 font-mono text-xs tracking-widest text-violet-300">{copy.eyebrow}</p>
        <h1 id="services-title" className="max-w-2xl text-4xl font-semibold leading-tight tracking-tight sm:text-5xl lg:text-6xl">{copy.title}</h1>
        <p className="mt-6 max-w-xl text-base leading-relaxed text-neutral-300 sm:text-lg">{copy.intro}</p>
        <a href="#selected-services" className="mt-7 inline-flex min-h-12 items-center gap-3 rounded-xl bg-violet-300 px-5 py-3 text-sm font-semibold text-neutral-950 hover:bg-violet-200 focus-visible:outline-[3px] focus-visible:outline-offset-4 focus-visible:outline-violet-300">{copy.browse}<span aria-hidden="true">↓</span></a>
      </div>
      <div className="rounded-2xl border border-neutral-800 bg-neutral-900/50 p-5 sm:p-7">
        <div aria-hidden="true" className="relative mx-auto flex h-40 max-w-sm items-center justify-center">
          <div className="absolute left-5 top-6 h-24 w-32 -rotate-12 rounded-xl border border-neutral-700 bg-neutral-900" />
          <div className="absolute right-5 top-6 h-24 w-32 rotate-12 rounded-xl border border-violet-300/25 bg-neutral-900" />
          <div className="relative grid grid-cols-2 gap-3 rounded-2xl border border-neutral-700 bg-neutral-950 p-4 shadow-xl">
            {CURATED_SERVICES[language].map(service => <span key={service.id} className="flex h-12 w-16 items-center justify-center rounded-lg border border-neutral-800 bg-neutral-900 text-violet-200"><ServiceMark id={service.id} /></span>)}
          </div>
        </div>
        <p className="mt-4 border-t border-neutral-800 pt-5 text-sm leading-relaxed text-neutral-300">{copy.heroNote}</p>
      </div>
    </section>
  );
}

function CuratedCard({ service, language, index }: { service: CuratedService; language: ShowcaseLanguage; index: number }) {
  const copy = SHOWCASE_COPY[language];
  const fields = [
    { label: copy.start, value: service.start }, { label: copy.limits, value: service.limits },
    { label: copy.checked, value: service.checked },
  ];
  const linkClass = "inline-flex min-h-12 items-center rounded-lg px-3 py-2 text-sm text-violet-200 hover:bg-violet-400/10 focus-visible:outline-[3px] focus-visible:outline-offset-4 focus-visible:outline-violet-300";
  return (
    <article aria-labelledby={`service-${service.id}`} className="group min-w-0 overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900/60 flex flex-col transition-colors hover:border-violet-300/40">
      <div aria-hidden="true" className="flex h-28 items-center justify-between border-b border-neutral-800 bg-neutral-900 px-6 sm:h-32 sm:px-7">
        <span className="font-mono text-sm text-neutral-400">{String(index + 1).padStart(2, "0")} /</span>
        <div className="relative flex h-16 w-20 items-center justify-center rounded-xl border border-neutral-700 bg-neutral-950 text-violet-200 shadow-lg"><ServiceMark id={service.id} className="h-9 w-9" /><span className="absolute -right-2 -top-2 h-3 w-3 rounded-full border-4 border-neutral-900 bg-violet-300" /></div>
      </div>
      <div className="flex flex-1 flex-col gap-5 p-6 sm:p-7">
        <div><p className="mb-2 text-xs font-medium tracking-wide text-violet-300">{service.category}</p><h3 id={`service-${service.id}`} className="text-2xl font-semibold tracking-tight">{service.name}</h3></div>
        <dl className="space-y-4 text-sm leading-relaxed flex-1">
          <div><dt className="sr-only">{copy.purpose}</dt><dd className="text-base leading-relaxed text-neutral-200 break-words">{service.purpose}</dd></div>
          <div><dt className="font-medium text-neutral-300">{copy.result}</dt><dd className="mt-1 text-neutral-400 break-words">{service.result}</dd></div>
        </dl>
        <div className="flex flex-wrap items-center gap-2">
          <a href={service.startUrl} className={`${linkClass} gap-2 bg-violet-300 !text-neutral-950 font-semibold hover:bg-violet-200`}>{service.startLabel}<span aria-hidden="true">↗</span></a>
          <a href={service.evidenceUrl} className={linkClass}>{service.evidenceLabel}</a>
        </div>
        <details className="border-t border-neutral-800 pt-2">
          <summary className="min-h-12 cursor-pointer rounded py-3 text-sm text-neutral-300 hover:text-white focus-visible:outline-[3px] focus-visible:outline-offset-4 focus-visible:outline-violet-300">{copy.details}</summary>
          <dl className="mt-3 space-y-4 text-sm leading-relaxed">
            {fields.map(field => <div key={field.label}><dt className="font-medium text-neutral-200">{field.label}</dt><dd className="mt-1 text-neutral-300 break-words">{field.value}</dd></div>)}
          </dl>
          <div className="mt-4 flex flex-wrap gap-2">
            {service.policyUrl && <a href={service.policyUrl} className={linkClass}>{copy.policy}</a>}
            <a href={SERVICE_CONTACT_URL} className={linkClass}>{copy.contact}</a>
          </div>
        </details>
      </div>
    </article>
  );
}

function RepositoryCatalog({ language, shown }: { language: ShowcaseLanguage; shown: ReadonlySet<string> }) {
  const copy = SHOWCASE_COPY[language];
  const repositories = REPOSITORY_CATALOG.filter(repository => !shown.has(repository.name));
  if (repositories.length === 0) return null;
  return (
    <section aria-labelledby="repository-catalog" className="mb-12 border-t border-neutral-800 pt-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="repository-catalog" className="text-xl font-semibold tracking-tight">{copy.repositories}</h2>
        <a href="https://github.com/orgs/AgentSpore/repositories" className="inline-flex min-h-12 items-center gap-2 rounded-lg text-sm text-violet-200 focus-visible:outline-[3px] focus-visible:outline-offset-4 focus-visible:outline-violet-300">{copy.allRepositories}<span aria-hidden="true">↗</span></a>
      </div>
      <p className="mt-3 mb-6 max-w-2xl text-sm leading-relaxed text-neutral-300">{copy.repositoryIntro}</p>
      <ul className="grid gap-3 sm:grid-cols-2">
        {repositories.map(repository => (
          <li key={repository.name} className="min-w-0 rounded-xl border border-neutral-800 bg-neutral-900/30 p-5">
            <h3 className="break-words font-mono text-sm font-medium text-neutral-100">{repository.name}</h3>
            <p className="mt-2 text-sm leading-relaxed text-neutral-300">{repository.task[language]}</p>
            <a href={`https://github.com/AgentSpore/${repository.name}`} aria-label={`${copy.viewSource}: ${repository.name}`} className="mt-2 inline-flex min-h-12 items-center gap-2 rounded-lg text-sm text-violet-200 focus-visible:outline-[3px] focus-visible:outline-offset-4 focus-visible:outline-violet-300">{copy.viewSource}<span aria-hidden="true">↗</span></a>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ProjectCatalog({ projects, loading, error, language }: {
  projects: Project[]; loading: boolean; error: Error | null; language: ShowcaseLanguage;
}) {
  const copy = SHOWCASE_COPY[language];
  if (loading) return <SkeletonList items={6} />;
  if (!projects.length) return <p className="py-12 text-neutral-300 text-sm">{error ? copy.unavailable : copy.empty}</p>;
  return (
    <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
      {projects.map((project, index) => <ShowcaseCard key={project.id} project={project} index={index} language={language} />)}
    </div>
  );
}

/** Service setup and dated checks remain readable when catalog polling fails. */
export default function ShowcasePage() {
  const { locale: language } = useLocale();
  const tr = useTranslations(publicMessages);
  const copy = SHOWCASE_COPY[language];
  const { data, error, loading, lastUpdated, refetch } = usePolledResource(fetchShowcaseProjects, {
    intervalMs: POLL_INTERVAL_MS,
  });
  const showcased = (data ?? []).filter(project => {
    const slug = repoSlug(project.repo_url);
    return slug !== null && SHOWCASE_SLUGS.includes(slug);
  });
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />
      <main id="main-content" tabIndex={-1} lang={language} className="relative z-10 max-w-6xl mx-auto px-6 py-12">
        <div className="flex items-center gap-2 mb-8 text-xs font-mono">
          <Link href="/" className="inline-flex min-h-11 min-w-11 items-center rounded text-neutral-400 hover:text-white focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-300">{copy.home}</Link>
          <span aria-hidden="true" className="text-neutral-500">/</span><span className="text-neutral-300">showcase</span>
        </div>
        <ServicesHero language={language} />
        <section aria-labelledby="selected-services" className="mb-12">
          <h2 id="selected-services" className="scroll-mt-24 lg:scroll-mt-32 text-xl font-semibold tracking-tight mb-5">{copy.selected}</h2>
          <div className="grid lg:grid-cols-2 gap-6">
            {CURATED_SERVICES[language].map((service, index) => <CuratedCard key={service.id} service={service} language={language} index={index} />)}
          </div>
        </section>
        <RepositoryCatalog language={language} shown={new Set(showcased.map(project => repoSlug(project.repo_url) ?? ""))} />
        <section aria-labelledby="project-catalog">
          <h2 id="project-catalog" className="text-lg font-medium">{copy.catalog}</h2>
          <p className="text-neutral-300 text-sm mt-2 mb-3">{copy.catalogIntro}{showcased.some(project => !project.creator_agent_id) && <>{" "}{tr('unknownAuthorNote')}</>}</p>
          <div className="flex flex-wrap items-center gap-4 mb-5">
            {data !== null && <span className="text-sm text-neutral-400">{showcased.length} {copy.apps}</span>}
            <FreshnessBadge lastUpdated={lastUpdated} error={error} onRetry={refetch} />
          </div>
          <ProjectCatalog projects={showcased} loading={loading} error={error} language={language} />
        </section>
        <VisitorHint />
      </main>
    </div>
  );
}
