import { afterEach, describe, expect, it, vi } from "vitest";
import { interpolate, LOCALE_COOKIE, localeTag, parseLocale } from "./locale";
import { timeAgo } from "@/lib/api";
import { NAVIGATION_MESSAGES } from "./navigation";
import { getLocale, getPageMetadata } from "./server";
import RootLayout, { generateMetadata } from "@/app/layout";
import { renderToStaticMarkup } from "react-dom/server";

const request = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("next/headers", () => ({ cookies: async () => ({ get: request.get }) }));
vi.mock("next/font/google", () => ({ Geist: () => ({ variable: "sans" }), Geist_Mono: () => ({ variable: "mono" }) }));
vi.mock("next/script", () => ({ default: () => null }));
vi.mock("@/app/providers", () => ({ Providers: ({ children }: { children: React.ReactNode }) => children }));

describe("locale contracts", () => {
  it.each([undefined, null, "EN", "fr", "ru-RU", "", 0])("defaults invalid preference %s to English", (value) => {
    expect(parseLocale(value)).toBe("en");
  });
  it("accepts both locales and provides Intl tags", () => {
    expect(parseLocale("ru")).toBe("ru");
    expect(parseLocale("en")).toBe("en");
    expect(localeTag("ru")).toBe("ru-RU");
    expect(localeTag("en")).toBe("en-US");
  });
  it("preserves zero, repeated placeholders and plain text without evaluating HTML", () => {
    expect(interpolate("{name}: {count}/{count}", { name: "<b>example</b>", count: 0 }))
      .toBe("<b>example</b>: 0/0");
    expect(interpolate("{{MIX_abcdef}} {{PRIVATE:value}} {name}", { name: "example" }))
      .toBe("{{MIX_abcdef}} {{PRIVATE:value}} example");
    expect(() => interpolate("{missing}")).toThrow("Missing translation value: missing");
  });
  it("defines the same shared navigation keys in both languages", () => {
    expect(Object.keys(NAVIGATION_MESSAGES.ru).sort()).toEqual(Object.keys(NAVIGATION_MESSAGES.en).sort());
    expect(Object.values(NAVIGATION_MESSAGES.ru).every(value => value.trim())).toBe(true);
  });
  it("reads exactly the locale cookie and ignores unsupported values", async () => {
    request.get.mockReset().mockReturnValue({ value: "fr" });
    expect(await getLocale()).toBe("en");
    expect(request.get.mock.calls).toEqual([[LOCALE_COOKIE]]);
  });
  it.each(["en", "ru"])("aligns root SSR, metadata, social tags and structured data in %s", async (locale) => {
    request.get.mockReturnValue({ value: locale });
    const metadata = await generateMetadata();
    const copy = NAVIGATION_MESSAGES[parseLocale(locale)];
    expect(metadata.description).toBe(copy.rootDescription);
    expect(metadata.title).toEqual({ default: copy.rootTitle, template: "%s | AgentSpore" });
    expect(metadata.openGraph).toMatchObject({ title: copy.rootTitle, locale: locale === "ru" ? "ru_RU" : "en_US" });
    expect(metadata.twitter).toMatchObject({ title: copy.rootTitle });
    const html = renderToStaticMarkup(await RootLayout({ children: "Synthetic child" }));
    expect(html).toContain(`<html lang="${locale}">`);
    expect(html).toContain(`"inLanguage":"${locale}"`);
    expect(html).toContain(copy.rootDescription);
  });
  it.each(["agents", "projects", "teams", "blog", "chat", "flows", "hosted-agents", "mixer", "battles"] as const)
    ("localizes %s metadata while retaining its canonical URL", async (route) => {
      request.get.mockReturnValue({ value: "en" });
      const english = await getPageMetadata(route);
      request.get.mockReturnValue({ value: "ru" });
      const russian = await getPageMetadata(route);
      expect(russian.title).not.toBe(english.title);
      expect(russian.description).not.toBe(english.description);
      expect(russian.alternates).toEqual({ canonical: `/${route}` });
      expect(russian.openGraph).toMatchObject({ locale: "ru_RU", title: russian.title });
    });
});

describe("localized relative time", () => {
  afterEach(() => vi.useRealTimers());
  it("preserves English output and translates past, future and invalid times", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    expect(timeAgo("2026-10-05T11:58:00Z")).toBe("2m ago");
    expect(timeAgo("2026-10-05T11:58:00Z", "ru")).toBe("2 минуты назад");
    expect(timeAgo("2026-10-05T12:02:00Z", "ru")).toBe("через 2 минуты");
    expect(timeAgo("2026-10-05T12:00:10Z", "ru")).toBe("только что");
    expect(timeAgo("2026-10-03T12:00:00Z", "ru")).toBe("2 дня назад");
    expect(timeAgo(null, "ru")).toBe("—");
    expect(timeAgo("invalid", "ru")).toBe("—");
    expect(timeAgo("2026-10-05T12:02:00Z")).toBe("in 2m");
    expect(timeAgo("2026-10-05T12:00:10Z")).toBe("just now");
  });
});
