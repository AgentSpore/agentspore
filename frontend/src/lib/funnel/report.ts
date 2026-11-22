/** Shape of `GET /api/v1/admin/funnel` (backend FunnelReport). */
export interface FunnelRow { day: string; step: string; source: string; count: number }
export interface FunnelPageRow { day: string; page: string; visitors: number }
export interface FunnelReport {
  days: number;
  rows: FunnelRow[];
  totals: Record<string, number>;
  /** Absent on a backend that predates section metrics. */
  pages?: FunnelPageRow[];
  /** Sum of daily unique visitors per section: one person on two days counts twice. */
  page_visitor_days?: Record<string, number>;
}

/** Site sections in display order; sections outside this list follow, sorted by key. */
export const SECTION_ORDER = [
  "home", "showcase", "agents", "projects", "blog", "docs", "teams", "hosted-agents",
  "signup", "login", "privacy", "chat", "start", "token",
] as const;

/** Reported but not part of the human funnel: shown as its own column, never in conversion. */
export const AUTOMATED_STEP = "visit_automated";

/** Canonical steps in funnel order; unknown steps from the server are appended after them. */
export const FUNNEL_STEPS = [
  "visit", "skill_fetch", "user_register", "agent_register", "agent_first_heartbeat", "project_first_push",
] as const;

/** Share of `current` relative to `previous`; null (no data) instead of dividing by zero. */
export function conversion(previous: number, current: number): number | null {
  return previous > 0 ? current / previous : null;
}

export interface FunnelTable {
  steps: string[];
  days: { day: string; counts: Record<string, number> }[];
  totals: Record<string, number>;
  conversions: Record<string, number | null>;
  sections: SectionsTable | null;
}

export interface SectionsTable {
  pages: string[];
  days: { day: string; visitors: Record<string, number> }[];
  /** Visitor-days per section, not distinct people. */
  totals: Record<string, number>;
}

/** Section x day grid (newest day first); null when the backend sent no section data. */
export function buildSectionsTable(report: FunnelReport): SectionsTable | null {
  const rows = report.pages ?? [];
  if (rows.length === 0) return null;
  const known = SECTION_ORDER as readonly string[];
  const keys = new Set(rows.map(row => row.page));
  const pages = [...known.filter(key => keys.has(key)), ...[...keys].filter(key => !known.includes(key)).sort()];
  const byDay = new Map<string, Record<string, number>>();
  const summed: Record<string, number> = {};
  for (const row of rows) {
    const visitors = byDay.get(row.day) ?? {};
    visitors[row.page] = (visitors[row.page] ?? 0) + row.visitors;
    byDay.set(row.day, visitors);
    summed[row.page] = (summed[row.page] ?? 0) + row.visitors;
  }
  const days = [...byDay.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([day, visitors]) => ({ day, visitors }));
  const totals = Object.fromEntries(pages.map(page => [page, report.page_visitor_days?.[page] ?? summed[page] ?? 0]));
  return { pages, days, totals };
}

/** Day x step grid (sources summed, newest day first) with totals and step-to-step conversion. */
export function buildFunnelTable(report: FunnelReport): FunnelTable {
  const seen = new Set(report.rows.map(row => row.step));
  const extra = [...seen].filter(step => !(FUNNEL_STEPS as readonly string[]).includes(step) && step !== AUTOMATED_STEP).sort();
  const automated = seen.has(AUTOMATED_STEP) || AUTOMATED_STEP in report.totals ? [AUTOMATED_STEP] : [];
  const steps: string[] = [...FUNNEL_STEPS, ...automated, ...extra];
  const byDay = new Map<string, Record<string, number>>();
  for (const row of report.rows) {
    const counts = byDay.get(row.day) ?? {};
    counts[row.step] = (counts[row.step] ?? 0) + row.count;
    byDay.set(row.day, counts);
  }
  const days = [...byDay.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([day, counts]) => ({ day, counts }));
  const totals = Object.fromEntries(steps.map(step => [step, report.totals[step] ?? 0]));
  const conversions = Object.fromEntries(steps.map(step => {
    const index = FUNNEL_STEPS.indexOf(step as (typeof FUNNEL_STEPS)[number]);
    return [step, index <= 0 ? null : conversion(totals[FUNNEL_STEPS[index - 1] as string] ?? 0, totals[step] ?? 0)];
  }));
  return { steps, days, totals, conversions, sections: buildSectionsTable(report) };
}
