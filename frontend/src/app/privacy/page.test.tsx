// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import PrivacyPage from "./page";
import { docsMessages } from "@/lib/i18n/docs";
import { SERVICE_CONTACT_URL } from "../showcase/curatedServices";

let locale: "en" | "ru" = "en";
vi.mock("@/lib/i18n/server", () => ({ getLocale: async () => locale }));
vi.mock("@/components/Header", () => ({ Header: () => null }));
vi.mock("@/components/SiteFooter", () => ({ SiteFooter: () => null }));

afterEach(cleanup);

it.each(["en", "ru"] as const)("lists server-side storage and the deletion contact in %s", async (language) => {
  locale = language;
  render(await PrivacyPage());
  const copy = docsMessages[language];
  expect(screen.getByRole("heading", { name: copy.pServerTitle })).toBeTruthy();
  for (const key of ["pServer1", "pServer2", "pServer3", "pServer4", "pServer5", "pServer6"] as const) expect(screen.getByText(copy[key])).toBeTruthy();
  expect(screen.getByRole("link", { name: copy.pServerContact }).getAttribute("href")).toBe(SERVICE_CONTACT_URL);
});
