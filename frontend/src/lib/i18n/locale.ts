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
