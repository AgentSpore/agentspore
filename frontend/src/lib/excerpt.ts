import { localeTag, type Locale } from "@/lib/i18n/locale";

const ISO_TIMESTAMP = /(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/g;

/** Replace machine ISO timestamps inside prose with a locale-formatted calendar date, as written by the author (offset is not applied). */
export function formatIsoDates(text: string, locale: Locale): string {
  const format = new Intl.DateTimeFormat(localeTag(locale), { dateStyle: "medium", timeZone: "UTC" });
  return text.replace(ISO_TIMESTAMP, (raw, year: string, month: string, day: string) => {
    const valid = !Number.isNaN(Date.parse(/[Z+-]\d{2}:?\d{2}$|Z$/.test(raw) ? raw : `${raw}Z`));
    return valid ? format.format(Date.UTC(Number(year), Number(month) - 1, Number(day))) : raw;
  });
}
