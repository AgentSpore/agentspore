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

it.each([["en", "useful services built by ai agents"], ["ru", "полезные сервисы, созданные ии-агентами"]] as const)("home footer tagline in %s is the product phrase", (locale, phrase) => {
  const { container } = render(<LocaleProvider initialLocale={locale}><HomeFooter /></LocaleProvider>);
  const text = (container.querySelector("footer")?.textContent ?? "").toLowerCase();
  expect(text).toContain(phrase);
  expect(text).not.toContain("autonomous startup forge");
  expect(text).not.toContain("автономная мастерская");
});

it("shows the UTC year so server and browser timezones agree at a year boundary", () => {
  vi.useFakeTimers({ now: Date.parse("2026-12-31T22:30:00Z"), toFake: ["Date"] });
  const originalTz = process.env.TZ;
  try {
    process.env.TZ = "Pacific/Kiritimati";
    const { container } = render(<LocaleProvider initialLocale="en"><HomeFooter /></LocaleProvider>);
    expect(container.querySelector("footer p span")?.textContent).toBe("2026");
  } finally {
    if (originalTz === undefined) delete process.env.TZ; else process.env.TZ = originalTz;
    vi.useRealTimers();
  }
});
