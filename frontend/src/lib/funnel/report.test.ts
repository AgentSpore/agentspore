import { expect, it } from "vitest";
import { buildFunnelTable, conversion } from "./report";

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
      { day: "2026-11-01", step: "custom_step", source: "x", count: 1 },
    ],
    totals: { visit: 20, skill_fetch: 5, custom_step: 1 },
  });
  expect(table.steps).toEqual(["visit", "skill_fetch", "custom_step"]);
  expect(table.days.map(d => d.day)).toEqual(["2026-11-01", "2026-10-31"]);
  expect(table.days[0]?.counts.visit).toBe(10);
  expect(table.conversions).toEqual({ visit: null, skill_fetch: 0.25, custom_step: 0.2 });
});

it("handles an empty report", () => {
  const table = buildFunnelTable({ days: 7, rows: [], totals: {} });
  expect(table.steps).toEqual([]);
  expect(table.days).toEqual([]);
});
