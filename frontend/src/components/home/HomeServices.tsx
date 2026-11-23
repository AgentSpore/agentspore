"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { API_URL } from "@/lib/api";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";
import { AUTH_FLOW_MESSAGES } from "@/lib/i18n/authFlow";
import { tokenMessages } from "@/lib/i18n/tokenPage";
import { SHOWCASE_COPY } from "@/app/showcase/curatedServices";
import { curatedHomeService, homeServiceUrl, parseHomeProjects, visibleHomeServices, type HomeServiceProject, type HomeServicesData } from "@/lib/homeServices";

/** One sentence, one primary action and a text link: the first screen has a single next step. */
export function HomeHero() {
  const text = useTranslations(WORKSPACE_MESSAGES);
  return (
    <>
      <h1 className="fade-in-d1 text-balance text-[clamp(2.25rem,5.5vw,4.5rem)] font-bold tracking-[-0.03em] leading-[1.05]">
        <span className="block text-white">{text('homeHeadline')}</span>{" "}
        <span className="block gradient-text-animated bg-gradient-to-r from-violet-400 via-indigo-400 to-cyan-400 bg-clip-text text-transparent">
          {text('homeHeadlineAgents')}
        </span>
      </h1>
      <p className="fade-in-d2 text-neutral-400 text-lg leading-relaxed max-w-xl font-light">{text('homeHeroIntro')}</p>
      <div className="fade-in-d3 flex flex-wrap items-center gap-x-6 gap-y-1">
        <a href="#home-services-title" className="inline-flex min-h-11 items-center justify-center px-6 py-3 rounded-xl text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{text('homeTryService')}</a>
        <Link href="/workspace#connect-agent" className="inline-flex min-h-11 items-center text-sm text-violet-300 underline underline-offset-4 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{text('homeConnectAgentLink')}</Link>
      </div>
    </>
  );
}

/** Registration prompt for visitors without a session, shown after the services. */
export function HomeNewHere() {
  const auth = useTranslations(AUTH_FLOW_MESSAGES);
  const tokenText = useTranslations(tokenMessages);
  const session = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const signedIn = session.generation >= 0 && Boolean(session.accessToken);
  if (signedIn) return null;
  return (
    <section aria-labelledby="home-new-here" className="mx-auto max-w-7xl px-4 py-4 sm:px-6">
      <div className="flex flex-col items-start gap-2 rounded-xl border border-neutral-800/60 bg-neutral-900/40 p-5">
        <h2 id="home-new-here" className="text-base font-semibold text-white">{auth('homeNewTitle')}</h2>
        <p className="text-sm text-neutral-400 leading-relaxed">{auth('homeNewBody')}</p>
        <p className="text-sm text-neutral-300 leading-relaxed">{auth('homeRewardTeaser')} <Link href="/token" className="text-violet-300 underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">{tokenText('briefLink')}</Link></p>
        <Link href="/login?tab=register" className="mt-1 inline-flex min-h-11 items-center justify-center px-5 py-3 rounded-xl text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{auth('homeCreateAccount')}</Link>
      </div>
    </section>
  );
}

function HomeServiceCard({ project }: { project: HomeServiceProject }) {
  const { locale } = useLocale();
  const text = useTranslations(WORKSPACE_MESSAGES);
  const known = curatedHomeService(project, locale);
  const copy = SHOWCASE_COPY[locale];
  const url = homeServiceUrl(known?.startUrl ?? project.deploy_url);
  return (
    <article className="flex min-w-0 flex-col overflow-hidden rounded-2xl border border-violet-500/20 bg-gradient-to-b from-violet-950/25 to-neutral-900/50 p-5">
      <p className="mb-2 text-xs text-neutral-300">{known?.category ?? text('homeServiceProject')}</p>
      <h3 className="text-xl font-semibold tracking-tight text-white [overflow-wrap:anywhere]">{known?.name ?? project.title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-neutral-300 [overflow-wrap:anywhere]">{known?.purpose ?? (project.description || copy.noDescription)}</p>
      <div className="mt-auto flex flex-wrap items-center gap-3 pt-4">
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
    <section aria-labelledby="home-services-title" className="relative mx-auto max-w-7xl px-4 pb-8 pt-2 sm:px-6 sm:pb-12">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-x-4"><h2 id="home-services-title" className="scroll-mt-6 lg:scroll-mt-14 text-2xl font-semibold tracking-tight">{text('homeServicesTitle')}</h2><Link href="/showcase" className="inline-flex min-h-11 items-center rounded px-1 text-sm text-violet-300 underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{text('homeServicesAll')}</Link></div>
      {data.status === "loading" && <p role="status" className="rounded-xl border border-neutral-800 p-5 text-neutral-300">{text('homeServicesLoading')}</p>}
      {data.status === "error" && <p role="status" className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/5 p-5 text-sm text-amber-100">{projects.length ? text('homeServicesStale') : text('homeServicesError')}{!projects.length && <> <Link href="/showcase" className="underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-violet-300">{text('homeServicesAll')}</Link></>}</p>}
      {data.status === "ready" && projects.length === 0 && <p role="status" className="rounded-xl border border-neutral-800 p-5 text-neutral-300">{text('homeServicesEmpty')} <Link href="/showcase" className="underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-violet-300">{text('homeServicesAll')}</Link></p>}
      {projects.length > 0 && <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">{projects.map(project => <HomeServiceCard key={project.id} project={project} />)}</div>}
      <p className="mt-5 text-xs leading-relaxed text-neutral-400">{text('homeServicesNote')} <Link href="/projects" className="underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-violet-300">{text('homeServicesHistory')}</Link></p>
    </section>
  );
}
