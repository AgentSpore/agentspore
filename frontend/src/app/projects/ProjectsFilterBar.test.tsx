// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectsFilterBar } from "./ProjectsFilterBar";
import { DEFAULT_FILTERS, type ProjectFilters } from "./projectFilters";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";
import { displayPublicLabel, publicMessages } from "@/lib/i18n/public";

vi.mock("next/navigation", () => ({ usePathname: () => "/projects", useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(cleanup);

function Harness({ initial }: { initial: ProjectFilters }) {
  const [filters, setFilters] = useState(initial);
  const [choice, setChoice] = useState<boolean | null>(null);
  return <ProjectsFilterBar filters={filters} onChange={patch => setFilters(f => ({ ...f, ...patch }))} disclosure={[choice, setChoice]} />;
}
const renderBar = (initial: ProjectFilters) =>
  render(<LocaleProvider initialLocale="en"><Harness initial={initial} /></LocaleProvider>);
const toggle = () => screen.getByRole("button", { name: new RegExp(`^${publicMessages.en.filtersLabel}`) });
const categoryName = docsMessages.en.projectCategoryFilter;

describe("Filters disclosure", () => {
  it("is closed by default and keeps search, status and sort outside it", () => {
    renderBar(DEFAULT_FILTERS);
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("group", { name: categoryName })).toBeNull();
    expect(screen.getByRole("textbox")).toBeTruthy();
    expect(screen.getByRole("group", { name: docsMessages.en.projectStatusFilter })).toBeTruthy();
    expect(screen.getByRole("button", { name: displayPublicLabel("en", "stars") })).toBeTruthy();
  });

  it.each([{ time: "7d" }, { category: "social" }] as Partial<ProjectFilters>[])("starts open for a non-default %o", patch => {
    renderBar({ ...DEFAULT_FILTERS, ...patch });
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("group", { name: categoryName })).toBeTruthy();
  });

  it("stays open after a choice is reset to All, and the user can close it", () => {
    renderBar({ ...DEFAULT_FILTERS, category: "social" });
    const group = () => screen.getByRole("group", { name: categoryName });
    fireEvent.click(within(group()).getByRole("button", { name: displayPublicLabel("en", "all") }));
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(toggle());
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
  });

  it("counts only the hidden active filters on the toggle", () => {
    renderBar({ ...DEFAULT_FILTERS, status: "deployed", sort: "stars", time: "7d" });
    expect(toggle().textContent).toBe(`${publicMessages.en.filtersLabel} (1)`);
  });
});
