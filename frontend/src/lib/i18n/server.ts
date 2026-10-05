import { cookies } from "next/headers";
import type { Metadata } from "next";
import { LOCALE_COOKIE, parseLocale, type Locale } from "./locale";
import { NAVIGATION_MESSAGES } from "./navigation";

/** Read only the UI preference cookie, never auth or session cookies. */
export async function getLocale(): Promise<Locale> {
  return parseLocale((await cookies()).get(LOCALE_COOKIE)?.value);
}

const routeKeys = {
  agents: ["agentsTitle", "agentsDescription"], projects: ["projectsTitle", "projectsDescription"],
  teams: ["teams", "teamsDescription"], blog: ["blog", "blogDescription"], chat: ["chatTitle", "chatDescription"],
  flows: ["flows", "flowsDescription"], "hosted-agents": ["hosted", "hostedDescription"],
  mixer: ["mixer", "mixerDescription"], battles: ["battlesTitle", "battlesDescription"],
} as const;

/** Resolve a route's metadata from the same locale as its server-rendered page. */
export async function getPageMetadata(route: keyof typeof routeKeys): Promise<Metadata> {
  const locale = await getLocale();
  const copy = NAVIGATION_MESSAGES[locale];
  const [titleKey, descriptionKey] = routeKeys[route];
  const title = copy[titleKey];
  const description = copy[descriptionKey];
  return {
    title, description, alternates: { canonical: `/${route}` },
    openGraph: { title, description, locale: locale === "ru" ? "ru_RU" : "en_US" },
    twitter: { title, description },
  };
}
