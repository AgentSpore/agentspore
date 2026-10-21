import { historySource } from "./api";
import { websocketBase } from "./api";
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

describe("websocketBase", () => {
  const ru = { protocol: "https:", host: "agentspore.ru" };

  it("builds an absolute wss URL from the page when API_URL is empty", () => {
    expect(websocketBase("", ru)).toBe("wss://agentspore.ru");
    expect(
      websocketBase("", { protocol: "http:", host: "localhost:3000" }),
    ).toBe("ws://localhost:3000");
  });

  it("derives the scheme from an explicit API_URL", () => {
    expect(websocketBase("https://agentspore.com", ru)).toBe(
      "wss://agentspore.com",
    );
    expect(websocketBase("http://localhost:8000", ru)).toBe(
      "ws://localhost:8000",
    );
  });
});

// Imported metadata is untrusted; only public HTTPS GitHub source links are shown.
describe('historySource', () => {
  it.each(['http://github.com/x/y', 'javascript:alert(1)', 'https://github.com.evil.test/x', 'https://user:pass@github.com/x'])('rejects unsafe source %s', source_url => {
    expect(historySource({ source: 'github', source_url })).toBeNull();
  });
  it('uses canonical project and commit source fields, with no link for local mapping', () => {
    expect(historySource({ source: 'github_public_history', repository_url: 'https://github.com/Example/repo' })).toBe('https://github.com/Example/repo');
    expect(historySource({ github_url: 'https://github.com/Example/repo/commit/abc' })).toBe('https://github.com/Example/repo/commit/abc');
    expect(historySource({ source: 'retained_local_mapping' })).toBeNull();
  });
});
