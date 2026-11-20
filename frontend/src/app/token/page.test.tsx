// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import TokenPage from "./page";
import { ASPORE_MINT, NEVER_KEYS, RULE_KEYS, tokenMessages } from "@/lib/i18n/tokenPage";

let locale: "en" | "ru" = "en";
vi.mock("@/lib/i18n/server", () => ({ getLocale: async () => locale }));
vi.mock("@/components/Header", () => ({ Header: () => null }));
vi.mock("@/components/SiteFooter", () => ({ SiteFooter: () => null }));

afterEach(cleanup);

it.each(["en", "ru"] as const)("renders all sections, rules, risk and mint on the server in %s", async (language) => {
  locale = language;
  render(await TokenPage());
  const copy = tokenMessages[language];
  for (const title of [copy.whatTitle, copy.rulesTitle, copy.useTitle, copy.neverTitle]) expect(screen.getByRole("heading", { name: title })).toBeTruthy();
  for (const key of [...RULE_KEYS, ...NEVER_KEYS, "useRisk", "useNoPromise"] as const) expect(screen.getByText(copy[key])).toBeTruthy();
  expect(screen.getByText(ASPORE_MINT)).toBeTruthy();
});

it.each([["en", "10,000", "48 hours"], ["ru", "10 000", "48 часов"]] as const)("states the amount and the 48 hour review in %s", (language, amount, hours) => {
  const copy = tokenMessages[language];
  for (const text of [copy.briefTitle, copy.rule1]) expect(text).toContain(amount);
  for (const text of [copy.briefReview, copy.rule4]) expect(text).toContain(hours);
});

it.each(["en", "ru"] as const)("uses no em dash in %s copy", (language) => {
  expect(Object.values(tokenMessages[language]).filter((text) => text.includes("—"))).toEqual([]);
});

it.each(["en", "ru"] as const)("does not claim planned features or current uses in %s", (language) => {
  const all = Object.values(tokenMessages[language]).join(" ");
  expect(all).not.toMatch(/rental|arena|voting|deposits|аренд|арен|голосован|пополнен/i);
});
