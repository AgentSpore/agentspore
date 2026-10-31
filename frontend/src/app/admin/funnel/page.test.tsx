// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import AdminFunnelPage from "./page";

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

it("renders day x step counts, totals and conversion from the report", async () => {
  fetchMock.mockResolvedValue(json(200, REPORT));
  render(<AdminFunnelPage />);
  const table = await screen.findByRole("table");
  expect(within(table).getByRole("columnheader", { name: "Visit" })).toBeTruthy();
  const row = within(table).getByRole("row", { name: /2026-11-01/ });
  expect(within(row).getAllByRole("cell").map(c => c.textContent)).toEqual(["10", "5"]);
  expect(within(table).getByRole("row", { name: /Conversion/ }).textContent).toContain("50%");
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
