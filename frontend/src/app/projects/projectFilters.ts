import type { Project } from "@/lib/api";

export type TimeFilter = "all" | "24h" | "7d" | "30d";
export type SortKey = "newest" | "stars" | "votes";
export interface ProjectFilters { search: string; category: string; status: string; time: TimeFilter; sort: SortKey }

export const DEFAULT_FILTERS: ProjectFilters = { search: "", category: "all", status: "all", time: "all", sort: "newest" };

const TIME_WINDOW_MS: Record<Exclude<TimeFilter, "all">, number> = { "24h": 864e5, "7d": 7 * 864e5, "30d": 30 * 864e5 };

/** Number of non-default filter groups, shown on the collapsed toggle. */
export function activeFilterCount(f: ProjectFilters): number {
  return [f.status !== "all", f.time !== "all", f.sort !== "newest", f.category !== "all"].filter(Boolean).length;
}

function matchesSearch(p: Project, search: string): boolean {
  const q = search.toLowerCase();
  return !q || [p.title, p.description, p.agent_name ?? ""].some(text => text.toLowerCase().includes(q));
}

function inTimeWindow(p: Project, time: TimeFilter, reference: number): boolean {
  if (time === "all") return true;
  const created = Date.parse(p.created_at);
  return Number.isNaN(created) || created >= reference - TIME_WINDOW_MS[time];
}

function compareBy(sort: SortKey) {
  return (a: Project, b: Project): number => {
    if (sort === "stars") return (b.github_stars ?? 0) - (a.github_stars ?? 0);
    if (sort === "votes") return (b.votes_up - b.votes_down) - (a.votes_up - a.votes_down);
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  };
}

/** Client-side search, time window and sort; category and status are applied by the API query. */
export function filterProjects(projects: Project[], f: ProjectFilters, reference: number): Project[] {
  return projects.filter(p => matchesSearch(p, f.search) && inTimeWindow(p, f.time, reference)).sort(compareBy(f.sort));
}
