"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";
import { SHOWCASE_COPY } from "@/app/showcase/curatedServices";
import { curatedHomeService, homeServiceUrl, parseHomeProjects, visibleHomeServices, type HomeServiceProject, type HomeServicesData } from "@/lib/homeServices";

/** Keep the homepage introduction focused on a useful service and a separate agent entry. */
export function HomeServicesIntroduction() {
  const text = useTranslations(WORKSPACE_MESSAGES);
  return (
    <>
      <h1 className="fade-in-d1 text-balance text-[clamp(2.5rem,5.5vw,4.5rem)] font-bold tracking-[-0.03em] leading-[1.05]">
        <span className="block text-white">{text('homeHeadline')}</span>{" "}
        <span className="block gradient-text-animated bg-gradient-to-r from-violet-400 via-indigo-400 to-cyan-400 bg-clip-text text-transparent">
          {text('homeHeadlineAgents')}
        </span>
      </h1>
      <p className="fade-in-d2 text-neutral-400 text-lg leading-relaxed max-w-xl font-light">{text('homeHeroIntro')}</p>
      <div className="fade-in-d3 grid sm:grid-cols-2 gap-4">
        <section aria-labelledby="home-people" className="flex flex-col items-start gap-3 rounded-xl border border-neutral-800/60 bg-neutral-900/60 p-5">
          <h2 id="home-people" className="text-base font-semibold text-white">{text('homePeople')}</h2>
          <p className="text-sm text-neutral-400 leading-relaxed">{text('homePeopleBody')}</p>
          <Link href="/showcase" className="mt-auto inline-flex min-h-11 items-center justify-center px-5 py-3 rounded-xl text-sm font-medium font-mono bg-white text-black hover:bg-neutral-200 transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{text('homeBrowseServices')}</Link>
        </section>
        <section aria-labelledby="home-agent-owners" className="flex flex-col items-start gap-3 rounded-xl border border-violet-500/20 bg-violet-500/10 p-5">
          <h2 id="home-agent-owners" className="text-base font-semibold text-white">{text('homeAgentOwners')}</h2>
          <p className="text-sm text-neutral-400 leading-relaxed">{text('homeAgentOwnersBody')}</p>
          <Link href="/workspace#connect-agent" className="mt-auto inline-flex min-h-11 items-center justify-center px-5 py-3 rounded-xl text-sm font-medium font-mono text-violet-300 bg-violet-500/10 border border-violet-500/20 hover:bg-violet-500/20 hover:border-violet-500/30 transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{text('connect')}</Link>
        </section>
      </div>
    </>
  );
}

function HomeServiceCard({ project }: { project: HomeServiceProject }) {
  const { locale } = useLocale();
  const text = useTranslations(WORKSPACE_MESSAGES);
  const known = curatedHomeService(project, locale);
  const copy = SHOWCASE_COPY[locale];
  const url = homeServiceUrl(known?.startUrl ?? project.deploy_url);
  return (
    <article className="flex min-w-0 flex-col overflow-hidden rounded-2xl border border-violet-500/20 bg-gradient-to-b from-violet-950/25 to-neutral-900/50 p-6">
      <div className="mb-5 flex items-center justify-between gap-3"><span className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-violet-400/25 bg-violet-400/10 text-lg text-violet-200" aria-hidden="true">◇</span><span className="text-xs text-neutral-300">{known?.category ?? text('homeServiceProject')}</span></div>
      <h3 className="text-xl font-semibold tracking-tight text-white [overflow-wrap:anywhere]">{known?.name ?? project.title}</h3>
      <p className="mt-3 text-sm leading-relaxed text-neutral-300 [overflow-wrap:anywhere]">{known?.purpose ?? (project.description || copy.noDescription)}</p>
      {known && <p className="mt-4 text-xs leading-relaxed text-neutral-300">{known.limits}</p>}
      {known && <details className="mt-4 text-xs text-neutral-300"><summary className="cursor-pointer py-2 focus-visible:outline-2 focus-visible:outline-violet-300">{copy.details}</summary><p className="mt-2 leading-relaxed">{known.start}</p><p className="mt-2 leading-relaxed">{known.checked}</p></details>}
      <div className="mt-auto flex flex-wrap items-center gap-3 pt-6">
        {url && <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center justify-center rounded-xl bg-violet-300 px-4 py-2 text-sm font-semibold text-violet-950 transition-colors hover:bg-violet-200 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{known?.startLabel ?? text('homeServiceOpen')}</a>}
        <Link href={`/projects/${encodeURIComponent(project.id)}`} className="inline-flex min-h-11 items-center rounded px-1 text-sm text-neutral-300 underline underline-offset-4 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{text('homeServiceDetails')}</Link>
      </div>
    </article>
  );
}

/** Refresh this independent catalog without coupling service visibility to platform counters. */
export function HomeServices({ initialData }: { initialData?: HomeServicesData }) {
  const text = useTranslations(WORKSPACE_MESSAGES);
  const [data, setData] = useState<HomeServicesData>(initialData ?? { status: "loading", projects: [] });
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timer = setTimeout(() => controller.abort(), 5000);
    fetch(`${API_URL}/api/v1/projects?limit=200`, { signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error("Project catalog unavailable"); return response.json(); })
      .then(value => { const projects = parseHomeProjects(value); if (active) setData({ status: "ready", projects }); })
      .catch(() => { if (active) setData(previous => ({ status: "error", projects: previous.projects })); })
      .finally(() => clearTimeout(timer));
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, []);
  const projects = visibleHomeServices(data.projects);
  return (
    <section aria-labelledby="home-services-title" className="relative mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-12">
      <div className="mb-7 flex flex-wrap items-end justify-between gap-4"><div><p className="mb-3 font-mono text-xs tracking-widest text-violet-300">{text('homeServicesEyebrow')}</p><h2 id="home-services-title" className="text-3xl font-semibold tracking-tight">{text('homeServicesTitle')}</h2><p className="mt-3 max-w-2xl text-sm leading-relaxed text-neutral-400">{text('homeServicesIntro')}</p></div><Link href="/showcase" className="inline-flex min-h-11 items-center rounded px-1 text-sm text-violet-300 underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{text('homeServicesAll')}</Link></div>
      {data.status === "loading" && <p role="status" className="rounded-xl border border-neutral-800 p-5 text-neutral-300">{text('homeServicesLoading')}</p>}
      {data.status === "error" && <p role="status" className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/5 p-5 text-sm text-amber-100">{projects.length ? text('homeServicesStale') : text('homeServicesError')}</p>}
      {data.status === "ready" && projects.length === 0 && <p role="status" className="rounded-xl border border-neutral-800 p-5 text-neutral-300">{text('homeServicesEmpty')}</p>}
      {projects.length > 0 && <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">{projects.map(project => <HomeServiceCard key={project.id} project={project} />)}</div>}
      <p className="mt-5 text-xs leading-relaxed text-neutral-400">{text('homeServicesNote')} <Link href="/projects" className="underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-violet-300">{text('homeServicesHistory')}</Link></p>
    </section>
  );
}
