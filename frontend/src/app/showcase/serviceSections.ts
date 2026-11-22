import type { Project } from "@/lib/api";
import type { CuratedService } from "./curatedServices";
import { repoSlug } from "./descriptionText";

export interface ServiceSections {
  liveCurated: readonly CuratedService[];
  liveOther: Project[];
  archive: Project[];
}

/**
 * Splits catalog projects by the backend health probe. Before the backend reports
 * `service_live` on any item, the curated services stay listed so the page is not empty.
 */
export function splitServices(projects: readonly Project[], curated: readonly CuratedService[]): ServiceSections {
  const probed = projects.some(project => typeof project.service_live === "boolean");
  const curatedIds = new Set(curated.map(service => service.id));
  if (!probed) {
    return { liveCurated: curated, liveOther: [], archive: projects.filter(project => !curatedIds.has(repoSlug(project.repo_url) ?? "")) };
  }
  const live = projects.filter(project => project.service_live === true);
  const liveSlugs = new Set(live.map(project => repoSlug(project.repo_url) ?? ""));
  const liveCurated = curated.filter(service => liveSlugs.has(service.id));
  const shownCurated = new Set(liveCurated.map(service => service.id));
  return {
    liveCurated,
    liveOther: live.filter(project => !curatedIds.has(repoSlug(project.repo_url) ?? "")),
    archive: projects.filter(project => project.service_live !== true && !shownCurated.has(repoSlug(project.repo_url) ?? "")),
  };
}
