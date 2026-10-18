"use client";

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { NAVIGATION_MESSAGES } from '@/lib/i18n/navigation';

/** Shared language controls for navigation and standalone account screens. */
export function LanguageSelector() {
  const { locale, setLocale } = useLocale();
  const t = useTranslations(NAVIGATION_MESSAGES);
  return (
    <div role="group" aria-label={t("language")} className="flex gap-1">
      {(["en", "ru"] as const).map(value => (
        <button key={value} type="button" lang={value} aria-label={value === "en" ? "English" : "Русский"}
          aria-pressed={locale === value} onClick={() => setLocale(value)}
          className="min-h-11 min-w-11 rounded-lg px-2 text-xs text-neutral-300 hover:bg-white/5 aria-pressed:bg-violet-400/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300">
          {value.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
