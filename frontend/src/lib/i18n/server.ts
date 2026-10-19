import { cookies, headers } from "next/headers";
import type { Metadata } from "next";
import { LOCALE_COOKIE, localeAlternates, resolveLocale, type Locale } from "./locale";
import { NAVIGATION_MESSAGES } from "./navigation";

/** Cookie (user choice) wins, then host, then Accept-Language; never auth or session cookies. */
export async function getLocale(): Promise<Locale> {
  const [jar, requestHeaders] = await Promise.all([cookies(), headers()]);
  return resolveLocale({
    cookie: jar.get(LOCALE_COOKIE)?.value,
    host: requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host"),
    acceptLanguage: requestHeaders.get("accept-language"),
  });
}

const routeKeys = {
  agents: ["agentsTitle", "agentsDescription"], projects: ["projectsTitle", "projectsDescription"],
  teams: ["teams", "teamsDescription"], blog: ["blog", "blogDescription"], chat: ["chatTitle", "chatDescription"],
  battles: ["battlesTitle", "battlesDescription"],
  showcase: ["showcaseTitle", "showcaseDescription"], login: ["loginTitle", "loginDescription"],
  profile: ["profileTitle", "profileDescription"], workspace: ["workspaceTitle", "workspaceDescription"],
  dashboard: ["dashboardTitle", "dashboardDescription"],
} as const;

/** Resolve a route's metadata from the same locale as its server-rendered page. */
export async function getPageMetadata(route: keyof typeof routeKeys): Promise<Metadata> {
  const locale = await getLocale();
  const copy = NAVIGATION_MESSAGES[locale];
  const [titleKey, descriptionKey] = routeKeys[route];
  const title = copy[titleKey];
  const description = copy[descriptionKey];
  return {
    title, description, alternates: localeAlternates(`/${route}`, locale),
    openGraph: { title, description, locale: locale === "ru" ? "ru_RU" : "en_US" },
    twitter: { title, description },
  };
}
