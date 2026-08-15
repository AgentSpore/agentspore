// @vitest-environment jsdom
import { cleanup, fireEvent, render, within } from "@testing-library/react";
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

  it("shows relative time when healthy", () => {
    const stamp = Date.now() - 12000;
    const { container } = render(<FreshnessBadge lastUpdated={stamp} error={null} />);
    expect(within(container).getByText(/Updated 12s ago/)).toBeTruthy();
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
