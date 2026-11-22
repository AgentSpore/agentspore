// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectTabs, projectPanelId, projectTabId } from "./ProjectTabs";

afterEach(cleanup);
const TABS = [
  { key: "overview", label: "Overview" },
  { key: "chat", label: "Discussion" },
  { key: "ownership", label: "Ownership" },
] as const;

function setup(active = "chat", onSelect = vi.fn()) {
  const view = render(<ProjectTabs tabs={[...TABS]} active={active} onSelect={onSelect} label="Project sections" />);
  return { ...view, onSelect };
}
const tab = (name: string) => screen.getByRole("tab", { name });

it("renders one non-wrapping scrollable named tablist", () => {
  setup();
  const list = screen.getByRole("tablist", { name: "Project sections" });
  expect(list.className).toContain("flex-nowrap");
  expect(list.className).toContain("overflow-x-auto");
  expect(list.className).not.toContain("grid");
  expect(screen.queryAllByRole("button")).toHaveLength(0);
  for (const t of screen.getAllByRole("tab")) {
    expect(t.className).toContain("shrink-0");
    expect(t.className).toContain("whitespace-nowrap");
    expect(t.className).toContain("min-h-12");
  }
});

it("marks selection with aria-selected, roving tabindex and panel links", () => {
  setup();
  expect(tab("Discussion").getAttribute("aria-selected")).toBe("true");
  expect(tab("Overview").getAttribute("aria-selected")).toBe("false");
  expect(tab("Discussion").tabIndex).toBe(0);
  expect(tab("Overview").tabIndex).toBe(-1);
  expect(tab("Ownership").tabIndex).toBe(-1);
  expect(tab("Discussion").id).toBe(projectTabId("chat"));
  expect(tab("Discussion").getAttribute("aria-controls")).toBe(projectPanelId("chat"));
  expect(tab("Discussion").hasAttribute("aria-pressed")).toBe(false);
});

it("click selects the tab", () => {
  const { onSelect } = setup();
  fireEvent.click(tab("Ownership"));
  expect(onSelect).toHaveBeenCalledWith("ownership");
});

it.each([
  ["chat", "ArrowRight", "ownership"],
  ["ownership", "ArrowRight", "overview"],
  ["chat", "ArrowLeft", "overview"],
  ["overview", "ArrowLeft", "ownership"],
  ["chat", "Home", "overview"],
  ["chat", "End", "ownership"],
])("from %s, %s activates %s and moves focus", (from, key, to) => {
  const { onSelect } = setup(from);
  const label = TABS.find(t => t.key === to)!.label;
  fireEvent.keyDown(tab(TABS.find(t => t.key === from)!.label), { key });
  expect(onSelect).toHaveBeenCalledWith(to);
  expect(document.activeElement).toBe(tab(label));
});

it("ignores other keys", () => {
  const { onSelect } = setup();
  fireEvent.keyDown(tab("Discussion"), { key: "ArrowDown" });
  fireEvent.keyDown(tab("Discussion"), { key: "a" });
  expect(onSelect).not.toHaveBeenCalled();
});
