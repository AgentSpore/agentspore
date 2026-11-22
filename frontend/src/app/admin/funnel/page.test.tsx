// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import AdminFunnelPage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

vi.mock("@/components/Header", () => ({ Header: () => null }));

const fetchMock = vi.fn();
const json = (status: number, body: unknown = {}) => ({ ok: status < 400, status, json: async () => body });

const store = new Map<string, string>();
beforeEach(() => {
  store.clear();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, value); },
    removeItem: (key: string) => { store.delete(key); },
  });
  store.set("access_token", "t");
  store.set("refresh_token", "r");
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const REPORT = {
  days: 7,
  rows: [
    { day: "2026-11-01", step: "visit", source: "x", count: 8 },
    { day: "2026-11-01", step: "visit", source: "direct", count: 2 },
    { day: "2026-11-01", step: "skill_fetch", source: "x", count: 5 },
  ],
  totals: { visit: 10, skill_fetch: 5 },
};

it("renders daily counts and totals without a conversion claim", async () => {
  fetchMock.mockResolvedValue(json(200, REPORT));
  render(<AdminFunnelPage />);
  const table = await screen.findByRole("table");
  expect(within(table).getByRole("columnheader", { name: "Visit" })).toBeTruthy();
  const row = within(table).getByRole("row", { name: /2026-11-01/ });
  expect(within(row).getAllByRole("cell").map(c => c.textContent)).toEqual(["10", "5", "0", "0", "0", "0"]);
  expect(within(within(table).getByRole("row", { name: /Total/ })).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["10", "5", "0", "0", "0", "0"]);
  expect(table.textContent).not.toMatch(/conversion|%/i);
  expect(fetchMock.mock.calls[0]?.[0]).toMatch(/\/api\/v1\/admin\/funnel\?days=7$/);
});

it("reloads with the selected period", async () => {
  fetchMock.mockResolvedValue(json(200, REPORT));
  render(<AdminFunnelPage />);
  await screen.findByRole("table");
  fireEvent.click(screen.getByRole("button", { name: "90 days" }));
  await screen.findByRole("table");
  expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("days=90"))).toBe(true);
});

it("shows the sign-in state on 401", async () => {
  fetchMock.mockResolvedValue(json(401));
  render(<AdminFunnelPage />);
  expect(await screen.findByText("Sign in required")).toBeTruthy();
});

it("shows the administrators-only state on 403", async () => {
  fetchMock.mockResolvedValue(json(403));
  render(<AdminFunnelPage />);
  expect(await screen.findByText("Administrators only")).toBeTruthy();
});

it("shows empty and error states", async () => {
  fetchMock.mockResolvedValueOnce(json(200, { days: 7, rows: [], totals: {} }));
  render(<AdminFunnelPage />);
  expect(await screen.findByText("No funnel data for this period")).toBeTruthy();
  cleanup();
  fetchMock.mockResolvedValue(json(500));
  render(<AdminFunnelPage />);
  expect(await screen.findByRole("button", { name: /retry/i })).toBeTruthy();
});

it("signed-out visitor sees the sign-in state without a request", async () => {
  store.clear();
  render(<AdminFunnelPage />);
  expect(await screen.findByText("Sign in required")).toBeTruthy();
  expect(fetchMock).not.toHaveBeenCalled();
});


it("preserves mixed-unit counts above the preceding step and unknown steps", async () => {
  fetchMock.mockResolvedValue(json(200, {
    days: 7,
    rows: [
      { day: "2026-11-02", step: "visit", source: "direct", count: 2 },
      { day: "2026-11-02", step: "skill_fetch", source: "direct", count: 9 },
      { day: "2026-11-02", step: "future_step", source: "direct", count: 17 },
    ],
    totals: { visit: 2, skill_fetch: 9, future_step: 17 },
  }));
  render(<AdminFunnelPage />);
  const table = await screen.findByRole("table");
  expect(within(table).getByRole("columnheader", { name: "future_step" })).toBeTruthy();
  for (const name of [/2026-11-02/, /Total/]) {
    const row = within(table).getByRole("row", { name });
    expect(within(row).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["2", "9", "0", "0", "0", "0", "17"]);
  }
  expect(table.textContent).not.toMatch(/conversion|%/i);
});

it.each([
  ["en", /different units/, /not one cohort or a count of people/, /completeness and production coverage are unverified/, /automation/, /Missing observations do not mean no activity/],
  ["ru", /разные единицы/, /не одна когорта и\s+не число людей/, /Полнота сбора и\s+охват рабочей среды не проверены/, /автоматизации/, /Отсутствие наблюдений не означает отсутствия активности/],
] as const)("explains counting and collection limits in %s, including empty reports", async (locale, units, population, coverage, automation, missing) => {
  fetchMock.mockResolvedValue(json(200, { days: 7, rows: [], totals: {} }));
  render(<LocaleProvider initialLocale={locale}><AdminFunnelPage /></LocaleProvider>);
  await screen.findByRole("alert");
  for (const text of [units, population, coverage, automation, missing]) {
    expect(screen.getByText(text)).toBeTruthy();
  }
});

const WITH_PAGES = {
  ...REPORT,
  rows: [...REPORT.rows, { day: "2026-11-01", step: "visit_automated", source: "x", count: 40 }],
  totals: { ...REPORT.totals, visit_automated: 40 },
  pages: [
    { day: "2026-11-01", page: "home", visitors: 6 },
    { day: "2026-10-31", page: "home", visitors: 4 },
    { day: "2026-11-01", page: "hosted-agents", visitors: 2 },
    { day: "2026-11-01", page: "newthing", visitors: 1 },
  ],
  page_visitor_days: { home: 10, "hosted-agents": 2, newthing: 1 },
};

it.each([
  ["en", "Sections", "Home", "Hosted agents", "Visitor-days", /one person on two days counts twice/i, "Automated visits"],
  ["ru", "Разделы", "Главная", "Размещённые агенты", "Посетителе-дни", /один человек в два дня считается дважды/i, "Автоматические заходы"],
] as const)("renders the sections table in %s with visitor-days total", async (locale, title, home, hosted, total, note, automated) => {
  fetchMock.mockResolvedValue(json(200, WITH_PAGES));
  render(<LocaleProvider initialLocale={locale}><AdminFunnelPage /></LocaleProvider>);
  await screen.findAllByRole("table");
  expect(screen.getByRole("heading", { name: title })).toBeTruthy();
  const table = screen.getByRole("table", { name: title });
  expect(within(within(table).getByRole("row", { name: new RegExp(home) })).getAllByRole("cell").map(c => c.textContent)).toEqual(["6", "4", "10"]);
  expect(within(table).getByRole("columnheader", { name: total })).toBeTruthy();
  expect(within(table).getByRole("row", { name: /hosted|Размещ/i })).toBeTruthy();
  expect(within(table).getByRole("rowheader", { name: hosted })).toBeTruthy();
  expect(within(table).getByRole("rowheader", { name: "newthing" })).toBeTruthy();
  expect(screen.getByText(note)).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: automated })).toBeTruthy();
});

it("hides the sections table when the backend sends no pages", async () => {
  fetchMock.mockResolvedValue(json(200, REPORT));
  render(<AdminFunnelPage />);
  await screen.findByRole("table");
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.queryByRole("heading", { name: "Sections" })).toBeNull();
});
