// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FreshnessBadge } from "./FreshnessBadge";

// This project registers no global setup file, so React Testing Library's
// auto-cleanup never self-installs (see ReplicaCard.test.tsx).
afterEach(cleanup);

describe("FreshnessBadge", () => {
  it("renders nothing when lastUpdated is null and no error", () => {
    const { container } = render(<FreshnessBadge lastUpdated={null} error={null} />);
    expect(container.textContent).toBe("");
  });

  it("renders nothing for an empty list, but still reports an error", () => {
    const stamp = Date.now() - 12000;
    const healthy = render(<FreshnessBadge lastUpdated={stamp} error={null} count={0} />);
    expect(healthy.container.textContent).toBe("");
    const failed = render(<FreshnessBadge lastUpdated={stamp} error={new Error("x")} count={0} />);
    expect(failed.container.textContent).toContain("Stale");
    const filled = render(<FreshnessBadge lastUpdated={stamp} error={null} count={3} />);
    expect(filled.container.textContent).toContain("Updated");
  });

  it("says just now under one minute instead of counting seconds", () => {
    const stamp = Date.now() - 12000;
    const { container } = render(<FreshnessBadge lastUpdated={stamp} error={null} />);
    expect(within(container).getByText("Updated just now")).toBeTruthy();
  });

  it("keeps counting the age during an outage without new props", () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<FreshnessBadge lastUpdated={Date.now()} error={new Error("x")} />);
      act(() => { vi.advanceTimersByTime(5 * 60000 + 1000); });
      expect(container.textContent).toContain("5m ago");
      act(() => { vi.advanceTimersByTime(10 * 60000); });
      expect(container.textContent).toContain("15m ago");
    } finally { vi.useRealTimers(); }
  });

  it("leaves just now once the clock passes one minute without new props", () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<FreshnessBadge lastUpdated={Date.now()} error={null} />);
      expect(within(container).getByText("Updated just now")).toBeTruthy();
      act(() => { vi.advanceTimersByTime(70000); });
      expect(container.textContent).not.toContain("just now");
    } finally { vi.useRealTimers(); }
  });

  it("shows relative time from one minute on", () => {
    const stamp = Date.now() - 125000;
    const { container } = render(<FreshnessBadge lastUpdated={stamp} error={null} />);
    expect(within(container).getByText(/Updated 2m ago/)).toBeTruthy();
  });

  it("shows the stale/error state with a working Retry", () => {
    const onRetry = vi.fn();
    const stamp = Date.now() - 120000;
    const { container } = render(
      <FreshnessBadge lastUpdated={stamp} error={new Error("boom")} onRetry={onRetry} />
    );
    const badge = within(container);

    const status = badge.getByText(/Stale/);
    expect(status.textContent).toMatch(/2m ago/);

    fireEvent.click(badge.getByText("Retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
