import type { Project } from "./api";
import type { Locale } from "./i18n/locale";
import { CURATED_SERVICES, type CuratedService } from "@/app/showcase/curatedServices";

export type HomeServiceProject = Pick<Project, "id" | "title" | "description" | "status" | "repo_url" | "deploy_url">;
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

/** Only actual deployed API entries with safe application links belong on the homepage. */
export function visibleHomeServices(projects: HomeServiceProject[]): HomeServiceProject[] {
  return projects.filter(project => project.status === "deployed" && homeServiceUrl(project.deploy_url)).slice(0, 6);
}

/** Reuse instructions only for an exact AgentSpore repository, never a similar external name. */
export function curatedHomeService(project: HomeServiceProject, locale: Locale): CuratedService | undefined {
  const source = homeServiceUrl(project.repo_url);
  if (!source) return undefined;
  const repo = new URL(source);
  if (repo.hostname !== "github.com" || repo.search || repo.hash) return undefined;
  return CURATED_SERVICES[locale].find(service => repo.pathname.replace(/\/$/, "").toLowerCase() === `/agentspore/${service.id}`);
}
