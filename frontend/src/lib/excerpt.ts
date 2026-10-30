import { localeTag, type Locale } from "@/lib/i18n/locale";

const ISO_TIMESTAMP = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/g;

/** Replace machine ISO timestamps inside prose with a locale-formatted calendar date (UTC). */
export function formatIsoDates(text: string, locale: Locale): string {
  const format = new Intl.DateTimeFormat(localeTag(locale), { dateStyle: "medium", timeZone: "UTC" });
  return text.replace(ISO_TIMESTAMP, raw => {
    const ms = Date.parse(/[Z+-]\d{2}:?\d{2}$|Z$/.test(raw) ? raw : `${raw}Z`);
    return Number.isNaN(ms) ? raw : format.format(ms);
  });
}

/** Plain-text card excerpt: markdown marks removed, dates formatted, clamped at a word boundary. */
export function blogExcerpt(content: string, locale: Locale, max = 160): string {
  const text = formatIsoDates(content.replace(/[#*_`>[\]]/g, ""), locale).replace(/\s+/g, " ").trim();
  const characters = Array.from(text);
  if (characters.length <= max) return text;
  const cut = characters.slice(0, max).join("");
  const atWord = /\s/u.test(characters[max] ?? "") ? cut : cut.replace(/\s+\S*$/u, "");
  return `${atWord || cut}…`;
}
