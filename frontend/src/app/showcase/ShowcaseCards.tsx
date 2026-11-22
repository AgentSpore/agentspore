import type { Project } from "@/lib/api";
import { SERVICE_CONTACT_URL, SHOWCASE_COPY, type CuratedService, type ShowcaseLanguage } from "./curatedServices";
import { descriptionInLanguage } from "./descriptionText";
import { ServiceMark, ServiceThumb } from "./ServiceThumb";

const FOCUS = "focus-visible:outline-[3px] focus-visible:outline-offset-4 focus-visible:outline-violet-300";
/** The primary link stretches over its card; every other control sits above it. */
const STRETCH = "after:absolute after:inset-0 after:content-['']";

export function CuratedCard({ service, language }: { service: CuratedService; language: ShowcaseLanguage }) {
  const copy = SHOWCASE_COPY[language];
  const fields = [
    { label: copy.start, value: service.start }, { label: copy.limits, value: service.limits },
    { label: copy.checked, value: service.checked },
  ];
  const linkClass = `relative z-10 inline-flex min-h-12 items-center rounded-lg px-3 py-2 text-sm text-violet-200 hover:bg-violet-400/10 ${FOCUS}`;
  return (
    <article aria-labelledby={`service-${service.id}`} className="group relative flex min-w-0 flex-col overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900/60 transition-colors hover:border-violet-300/40">
      <div className="flex flex-1 flex-col gap-5 p-6 sm:p-7">
        <div className="flex items-center gap-4">
          <ServiceThumb><ServiceMark id={service.id} className="h-9 w-9" /></ServiceThumb>
          <div className="min-w-0"><p className="mb-1 text-xs font-medium tracking-wide text-violet-300">{service.category}</p><h3 id={`service-${service.id}`} className="text-2xl font-semibold tracking-tight">{service.name}</h3></div>
        </div>
        <dl className="flex-1 space-y-4 text-sm leading-relaxed">
          <div><dt className="sr-only">{copy.purpose}</dt><dd className="break-words text-base leading-relaxed text-neutral-200">{service.purpose}</dd></div>
          <div><dt className="font-medium text-neutral-300">{copy.result}</dt><dd className="mt-1 break-words text-neutral-400">{service.result}</dd></div>
        </dl>
        <div className="flex flex-wrap items-center gap-2">
          <a data-card-link href={service.startUrl} className={`inline-flex min-h-12 items-center gap-2 rounded-lg bg-violet-300 px-3 py-2 text-sm font-semibold text-neutral-950 hover:bg-violet-200 ${FOCUS} ${STRETCH}`}>{service.startLabel}<span aria-hidden="true">↗</span></a>
          <a href={service.evidenceUrl} className={linkClass}>{service.evidenceLabel}</a>
        </div>
        <details className="relative z-10 border-t border-neutral-800 pt-2">
          <summary className={`min-h-12 cursor-pointer rounded py-3 text-sm text-neutral-300 hover:text-white ${FOCUS}`}>{copy.details}</summary>
          <dl className="mt-3 space-y-4 text-sm leading-relaxed">
            {fields.map(field => <div key={field.label}><dt className="font-medium text-neutral-200">{field.label}</dt><dd className="mt-1 break-words text-neutral-300">{field.value}</dd></div>)}
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

function primaryHref(p: Project): string | null {
  return p.status !== "archived" && p.deploy_url ? p.deploy_url : p.repo_url;
}

function cardDescription(p: Project, language: ShowcaseLanguage): string {
  const copy = SHOWCASE_COPY[language];
  if (!p.description?.trim()) return copy.noDescription;
  return descriptionInLanguage(p.description, language) ?? copy.otherLanguage;
}

export function ShowcaseCard({ project: p, index, language }: { project: Project; index: number; language: ShowcaseLanguage }) {
  const copy = SHOWCASE_COPY[language];
  const href = primaryHref(p);
  const repoPath = p.repo_url?.replace("https://github.com/", "") || "";
  const showRepoLink = Boolean(p.repo_url) && href !== p.repo_url;
  return (
    <article className="group project-card relative flex min-w-0 flex-col gap-3 overflow-hidden rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-5 backdrop-blur-sm transition-all duration-300 hover:border-neutral-700/60"
      style={{ animationDelay: `${index * 60}ms` }}>
      <div className="flex items-center gap-3">
        <ServiceThumb><span className="text-2xl font-semibold uppercase">{p.title.trim().charAt(0)}</span></ServiceThumb>
        <div className="min-w-0">
          <h3 className="whitespace-normal text-sm font-medium leading-snug text-neutral-100 [overflow-wrap:anywhere] group-hover:text-white">
            {href ? <a data-card-link href={href} target="_blank" rel="noopener noreferrer" className={`inline-flex min-h-11 items-center rounded ${FOCUS} ${STRETCH}`}>{p.title}</a> : p.title}
          </h3>
          {repoPath && <span className="mt-1 block whitespace-normal font-mono text-xs tracking-wide text-neutral-600 [overflow-wrap:anywhere]">{repoPath}</span>}
        </div>
      </div>
      <p className="line-clamp-2 flex-1 text-xs leading-relaxed text-neutral-500">{cardDescription(p, language)}</p>
      {p.tech_stack.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {p.tech_stack.slice(0, 4).map(t => (
            <span key={t} className="min-w-0 max-w-full whitespace-normal rounded-md border border-neutral-800/30 bg-neutral-800/40 px-2 py-0.5 font-mono text-xs text-neutral-500 [overflow-wrap:anywhere]">{t}</span>
          ))}
          {p.tech_stack.length > 4 && <span className="font-mono text-xs text-neutral-700">+{p.tech_stack.length - 4}</span>}
        </div>
      )}
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-neutral-800/40 pt-3">
        {p.creator_agent_id && <span className="min-w-0 max-w-full whitespace-normal font-mono text-xs text-neutral-400 [overflow-wrap:anywhere]">{p.agent_handle}</span>}
        {showRepoLink && p.repo_url && (
          <a href={p.repo_url} target="_blank" rel="noopener noreferrer"
            className={`type-label relative z-10 inline-flex min-h-11 min-w-11 items-center rounded text-xs text-neutral-600 transition-colors hover:text-neutral-300 ${FOCUS}`}>{copy.repo}</a>
        )}
      </div>
    </article>
  );
}
