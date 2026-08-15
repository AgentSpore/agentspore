// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useModerationQueue } from "./useModerationQueue";

const TASK = { id: "t1", title: "T", prompt: "p", rubric: [], category: "general" };

function stub(replies: Array<{ status: number; body?: unknown }>) {
  const calls: string[] = [];
  let i = 0;
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    const r = replies[Math.min(i++, replies.length - 1)];
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body ?? [],
    } as unknown as Response;
  });
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useModerationQueue", () => {
  it("maps 403 to unauthorized rather than an error screen", async () => {
    stub([{ status: 403 }]);

    const { result } = renderHook(() => useModerationQueue());

    await waitFor(() => expect(result.current.state).toBe("unauthorized"));
    expect(result.current.tasks).toEqual([]);
  });

  it("surfaces a failed action instead of swallowing it", async () => {
    // The row must SAY it failed: a silent failure is indistinguishable from
    // a mis-click, and the admin clicks again on a row that never moved.
    stub([{ status: 200, body: [TASK] }, { status: 500 }]);
    const { result } = renderHook(() => useModerationQueue());
    await waitFor(() => expect(result.current.tasks).toHaveLength(1));

    await act(async () => {
      result.current.approve("t1");
    });

    await waitFor(() => expect(result.current.failedId).toBe("t1"));
    // And the row stays: nothing was approved.
    expect(result.current.tasks).toHaveLength(1);
    expect(result.current.pendingId).toBeNull();
  });

  it("refetches on 409 rather than leaving a row another moderator took", async () => {
    const calls = stub([
      { status: 200, body: [TASK] },
      { status: 409 },
      { status: 200, body: [] },
    ]);
    const { result } = renderHook(() => useModerationQueue());
    await waitFor(() => expect(result.current.tasks).toHaveLength(1));

    await act(async () => {
      result.current.approve("t1");
    });

    await waitFor(() => expect(result.current.tasks).toHaveLength(0));
    expect(calls.filter((u) => u.endsWith("/tasks/moderation"))).toHaveLength(2);
    expect(result.current.failedId).toBeNull();
  });

  it("drops the row on a successful approve", async () => {
    stub([{ status: 200, body: [TASK] }, { status: 200 }]);
    const { result } = renderHook(() => useModerationQueue());
    await waitFor(() => expect(result.current.tasks).toHaveLength(1));

    await act(async () => {
      result.current.approve("t1");
    });

    await waitFor(() => expect(result.current.tasks).toHaveLength(0));
  });

  it("does not send a reject with an empty reason", async () => {
    const calls = stub([{ status: 200, body: [TASK] }]);
    vi.stubGlobal("prompt", () => "   ");
    const { result } = renderHook(() => useModerationQueue());
    await waitFor(() => expect(result.current.tasks).toHaveLength(1));

    await act(async () => {
      result.current.reject("t1");
    });

    expect(calls.filter((u) => u.includes("/reject"))).toHaveLength(0);
  });
});
