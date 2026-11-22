import { expect, it } from "vitest";
import { SHOWCASE_COPY } from "@/app/showcase/curatedServices";
import { docsMessages } from "./docs";
import { NAVIGATION_MESSAGES } from "./navigation";

const LOCALES = ["en", "ru"] as const;

it.each(LOCALES)("home title in %s is the product phrase, not the old slogan", locale => {
  const title = NAVIGATION_MESSAGES[locale].rootTitle;
  expect(title).not.toContain("Autonomous Startup Forge");
  expect(title).toBe(locale === "en" ? "AgentSpore: useful services built by AI agents" : "AgentSpore: полезные сервисы, созданные ИИ-агентами");
  expect(title).not.toContain("—");
});

it.each(LOCALES)("showcase breadcrumb in %s equals the nav label and is not the route slug", locale => {
  expect(SHOWCASE_COPY[locale].crumb).toBe(NAVIGATION_MESSAGES[locale].showcase);
  expect(SHOWCASE_COPY[locale].crumb).toBe(locale === "en" ? "Services" : "Сервисы");
  expect(SHOWCASE_COPY[locale].crumb).not.toContain("showcase");
});

it.each(LOCALES)("docs sentences in %s do not say platform statistics", locale => {
  const text = Object.values(docsMessages[locale]).join("\n");
  expect(text).not.toMatch(/platform stat/i);
  expect(text).not.toMatch(/статистике платформы/i);
});
