import { afterEach, describe, expect, it, vi } from "vitest";

async function loadApiUrl(): Promise<string> {
  vi.resetModules();
  return (await import("./api")).API_URL;
}

describe("API_URL", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("is empty (same-origin) when the build sets it to an empty string", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(await loadApiUrl()).toBe("");
  });

  it("keeps an explicit absolute URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://agentspore.ru");
    expect(await loadApiUrl()).toBe("https://agentspore.ru");
  });
});
