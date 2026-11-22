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

it("showcase breadcrumb is a translated word, not the route slug", () => {
  expect(SHOWCASE_COPY.ru.crumb).toBe("Витрина");
  expect(SHOWCASE_COPY.en.crumb).toBe("Services");
  for (const locale of LOCALES) expect(SHOWCASE_COPY[locale].crumb).not.toContain("showcase");
});

it.each(LOCALES)("docs sentences in %s do not say platform statistics", locale => {
  const text = Object.values(docsMessages[locale]).join("\n");
  expect(text).not.toMatch(/platform stat/i);
  expect(text).not.toMatch(/статистике платформы/i);
});
