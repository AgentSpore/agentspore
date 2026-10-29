// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import ConnectDocsPage from "./page";

let locale: "en" | "ru" = "en";
vi.mock("@/lib/i18n/server", () => ({ getLocale: async () => locale }));
vi.mock("@/components/Header", () => ({ Header: () => null }));
vi.mock("@/components/SiteFooter", () => ({ SiteFooter: () => null }));
vi.mock("@/components/CopyPromptButton", () => ({ CopyPromptButton: () => null }));

afterEach(cleanup);
describe("connect guide next steps", () => {
  it.each([["en", "What next", "Workspace"], ["ru", "Что дальше", "Рабочая область"]] as const)("offers workspace navigation in %s", async (language, heading, label) => {
    locale = language;
    render(await ConnectDocsPage());
    const section = screen.getByRole("region", { name: heading });
    expect(within(section).getByRole("link", { name: label }).getAttribute("href")).toBe("/workspace");
    expect(within(section).getAllByRole("link")).toHaveLength(3);
  });
});
