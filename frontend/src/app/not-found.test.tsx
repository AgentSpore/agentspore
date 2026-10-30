// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import NotFound from "./not-found";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";

vi.mock("next/navigation", () => ({ usePathname: () => "/synthetic-missing", useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(cleanup);

it.each(["en", "ru"] as const)("offers the connection guide without losing existing 404 destinations in %s", locale => {
  render(<LocaleProvider initialLocale={locale}><NotFound /></LocaleProvider>);
  const guide = screen.getByRole("main").querySelector('a[href="/docs/connect"]');
  expect(guide?.textContent).toBe(docsMessages[locale].fConnect);
  const destinations = screen.getAllByRole("link").map(link => link.getAttribute("href"));
  expect(destinations).toEqual(expect.arrayContaining(["/", "/dashboard", "/agents", "/projects", "/chat"]));
  expect(screen.getByRole("main").id).toBe("main-content");
  expect(screen.getByRole("main").tabIndex).toBe(-1);
});

it.each(["en", "ru"] as const)("shows a human heading instead of the terminal error string in %s", locale => {
  render(<LocaleProvider initialLocale={locale}><NotFound /></LocaleProvider>);
  expect(screen.getByRole("heading", { level: 1, name: docsMessages[locale].nfTitle })).toBeTruthy();
  expect(screen.queryByText("page_not_found")).toBeNull();
  expect(screen.getByRole("link", { name: docsMessages[locale].nfCatalog }).getAttribute("href")).toBe("/showcase");
});
