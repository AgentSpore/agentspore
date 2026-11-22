import type { Project } from "./api";
import type { Locale } from "./i18n/locale";
import { CURATED_SERVICES, type CuratedService } from "@/app/showcase/curatedServices";
import { isShowcaseHidden } from "@/app/showcase/serviceSections";

export type HomeServiceProject = Pick<Project, "id" | "title" | "description" | "status" | "repo_url" | "deploy_url" | "service_live">;
export interface HomeServicesData {
  status: "loading" | "ready" | "error";
  projects: HomeServiceProject[];
}

/** Allow public HTTPS links without silently repairing credentials or malformed schemes. */
export function homeServiceUrl(value: unknown): string | null {
  if (typeof value !== "string" || !/^https:\/\//i.test(value) || /[\s\\]/u.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function isCatalogRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function optionalCatalogText(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value !== "string") throw new Error("Invalid project catalog");
  return value;
}

function parseHomeProject(row: unknown): HomeServiceProject {
  if (!isCatalogRecord(row)) throw new Error("Invalid project catalog");
  const { id, title, status } = row;
  if (typeof id !== "string" || !/^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(id)) throw new Error("Invalid project catalog");
  if (typeof title !== "string" || !title.trim()) throw new Error("Invalid project catalog");
  if (typeof status !== "string") throw new Error("Invalid project catalog");
  return {
    id, title, status,
    ...(typeof row.service_live === "boolean" && { service_live: row.service_live }),
    description: optionalCatalogText(row.description) ?? "",
    repo_url: optionalCatalogText(row.repo_url),
    deploy_url: optionalCatalogText(row.deploy_url),
  };
}

/** Validate the catalog boundary before a malformed response can appear as an empty result. */
export function parseHomeProjects(value: unknown): HomeServiceProject[] {
  if (!Array.isArray(value)) throw new Error("Invalid project catalog");
  return value.map(parseHomeProject);
}

/**
 * Only running services with safe application links belong on the homepage: the health probe
 * decides once any item reports it, else (backend rollout) the deployed status does.
 */
export function visibleHomeServices(projects: HomeServiceProject[]): HomeServiceProject[] {
  const probed = projects.some(project => typeof project.service_live === "boolean");
  const running = (project: HomeServiceProject) => probed ? project.service_live === true : project.status === "deployed";
  return projects.filter(project => !isShowcaseHidden(project.repo_url) && running(project) && homeServiceUrl(project.deploy_url)).slice(0, 6);
}

/** Reuse instructions only for an exact AgentSpore repository, never a similar external name. */
export function curatedHomeService(project: HomeServiceProject, locale: Locale): CuratedService | undefined {
  const source = homeServiceUrl(project.repo_url);
  if (!source) return undefined;
  const repo = new URL(source);
  if (repo.hostname !== "github.com" || repo.search || repo.hash) return undefined;
  return CURATED_SERVICES[locale].find(service => repo.pathname.replace(/\/$/, "").toLowerCase() === `/agentspore/${service.id}`);
}
