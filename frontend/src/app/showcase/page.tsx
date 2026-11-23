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

import { CURATED_SERVICES, SHOWCASE_COPY, type ShowcaseLanguage } from "./curatedServices";
import { repoSlug } from "./descriptionText";
import { CuratedCard, ShowcaseCard } from "./ShowcaseCards";
import { splitServices } from "./serviceSections";
import { REPOSITORY_CATALOG } from "./repositoryCatalog";
import { ServiceMark } from "./ServiceThumb";

const POLL_INTERVAL_MS = 30000;

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

async function fetchShowcaseProjects(): Promise<Project[]> {
  const r = await fetch(`${API_URL}/api/v1/projects?limit=200`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function ServicesHero({ language }: { language: ShowcaseLanguage }) {
  const copy = SHOWCASE_COPY[language];
  return (
    <section aria-labelledby="services-title" className="mb-8 grid gap-10 border-b border-neutral-800 pb-8 lg:mb-12 lg:pb-12 lg:grid-cols-[1.3fr_1fr] lg:items-center lg:gap-16">
      <div className="min-w-0">
        <p className="mb-5 type-label text-xs tracking-widest text-violet-300">{copy.eyebrow}</p>
        <h1 id="services-title" className="max-w-2xl text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">{copy.title}</h1>
        <p className="mt-6 max-w-xl text-base leading-relaxed text-neutral-300 sm:text-lg">{copy.intro}</p>
        <a href="#selected-services" className="mt-7 inline-flex min-h-12 items-center gap-3 rounded-xl bg-violet-300 px-5 py-3 text-sm font-semibold text-neutral-950 hover:bg-violet-200 focus-visible:outline-[3px] focus-visible:outline-offset-4 focus-visible:outline-violet-300">{copy.browse}<span aria-hidden="true">↓</span></a>
      </div>
      <div className="hidden rounded-2xl border border-neutral-800 bg-neutral-900/50 p-5 sm:p-7 lg:block">
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

function ProjectArchive({ projects, loading, error, language }: {
  projects: Project[]; loading: boolean; error: Error | null; language: ShowcaseLanguage;
}) {
  const copy = SHOWCASE_COPY[language];
  if (loading) return <SkeletonList items={6} />;
  if (!projects.length) return <p className="py-12 text-neutral-300 text-sm">{error ? copy.unavailable : copy.empty}</p>;
  return (
    <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
      {projects.map((project, index) => <ShowcaseCard key={project.id} project={project} index={index} language={language} mode="archive" />)}
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
  const { liveCurated, liveOther, archive } = splitServices(data ?? [], CURATED_SERVICES[language]);
  const shownSlugs = new Set([...liveCurated.map(service => service.id), ...[...liveOther, ...archive].map(project => repoSlug(project.repo_url) ?? "")]);
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />
      <main id="main-content" tabIndex={-1} lang={language} className="relative z-10 max-w-6xl mx-auto px-6 py-12">
        <div className="flex items-center gap-2 mb-8 text-xs type-label">
          <Link href="/" className="inline-flex min-h-11 min-w-11 items-center rounded text-neutral-400 hover:text-white focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-300">{copy.home}</Link>
          <span aria-hidden="true" className="text-neutral-500">/</span><span className="text-neutral-300">{copy.crumb}</span>
        </div>
        <ServicesHero language={language} />
        <section aria-labelledby="selected-services" className="mb-12">
          <h2 id="selected-services" className="scroll-mt-6 lg:scroll-mt-14 text-xl font-semibold tracking-tight mb-5">{copy.selected}</h2>
          {loading ? <SkeletonList items={4} /> : <div className="grid lg:grid-cols-2 gap-6">
            {liveCurated.map(service => <CuratedCard key={service.id} service={service} language={language} />)}
            {liveOther.map((project, index) => <ShowcaseCard key={project.id} project={project} index={index} language={language} mode="live" />)}
          </div>}
          {!loading && liveCurated.length + liveOther.length === 0 && <p role="status" className="py-6 text-sm text-neutral-300">{data === null ? copy.startUnavailable : copy.noneLive}</p>}
        </section>
        {!loading && <RepositoryCatalog language={language} shown={shownSlugs} />}
        <section aria-labelledby="project-archive">
          <h2 id="project-archive" className="text-lg font-medium">{copy.archive}</h2>
          <p className="text-neutral-300 text-sm mt-2 mb-3">{copy.archiveIntro}{archive.some(project => !project.creator_agent_id) && <>{" "}{tr('unknownAuthorNote')}</>}</p>
          <div className="flex flex-wrap items-center gap-4 mb-5">
            {data !== null && <span className="text-sm text-neutral-400">{archive.length} {copy.apps}</span>}
            <FreshnessBadge lastUpdated={lastUpdated} error={error} onRetry={refetch} />
          </div>
          <ProjectArchive projects={archive} loading={loading} error={error} language={language} />
        </section>
        <VisitorHint />
      </main>
    </div>
  );
}
