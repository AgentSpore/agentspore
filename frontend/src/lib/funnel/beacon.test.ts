import { afterEach, describe, expect, it, vi } from "vitest";
import { buildBeacon, isCountablePath, sendVisit, trackingOptOut } from "./beacon";

afterEach(() => vi.unstubAllGlobals());

describe("buildBeacon", () => {
  it("sends only path on non-entry navigations", () => {
    expect(buildBeacon("/agents", false, "?utm_source=x&token=secret", "https://t.co/abc?q=1", "agentspore.com"))
      .toEqual({ path: "/agents" });
  });

  it("entry page adds utm_source and the referrer origin only", () => {
    const beacon = buildBeacon("/", true, "?utm_source=telegram&email=a@b.c", "https://t.me/chan/42?x=1", "agentspore.com");
    expect(beacon).toEqual({ path: "/", utm_source: "telegram", referrer: "https://t.me" });
    expect(Object.keys(beacon ?? {}).sort()).toEqual(["path", "referrer", "utm_source"]);
  });

  it("drops own-host and unparsable referrers and oversized utm", () => {
    expect(buildBeacon("/", true, "", "https://agentspore.com/x", "agentspore.com")).toEqual({ path: "/" });
    expect(buildBeacon("/", true, "", "not a url", "agentspore.com")).toEqual({ path: "/" });
    expect(buildBeacon("/", true, `?utm_source=${"a".repeat(65)}`, "", "agentspore.com")).toEqual({ path: "/" });
  });

  it("skips admin pages and paths the schema would reject", () => {
    expect(buildBeacon("/admin/funnel", true, "", "", "h")).toBeNull();
    expect(buildBeacon("/admin", false, "", "", "h")).toBeNull();
    expect(isCountablePath("/a".repeat(101))).toBe(false);
    expect(isCountablePath("/administrator")).toBe(true);
  });
});

describe("trackingOptOut", () => {
  it("honours DNT and GPC only when set", () => {
    expect(trackingOptOut({ doNotTrack: "1" })).toBe(true);
    expect(trackingOptOut({ globalPrivacyControl: true })).toBe(true);
    expect(trackingOptOut({ doNotTrack: "0", globalPrivacyControl: false })).toBe(false);
    expect(trackingOptOut({})).toBe(false);
  });
});

describe("sendVisit", () => {
  it("posts keepalive JSON without credentials", () => {
    const fetchMock = vi.fn().mockResolvedValue({});
    vi.stubGlobal("fetch", fetchMock);
    sendVisit({ path: "/blog" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/v1\/funnel\/visit$/);
    expect(init).toMatchObject({ method: "POST", keepalive: true, credentials: "omit", body: '{"path":"/blog"}' });
  });

  it("swallows async and sync fetch failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(() => sendVisit({ path: "/" })).not.toThrow();
    vi.stubGlobal("fetch", vi.fn(() => { throw new Error("blocked"); }));
    expect(() => sendVisit({ path: "/" })).not.toThrow();
    await Promise.resolve();
  });
});
