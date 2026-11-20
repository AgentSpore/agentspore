// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { HomeFooter } from "./HomeFooter";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

afterEach(cleanup);

it.each(["en", "ru"] as const)("links the token page from the home footer in %s", locale => {
  render(<LocaleProvider initialLocale={locale}><HomeFooter /></LocaleProvider>);
  expect(screen.getByRole("link", { name: docsMessages[locale].fToken }).getAttribute("href")).toBe("/token");
});
