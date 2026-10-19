import { afterEach, describe, expect, it, vi } from "vitest";
import { interpolate, LOCALE_COOKIE, localeAlternates, localeTag, parseLocale, resolveLocale } from "./locale";
import { timeAgo } from "@/lib/api";
import { NAVIGATION_MESSAGES } from "./navigation";
import { getLocale, getPageMetadata } from "./server";
import RootLayout, { generateMetadata } from "@/app/layout";
import { renderToStaticMarkup } from "react-dom/server";

const request = vi.hoisted(() => ({ get: vi.fn(), header: vi.fn() }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: request.get }),
  headers: async () => ({ get: request.header }),
}));
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
  it.each(["agents", "projects", "teams", "blog", "chat", "battles"] as const)
    ("localizes %s metadata while retaining its canonical URL", async (route) => {
      request.get.mockReturnValue({ value: "en" });
      const english = await getPageMetadata(route);
      request.get.mockReturnValue({ value: "ru" });
      const russian = await getPageMetadata(route);
      expect(russian.title).not.toBe(english.title);
      expect(russian.description).not.toBe(english.description);
      expect(russian.alternates).toEqual(localeAlternates(`/${route}`));
      expect(russian.openGraph).toMatchObject({ locale: "ru_RU", title: russian.title });
    });
});

describe("locale resolution order", () => {
  it("explicit cookie beats host and Accept-Language", () => {
    expect(resolveLocale({ cookie: "en", host: "agentspore.ru", acceptLanguage: "ru-RU" })).toBe("en");
    expect(resolveLocale({ cookie: "ru", host: "agentspore.com", acceptLanguage: "en-US" })).toBe("ru");
  });
  it("host beats Accept-Language when no valid cookie exists", () => {
    expect(resolveLocale({ cookie: "fr", host: "agentspore.ru", acceptLanguage: "en-US" })).toBe("ru");
    expect(resolveLocale({ host: "www.agentspore.ru:443", acceptLanguage: "en" })).toBe("ru");
    expect(resolveLocale({ host: "agentspore.com", acceptLanguage: "ru-RU,ru;q=0.9" })).toBe("ru");
  });
  it("Accept-Language picks the best supported language by weight", () => {
    expect(resolveLocale({ acceptLanguage: "de-DE,de;q=0.9,ru;q=0.8,en;q=0.5" })).toBe("ru");
    expect(resolveLocale({ acceptLanguage: "en;q=0.9,ru;q=0.4" })).toBe("en");
    expect(resolveLocale({ acceptLanguage: "ru;q=0,en" })).toBe("en");
  });
  it("falls back to English", () => {
    expect(resolveLocale({})).toBe("en");
    expect(resolveLocale({ host: "agentspore.com", acceptLanguage: "de,fr" })).toBe("en");
    expect(resolveLocale({ host: "notagentspore.ru.example.com" })).toBe("en");
  });
  it("getLocale reads cookie, then forwarded host, then Accept-Language", async () => {
    request.get.mockReturnValue(undefined);
    request.header.mockImplementation((name: string) => ({ "x-forwarded-host": "agentspore.ru", "accept-language": "en" })[name] ?? null);
    expect(await getLocale()).toBe("ru");
    request.get.mockReturnValue({ value: "en" });
    expect(await getLocale()).toBe("en");
    request.get.mockReturnValue(undefined);
    request.header.mockImplementation((name: string) => ({ host: "agentspore.com", "accept-language": "ru-RU" })[name] ?? null);
    expect(await getLocale()).toBe("ru");
    request.header.mockReset();
  });
  it("links hreflang alternates to the .com and .ru twins at the same path", () => {
    expect(localeAlternates("/agents")).toEqual({
      canonical: "/agents",
      languages: { en: "https://agentspore.com/agents", ru: "https://agentspore.ru/agents", "x-default": "https://agentspore.com/agents" },
    });
    expect(localeAlternates("/").languages?.ru).toBe("https://agentspore.ru/");
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
