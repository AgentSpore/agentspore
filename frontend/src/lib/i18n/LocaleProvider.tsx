"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { interpolate, LOCALE_COOKIE, parseLocale, type Locale } from "./locale";

interface LocaleContextValue { locale: Locale; setLocale: (locale: Locale) => void; }
const LocaleContext = createContext<LocaleContextValue>({ locale: "en", setLocale: () => {} });

/** Preserve the client tree and form state while persisting a non-sensitive locale cookie. */
export function LocaleProvider({ initialLocale, children }: { initialLocale: Locale; children: React.ReactNode }) {
  const [locale, updateLocale] = useState(() => parseLocale(initialLocale));
  const router = useRouter();
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  const setLocale = (value: Locale) => {
    const nextLocale = parseLocale(value);
    updateLocale(nextLocale);
    try {
      const secure = window.location.protocol === "https:" ? "; Secure" : "";
      const preference = `${LOCALE_COOKIE}=${nextLocale}`;
      document.cookie = `${preference}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
      if (document.cookie.split(";").some(cookie => cookie.trim() === preference)) router.refresh();
    } catch {
      // Cookie denial must not prevent changing the current UI language.
    }
  };
  return <LocaleContext.Provider value={{ locale, setLocale }}>{children}</LocaleContext.Provider>;
}

/** English is the safe default for isolated components outside the application provider. */
export function useLocale(): LocaleContextValue {
  return useContext(LocaleContext);
}

/** Translate a domain key with equal EN/RU key sets and optional text interpolation. */
export function useTranslations<const T extends Record<string, string>>(messages: { en: T; ru: Record<keyof T, string> }) {
  const { locale } = useLocale();
  return useCallback((key: keyof T, values?: Record<string, string | number>) =>
    interpolate(messages[locale][key], values), [locale, messages]);
}
