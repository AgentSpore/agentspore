import { describe, expect, it } from "vitest";
import { CURATED_SERVICES, SHOWCASE_COPY } from "@/app/showcase/curatedServices";

const modules = import.meta.glob(
  ["./*.ts", "!./*.test.ts", "!./locale.ts", "!./server.ts"],
  { eager: true },
) as Record<string, Record<string, unknown>>;

function isDictionary(value: unknown): value is { en: unknown; ru: unknown } {
  return typeof value === "object" && value !== null && "en" in value && "ru" in value;
}

function leaves(value: unknown, prefix = ""): Record<string, string> {
  if (typeof value === "string") return { [prefix]: value };
  if (typeof value !== "object" || value === null) {
    throw new Error(`Non-text translation at ${prefix}`);
  }
  return Object.fromEntries(Object.entries(value).flatMap(([key, child]) =>
    Object.entries(leaves(child, prefix ? `${prefix}.${key}` : key)),
  ));
}

function placeholders(value: string): string[] {
  return [...new Set(Array.from(value.matchAll(/(?<!\{)\{(\w+)\}(?!\})/g), match => match[1]))].sort();
}

const dictionaries = Object.entries(modules).flatMap(([path, exports]) =>
  Object.entries(exports).filter(([, value]) => isDictionary(value))
    .map(([name, value]) => ({ name: `${path}:${name}`, value: value as { en: unknown; ru: unknown } })),
).concat([
  { name: "showcase:SHOWCASE_COPY", value: SHOWCASE_COPY },
  { name: "showcase:CURATED_SERVICES", value: CURATED_SERVICES },
]);

describe("shipped bilingual dictionaries", () => {
  it("discovers every current domain, lookup table and curated service dictionary", () => {
    expect(dictionaries.map(({ name }) => name)).toEqual(expect.arrayContaining([
      "./navigation.ts:NAVIGATION_MESSAGES", "./public.ts:publicMessages", "./public.ts:publicLabels",
      "./account.ts:accountMessages", "./communication.ts:agentMessages",
      "./communication.ts:communicationMessages", "./automation.ts:automationMessages",
      "./mixer.ts:mixerMessages", "./battles.ts:battlesMessages",
      "./hostedAgent.ts:hostedAgentMessages", "./shared.ts:sharedMessages", "./shared.ts:editorPhrases",
      "showcase:SHOWCASE_COPY", "showcase:CURATED_SERVICES",
    ]));
  });

  it("treats double-braced machine examples as literals beside real substitutions", () => {
    expect(placeholders("{{MIX_xxxxxx}} {{PRIVATE:value}} {name} {count} {name}"))
      .toEqual(["count", "name"]);
  });

  it.each(dictionaries)("$name has matching nonempty EN/RU text and placeholder names", ({ name, value }) => {
    const english = leaves(value.en);
    const russian = leaves(value.ru);
    expect(Object.keys(russian).sort(), `${name}: translation keys`).toEqual(Object.keys(english).sort());
    for (const [key, text] of Object.entries(english)) {
      expect.soft(text.trim(), `${name}:${key}: empty English text`).not.toBe("");
      expect.soft(russian[key]?.trim(), `${name}:${key}: empty Russian text`).toBeTruthy();
      expect.soft(placeholders(russian[key]), `${name}:${key}: interpolation values`).toEqual(placeholders(text));
    }
  });
});
