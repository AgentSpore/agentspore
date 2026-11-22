// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectTabs } from "./ProjectTabs";

afterEach(cleanup);
const TABS = [
  { key: "overview", label: "Overview" },
  { key: "chat", label: "Discussion" },
  { key: "ownership", label: "Ownership" },
] as const;

it("renders one non-wrapping scrollable row and marks the current tab", () => {
  const onSelect = vi.fn();
  const { container } = render(<ProjectTabs tabs={[...TABS]} active="chat" onSelect={onSelect} />);
  const row = container.firstElementChild as HTMLElement;
  expect(row.className).toContain("flex-nowrap");
  expect(row.className).toContain("overflow-x-auto");
  expect(row.className).not.toContain("grid");
  expect(screen.getByRole("button", { name: "Discussion" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Overview" }).getAttribute("aria-pressed")).toBe("false");
  for (const b of screen.getAllByRole("button")) {
    expect(b.className).toContain("shrink-0");
    expect(b.className).toContain("whitespace-nowrap");
    expect(b.className).toContain("min-h-12");
  }
  fireEvent.click(screen.getByRole("button", { name: "Ownership" }));
  expect(onSelect).toHaveBeenCalledWith("ownership");
});
