// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCopyToClipboard } from "./useCopyToClipboard";

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("useCopyToClipboard", () => {
  it("copies, reports copied, then returns to idle after the delay", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const { result } = renderHook(() => useCopyToClipboard(500));
    expect(result.current.state).toBe("idle");

    await act(async () => { await result.current.copy("abc"); });
    expect(writeText).toHaveBeenCalledWith("abc");
    expect(result.current.state).toBe("copied");

    act(() => { vi.advanceTimersByTime(499); });
    expect(result.current.state).toBe("copied");
    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current.state).toBe("idle");
  });

  it("defaults to a 2000ms reset", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => undefined) } });
    const { result } = renderHook(() => useCopyToClipboard());
    await act(async () => { await result.current.copy("x"); });
    act(() => { vi.advanceTimersByTime(1999); });
    expect(result.current.state).toBe("copied");
    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current.state).toBe("idle");
  });

  it("reports failed when writeText rejects", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => { throw new Error("denied"); }) } });
    const { result } = renderHook(() => useCopyToClipboard());
    await act(async () => { await result.current.copy("x"); });
    expect(result.current.state).toBe("failed");
  });

  it("reports failed when the clipboard API is missing", async () => {
    vi.stubGlobal("navigator", {});
    const { result } = renderHook(() => useCopyToClipboard());
    await act(async () => { await result.current.copy("x"); });
    expect(result.current.state).toBe("failed");
  });

  it("arms no timer and sets no state when unmounted while the write is pending", async () => {
    let resolveWrite: () => void = () => undefined;
    const writeText = vi.fn(() => new Promise<void>((resolve) => { resolveWrite = resolve; }));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { result, unmount } = renderHook(() => useCopyToClipboard());
    let pending: Promise<void> = Promise.resolve();
    act(() => { pending = result.current.copy("x"); });
    unmount();
    await act(async () => { resolveWrite(); await pending; });
    expect(vi.getTimerCount()).toBe(0);
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("clears the pending timer on unmount", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => undefined) } });
    const { result, unmount } = renderHook(() => useCopyToClipboard());
    await act(async () => { await result.current.copy("x"); });
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
