/** UI locale; machine identifiers and user content keep their original values. */
export type Locale = "en" | "ru";
/** Both languages must define every key of a domain dictionary. */
export type Messages<K extends string = string> = Record<Locale, Record<K, string>>;
/** Name of the only cookie read by the locale infrastructure. */
export const LOCALE_COOKIE = "agentspore_locale";
/** Unsupported or missing preferences use English. */
export function parseLocale(value: unknown): Locale {
  return value === "ru" ? "ru" : "en";
}
/** Request signals, in the order they are consulted by `resolveLocale`. */
export interface LocaleSignals { cookie?: string; host?: string | null; acceptLanguage?: string | null }

function isLocale(value: unknown): value is Locale {
  return value === "en" || value === "ru";
}

/** Highest-weighted supported language of an Accept-Language header, if any. */
function preferredLanguage(header: string): Locale | undefined {
  const ranked = header.split(",").map((part, index) => {
    const [tag = "", ...params] = part.trim().toLowerCase().split(";");
    const weight = params.map(p => /^\s*q=([\d.]+)/.exec(p)?.[1]).find(Boolean);
    return { language: tag.split("-")[0], q: weight === undefined ? 1 : Number(weight), index };
  });
  return ranked
    .filter(entry => isLocale(entry.language) && entry.q > 0)
    .sort((a, b) => b.q - a.q || a.index - b.index)
    .map(entry => entry.language as Locale)[0];
}

/** Explicit cookie, then `*.ru` host, then Accept-Language, then English. */
export function resolveLocale({ cookie, host, acceptLanguage }: LocaleSignals): Locale {
  if (isLocale(cookie)) return cookie;
  const hostname = host?.split(":")[0]?.toLowerCase() ?? "";
  if (hostname.endsWith(".ru")) return "ru";
  return (acceptLanguage ? preferredLanguage(acceptLanguage) : undefined) ?? "en";
}

const ORIGINS: Record<Locale, string> = { en: "https://agentspore.com", ru: "https://agentspore.ru" };

/** Canonical on the page's own language host plus hreflang links to both hosts. */
export function localeAlternates(path: string, locale: Locale) {
  return {
    canonical: ORIGINS[locale] + path,
    languages: { en: ORIGINS.en + path, ru: ORIGINS.ru + path, "x-default": ORIGINS.en + path },
  };
}

/** Browser Intl identifier for UI formatting, without changing submitted values. */
export function localeTag(locale: Locale): string {
  return locale === "ru" ? "ru-RU" : "en-US";
}
/** Replace text placeholders; missing values are explicit dictionary errors. */
export function interpolate(message: string, values: Record<string, string | number> = {}): string {
  return message.replace(/(?<!\{)\{(\w+)\}(?!\})/g, (_, key: string) => {
    const value = values[key];
    if (value === undefined) throw new Error(`Missing translation value: ${key}`);
    return String(value);
  });
}
