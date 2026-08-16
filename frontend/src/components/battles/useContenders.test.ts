// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BattleContender } from "@/lib/api";
import { sideName } from "./useContenders";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

function contender(id: string, display_name: string, enabled: boolean): BattleContender {
  return { id, display_name, provider: "llm7", model_id: "m", approach_key: "direct", enabled };
}

const RETIRED = contender("c-retired", "Mistral Small · Direct", false);
const ACTIVE = contender("c-active", "Qwen · Step by step", true);

describe("sideName", () => {
  // V79 retired eight contenders. The roster route filtered on `enabled`, so
  // the 1821 battles those fighters had already fought lost their names and
  // rendered as the loading placeholder. The fix is server-side (the roster now
  // carries retired rows); this pins the behaviour the UI depends on.
  it("resolves the name of a retired contender that already fought", () => {
    const map = new Map([RETIRED, ACTIVE].map((c) => [c.id, c]));
    expect(sideName(null, "c-retired", new Map(), map)).toBe("Mistral Small · Direct");
  });

  it("still resolves an active contender", () => {
    const map = new Map([[ACTIVE.id, ACTIVE]]);
    expect(sideName(null, "c-active", new Map(), map)).toBe("Qwen · Step by step");
  });

  it("falls back to the placeholder only for a genuinely unknown id", () => {
    expect(sideName(null, "c-gone", new Map(), new Map())).toBe("…");
  });

  it("returns null for an open side with no fighter assigned", () => {
    expect(sideName(null, null, new Map(), new Map())).toBeNull();
  });

  it("prefers a real agent name over the contender lookup", () => {
    const map = new Map([[ACTIVE.id, ACTIVE]]);
    expect(sideName("a1", "c-active", new Map([["a1", "ScoutBot"]]), map)).toBe("ScoutBot");
  });
});

describe("useContenders fetch path", () => {
  // The module-level cache is per-import, so each case loads a fresh copy.
  async function load(rows: BattleContender[]) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, json: async () => rows }) as Response)
    );
    const mod = await import("./useContenders");
    const { result } = renderHook(() => mod.useContenders());
    await waitFor(() => expect(result.current.size).toBeGreaterThan(0));
    return result.current;
  }

  // MUTATION: filter the roster on `enabled` inside loadContenders and this
  // goes red — which is exactly what the server used to do to this data.
  it("keeps retired contenders in the map so their battles keep their names", async () => {
    const map = await load([RETIRED, ACTIVE]);
    expect(map.get("c-retired")?.display_name).toBe("Mistral Small · Direct");
    expect(sideName(null, "c-retired", new Map(), map)).toBe("Mistral Small · Direct");
  });

  it("carries the enabled flag so a caller can still tell who is active", async () => {
    const map = await load([RETIRED, ACTIVE]);
    expect([...map.values()].filter((c) => c.enabled).map((c) => c.id)).toEqual(["c-active"]);
  });
});
