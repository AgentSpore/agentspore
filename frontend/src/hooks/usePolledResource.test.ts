// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePolledResource } from "./usePolledResource";

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Object.defineProperty(document, "hidden", { value: false, configurable: true });
});

describe("usePolledResource", () => {
  it("polls repeatedly at the interval", async () => {
    let calls = 0;
    const fetcher = vi.fn(async () => { calls++; return calls; });

    renderHook(() => usePolledResource(fetcher, { intervalMs: 1000 }));
    await waitFor(() => expect(calls).toBe(1));

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(calls).toBe(2);

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(calls).toBe(3);
  });

  it("does not stack concurrent requests when visibilitychange fires mid-fetch (single-flight)", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const fetcher = vi.fn(async () => {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((r) => setTimeout(r, 500));
      inFlight--;
      return "x";
    });

    renderHook(() => usePolledResource(fetcher, { intervalMs: 100 }));
    // A visibilitychange during the still-pending first fetch is the actual
    // race the guard exists for: without it, the handler's own load() call
    // would start a second fetch while the first is still in flight.
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });

    expect(maxInFlight).toBe(1);
  });

  it("keeps prior data on error and sets error; clears error on recovery", async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call++;
      if (call === 1) return "first";
      if (call === 2) throw new Error("boom");
      return "third";
    });

    const { result } = renderHook(() => usePolledResource(fetcher, { intervalMs: 1000 }));
    await waitFor(() => expect(result.current.data).toBe("first"));

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(result.current.error?.message).toBe("boom");
    expect(result.current.data).toBe("first"); // not blanked

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(result.current.data).toBe("third");
    expect(result.current.error).toBeNull();
  });

  it("does not advance lastUpdated on a failed fetch", async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call++;
      if (call === 1) return "ok";
      throw new Error("fail");
    });

    const { result } = renderHook(() => usePolledResource(fetcher, { intervalMs: 1000 }));
    await waitFor(() => expect(result.current.data).toBe("ok"));
    const firstStamp = result.current.lastUpdated;
    expect(firstStamp).not.toBeNull();

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(result.current.error).not.toBeNull();
    expect(result.current.lastUpdated).toBe(firstStamp);
  });

  it("stops polling when the tab is hidden and refetches on visibility restore", async () => {
    let calls = 0;
    const fetcher = vi.fn(async () => { calls++; return calls; });

    renderHook(() => usePolledResource(fetcher, { intervalMs: 1000 }));
    await waitFor(() => expect(calls).toBe(1));

    // The hook only reads document.hidden inside the visibilitychange
    // handler, mirroring the reference poller — so the flip must be paired
    // with the event, exactly as a real tab-switch fires both together.
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(calls).toBe(1); // no polling while hidden

    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(calls).toBe(2); // immediate refetch on focus
  });

  it("removes the visibilitychange listener on unmount", async () => {
    // `alive` already blocks a leaked listener's fetch, so counting fetches
    // cannot see this leak — the listener itself has to be observed. Each
    // leaked one retains the whole effect closure for the life of the tab.
    const added: unknown[] = [];
    const removed: unknown[] = [];
    const addSpy = vi.spyOn(document, "addEventListener").mockImplementation((t, h, o) => {
      if (t === "visibilitychange") added.push(h);
      return EventTarget.prototype.addEventListener.call(document, t, h, o);
    });
    const removeSpy = vi.spyOn(document, "removeEventListener").mockImplementation((t, h, o) => {
      if (t === "visibilitychange") removed.push(h);
      return EventTarget.prototype.removeEventListener.call(document, t, h, o);
    });

    const fetcher = vi.fn(async () => 1);
    const { unmount } = renderHook(() => usePolledResource(fetcher, { intervalMs: 1000 }));
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    unmount();

    expect(added.length).toBeGreaterThan(0);
    expect(removed).toEqual(added);
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it("a fetch resolving after unmount does not reschedule the loop", async () => {
    let release: (v: string) => void = () => {};
    const fetcher = vi.fn(() => new Promise<string>((r) => { release = r; }));

    const { unmount } = renderHook(() => usePolledResource(fetcher, { intervalMs: 1000 }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    unmount();
    await act(async () => { release("late"); await vi.advanceTimersByTimeAsync(5000); });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("restarts the loop when a declared dep changes", async () => {
    const seen: string[] = [];
    const { rerender } = renderHook(
      ({ q }: { q: string }) =>
        usePolledResource(async () => { seen.push(q); return q; }, {
          intervalMs: 10000,
          deps: [q],
        }),
      { initialProps: { q: "all" } }
    );
    await waitFor(() => expect(seen).toEqual(["all"]));

    // A server-side filter change must refetch now, not at the next tick.
    await act(async () => { rerender({ q: "building" }); await vi.advanceTimersByTimeAsync(0); });
    expect(seen).toEqual(["all", "building"]);
  });

  it("an inline (identity-changing) fetcher does not restart the loop", async () => {
    let calls = 0;
    const { rerender } = renderHook(
      ({ n }: { n: number }) =>
        usePolledResource(async () => { calls++; return n; }, { intervalMs: 1000 }),
      { initialProps: { n: 1 } }
    );
    await waitFor(() => expect(calls).toBe(1));

    // Re-render with a brand-new inline fetcher identity several times.
    rerender({ n: 2 });
    rerender({ n: 3 });

    // If the effect restarted on every fetcher identity change, this would
    // have fired extra immediate fetches beyond the single initial one.
    expect(calls).toBe(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(calls).toBe(2);
  });
});
