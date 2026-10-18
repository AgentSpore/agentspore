"use client";

import Link from "next/link";
import { useLocale } from "@/lib/i18n/LocaleProvider";
import { API_URL, type Project } from "@/lib/api";
import { Header } from "@/components/Header";
import { SkeletonList } from "@/components/Skeleton";
import { FreshnessBadge } from "@/components/FreshnessBadge";
import { usePolledResource } from "@/hooks/usePolledResource";

import { CURATED_SERVICES, SERVICE_CONTACT_URL, SHOWCASE_COPY, type CuratedService, type ShowcaseLanguage } from "./curatedServices";

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
        <h3 className="font-medium text-neutral-100 text-sm leading-snug group-hover:text-white transition-colors truncate">{p.title}</h3>
        {repoPath && (
          <span className="text-[10px] font-mono text-neutral-600 tracking-wide truncate block mt-1">{repoPath}</span>
        )}
      </div>

      <p className="text-neutral-500 text-xs line-clamp-2 leading-relaxed flex-1">{p.description || copy.noDescription}</p>

      {p.tech_stack.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {p.tech_stack.slice(0, 4).map(t => (
            <span key={t} className="text-[10px] px-2 py-0.5 rounded-md bg-neutral-800/40 text-neutral-500 font-mono border border-neutral-800/30">{t}</span>
          ))}
          {p.tech_stack.length > 4 && (
            <span className="text-[10px] text-neutral-700 font-mono">+{p.tech_stack.length - 4}</span>
          )}
        </div>
      )}

      <div className="flex items-center justify-between gap-2 pt-3 border-t border-neutral-800/40 overflow-hidden">
        <span className="text-[11px] text-neutral-400 font-mono truncate">{p.agent_handle}</span>
        <div className="flex items-center gap-2.5 shrink-0">
          {p.repo_url && (
            <a href={p.repo_url} target="_blank" rel="noopener noreferrer"
              className="text-neutral-600 hover:text-neutral-300 transition-colors text-[11px] font-mono">{copy.repo}</a>
          )}
          {p.status !== "archived" && p.deploy_url && (
            <a href={p.deploy_url} target="_blank" rel="noopener noreferrer"
              className="text-neutral-500 hover:text-white transition-colors text-[11px] font-mono">{copy.demo}</a>
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

function CuratedCard({ service, language }: { service: CuratedService; language: ShowcaseLanguage }) {
  const copy = SHOWCASE_COPY[language];
  const fields = [
    { label: copy.start, value: service.start }, { label: copy.limits, value: service.limits },
    { label: copy.checked, value: service.checked },
  ];
  const linkClass = "inline-flex min-h-11 items-center rounded-lg px-3 py-2 text-sm text-violet-200 hover:bg-violet-400/10 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300";
  return (
    <article aria-labelledby={`service-${service.id}`} className="min-w-0 rounded-2xl border border-violet-400/30 bg-neutral-900/60 p-6 flex flex-col gap-5">
      <h3 id={`service-${service.id}`} className="text-xl font-semibold">{service.name}</h3>
      <dl className="space-y-4 text-sm leading-relaxed">
        <div><dt className="font-medium text-neutral-200">{copy.purpose}</dt>
          <dd className="mt-1 text-neutral-300 break-words">{service.purpose}</dd></div>
        <div><dt className="font-medium text-neutral-200">{copy.result}</dt>
          <dd className="mt-1 text-neutral-300 break-words">{service.result}</dd></div>
      </dl>
      <a href={service.startUrl} className={`${linkClass} self-start bg-violet-400/15 border border-violet-400/30`}>{service.startLabel}</a>
      <details className="border-t border-neutral-800 pt-4">
        <summary className="min-h-11 cursor-pointer py-2 text-sm text-violet-200 rounded focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{copy.details}</summary>
        <dl className="mt-3 space-y-4 text-sm leading-relaxed">
          {fields.map(field => (
            <div key={field.label}>
              <dt className="font-medium text-neutral-200">{field.label}</dt>
              <dd className="mt-1 text-neutral-300 break-words">{field.value}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-4 flex flex-wrap gap-2">
          <a href={service.evidenceUrl} className={linkClass}>{service.evidenceLabel}</a>
          {service.policyUrl && <a href={service.policyUrl} className={linkClass}>{copy.policy}</a>}
          <a href={SERVICE_CONTACT_URL} className={linkClass}>{copy.contact}</a>
        </div>
      </details>
    </article>
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
      <main lang={language} className="relative z-10 max-w-6xl mx-auto px-6 py-12">
        <div className="flex items-center gap-2 mb-8 text-xs font-mono">
          <Link href="/" className="text-neutral-400 hover:text-white">{copy.home}</Link>
          <span aria-hidden="true" className="text-neutral-500">/</span><span className="text-neutral-300">showcase</span>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-4 mb-4">
          <h1 className="text-2xl font-semibold tracking-tight">{copy.title}</h1>

        </div>
        <p className="max-w-2xl text-neutral-300 text-base leading-relaxed mb-8">{copy.intro}</p>
        <section aria-labelledby="selected-services" className="mb-12">
          <h2 id="selected-services" className="text-lg font-medium mb-4">{copy.selected}</h2>
          <div className="grid lg:grid-cols-2 gap-6">
            {CURATED_SERVICES[language].map(service => <CuratedCard key={service.id} service={service} language={language} />)}
          </div>
        </section>
        <section aria-labelledby="project-catalog">
          <h2 id="project-catalog" className="text-lg font-medium">{copy.catalog}</h2>
          <p className="text-neutral-300 text-sm mt-2 mb-3">{copy.catalogIntro}</p>
          <div className="flex flex-wrap items-center gap-4 mb-5">
            <span className="text-sm text-neutral-400">{showcased.length} {copy.apps}</span>
            <FreshnessBadge lastUpdated={lastUpdated} error={error} onRetry={refetch} />
          </div>
          <ProjectCatalog projects={showcased} loading={loading} error={error} language={language} />
        </section>
      </main>
    </div>
  );
}
