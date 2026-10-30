// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeStyles } from "./HomeStyles";
import { PausableStrip } from "./PausableStrip";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubReducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matches && query.includes("prefers-reduced-motion"),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

const renderStrip = () =>
  render(
    <PausableStrip label="Live activity" pauseLabel="Pause">
      <a href="/a">first</a>
    </PausableStrip>,
  );

describe("PausableStrip", () => {
  it("scrolls by default and exposes a pause button that toggles motion (WCAG 2.2.2)", () => {
    stubReducedMotion(false);
    const { container } = renderStrip();
    const strip = container.querySelector("[data-strip]");
    expect(strip?.getAttribute("data-paused")).toBe("false");
    const button = screen.getByRole("button", { name: "Pause" });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(button);
    expect(strip?.getAttribute("data-paused")).toBe("true");
    expect(screen.getByRole("button", { name: "Pause" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("renders the looping copy hidden from assistive tech and keyboard", () => {
    stubReducedMotion(false);
    const { container } = renderStrip();
    const copies = container.querySelectorAll("[data-strip-copy]");
    expect(copies).toHaveLength(2);
    expect(copies[1]?.getAttribute("aria-hidden")).toBe("true");
    expect(copies[1]?.hasAttribute("inert")).toBe(true);
  });

  it("stays paused, without a button and without the looping copy, under prefers-reduced-motion", () => {
    stubReducedMotion(true);
    const { container } = renderStrip();
    expect(container.querySelector("[data-strip]")?.getAttribute("data-paused")).toBe("true");
    expect(screen.queryByRole("button")).toBeNull();
    expect(container.querySelectorAll("[data-strip-copy]")).toHaveLength(1);
  });

  it("keeps the pause button outside the focus-within pause scope", () => {
    const { container } = render(<HomeStyles />);
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain("[data-strip] .strip-viewport:focus-within .strip-track");
    expect(css).not.toContain("[data-strip]:focus-within");
  });
});
