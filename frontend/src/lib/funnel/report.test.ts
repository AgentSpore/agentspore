import { describe, expect, it } from "vitest";
import { buildFunnelTable, buildSectionsTable, conversion, FUNNEL_STEPS } from "./report";

it("conversion is null, not NaN or Infinity, when the previous step is 0", () => {
  expect(conversion(0, 5)).toBeNull();
  expect(conversion(0, 0)).toBeNull();
  expect(conversion(10, 5)).toBe(0.5);
});

it("sums sources per day and step, newest day first, with step-to-step conversion", () => {
  const table = buildFunnelTable({
    days: 7,
    rows: [
      { day: "2026-11-01", step: "visit", source: "x", count: 6 },
      { day: "2026-11-01", step: "visit", source: "direct", count: 4 },
      { day: "2026-10-31", step: "visit", source: "x", count: 10 },
      { day: "2026-11-01", step: "skill_fetch", source: "x", count: 5 },
      { day: "2026-11-01", step: "project_first_push", source: "x", count: 5 },
      { day: "2026-11-01", step: "custom_step", source: "x", count: 1 },
    ],
    totals: { visit: 20, skill_fetch: 5, project_first_push: 5, custom_step: 1 },
  });
  expect(table.steps).toEqual([...FUNNEL_STEPS, "custom_step"]);
  expect(table.days.map(d => d.day)).toEqual(["2026-11-01", "2026-10-31"]);
  expect(table.days[0]?.counts.visit).toBe(10);
  expect(table.conversions.skill_fetch).toBe(0.25);
  expect(table.conversions.custom_step).toBeNull();
});

it("a missing step keeps its column; the next step is not compared with an earlier one", () => {
  const table = buildFunnelTable({
    days: 7,
    rows: [
      { day: "2026-11-01", step: "visit", source: "x", count: 10 },
      { day: "2026-11-01", step: "user_register", source: "x", count: 2 },
    ],
    totals: { visit: 10, user_register: 2 },
  });
  expect(table.steps.slice(0, 3)).toEqual(["visit", "skill_fetch", "user_register"]);
  expect(table.conversions.skill_fetch).toBe(0);
  expect(table.conversions.user_register).toBeNull();
});

it("handles an empty report", () => {
  const table = buildFunnelTable({ days: 7, rows: [], totals: {} });
  expect(table.steps).toEqual([...FUNNEL_STEPS]);
  expect(table.days).toEqual([]);
});

it("automated visits get their own column and never enter conversion", () => {
  const table = buildFunnelTable({
    days: 7,
    rows: [
      { day: "2026-11-01", step: "visit", source: "x", count: 10 },
      { day: "2026-11-01", step: "visit_automated", source: "x", count: 90 },
      { day: "2026-11-01", step: "skill_fetch", source: "x", count: 5 },
    ],
    totals: { visit: 10, visit_automated: 90, skill_fetch: 5 },
  });
  expect(table.steps).toEqual([...FUNNEL_STEPS, "visit_automated"]);
  expect(table.totals.visit_automated).toBe(90);
  expect(table.conversions.visit_automated).toBeNull();
  expect(table.conversions.skill_fetch).toBe(0.5);
});

describe("buildSectionsTable", () => {
  it("is null for an old backend without pages, or an empty list", () => {
    expect(buildSectionsTable({ days: 7, rows: [], totals: {} })).toBeNull();
    expect(buildSectionsTable({ days: 7, rows: [], totals: {}, pages: [] })).toBeNull();
    expect(buildFunnelTable({ days: 7, rows: [], totals: {} }).sections).toBeNull();
  });

  it("pivots sections by day, known order first, newest day first, with visitor-day totals", () => {
    const sections = buildSectionsTable({
      days: 7, rows: [], totals: {},
      pages: [
        { day: "2026-11-01", page: "zeta", visitors: 1 },
        { day: "2026-11-01", page: "blog", visitors: 3 },
        { day: "2026-10-31", page: "blog", visitors: 2 },
        { day: "2026-10-31", page: "home", visitors: 4 },
      ],
      page_visitor_days: { blog: 5, home: 4, zeta: 1 },
    });
    expect(sections?.pages).toEqual(["home", "blog", "zeta"]);
    expect(sections?.days.map(d => d.day)).toEqual(["2026-11-01", "2026-10-31"]);
    expect(sections?.days[1]?.visitors).toEqual({ blog: 2, home: 4 });
    expect(sections?.totals).toEqual({ home: 4, blog: 5, zeta: 1 });
  });

  it("falls back to summing the daily rows when page_visitor_days is absent", () => {
    const sections = buildSectionsTable({
      days: 7, rows: [], totals: {},
      pages: [{ day: "d1", page: "home", visitors: 2 }, { day: "d2", page: "home", visitors: 3 }],
    });
    expect(sections?.totals.home).toBe(5);
  });
});
