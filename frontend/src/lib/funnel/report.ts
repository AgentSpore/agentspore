/** Shape of `GET /api/v1/admin/funnel` (backend FunnelReport). */
export interface FunnelRow { day: string; step: string; source: string; count: number }
export interface FunnelReport { days: number; rows: FunnelRow[]; totals: Record<string, number> }

/** Known steps in funnel order; unknown steps from the server are appended after them. */
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
}

/** Day x step grid (sources summed, newest day first) with totals and step-to-step conversion. */
export function buildFunnelTable(report: FunnelReport): FunnelTable {
  const seen = new Set(report.rows.map(row => row.step));
  const known = FUNNEL_STEPS.filter(step => seen.has(step) || step in report.totals);
  const extra = [...seen].filter(step => !(FUNNEL_STEPS as readonly string[]).includes(step)).sort();
  const steps: string[] = [...known, ...extra];
  const byDay = new Map<string, Record<string, number>>();
  for (const row of report.rows) {
    const counts = byDay.get(row.day) ?? {};
    counts[row.step] = (counts[row.step] ?? 0) + row.count;
    byDay.set(row.day, counts);
  }
  const days = [...byDay.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([day, counts]) => ({ day, counts }));
  const totals = Object.fromEntries(steps.map(step => [step, report.totals[step] ?? 0]));
  const conversions = Object.fromEntries(steps.map((step, index) => [
    step, index === 0 ? null : conversion(totals[steps[index - 1] as string] ?? 0, totals[step] ?? 0),
  ]));
  return { steps, days, totals, conversions };
}
