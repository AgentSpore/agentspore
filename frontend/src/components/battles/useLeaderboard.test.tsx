// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BattleLeaderboard } from "@/lib/api";
import { useLeaderboard } from "./useLeaderboard";

const BOARD: BattleLeaderboard = {
  contenders: [
    {
      id: "c1",
      display_name: "GLM-4.6 · direct",
      provider: "zai",
      model_id: "glm-4.6",
      approach_key: "direct",
      elo: 1520,
      wins: 3,
      losses: 1,
      ties: 0,
      battles: 4,
    },
  ],
  approaches: [{ approach_key: "direct", wins: 3, losses: 1, ties: 0, battles: 4 }],
};

function ok(body: BattleLeaderboard): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useLeaderboard", () => {
  it("reports the status of a failed first load and shows no board", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503 }) as Response));
    const { result } = renderHook(() => useLeaderboard(60000));
    await waitFor(() => expect(result.current.failure).toBe("HTTP 503"));
    expect(result.current.board).toBeNull();
  });

  it("reports a transport error by its message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("Failed to fetch");
      }),
    );
    const { result } = renderHook(() => useLeaderboard(60000));
    await waitFor(() => expect(result.current.failure).toBe("Failed to fetch"));
  });

  // The degradation that matters: a poll that fails must not blank a ladder the
  // user is already looking at, and must not keep claiming everything is fine.
  it("keeps the last good board when a later poll fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok(BOARD))
      .mockResolvedValue({ ok: false, status: 500 } as Response);
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers();

    const { result } = renderHook(() => useLeaderboard(1000));
    await vi.waitFor(() => expect(result.current.board).toEqual(BOARD));
    expect(result.current.failure).toBeNull();

    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(result.current.failure).toBe("HTTP 500"));
    expect(result.current.board).toEqual(BOARD);
  });
});
