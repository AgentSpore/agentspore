// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ScrollToTop from "./ScrollToTop";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
beforeEach(() => { vi.stubGlobal("scrollY", 0); vi.stubGlobal("scrollTo", vi.fn()); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("keeps the hidden control outside keyboard navigation and the accessible tree", () => {
  const { container } = render(<LocaleProvider initialLocale="en"><ScrollToTop /></LocaleProvider>);
  expect(screen.queryByRole("button")).toBeNull();
  expect(container.querySelector("button")?.tabIndex).toBe(-1);
  vi.stubGlobal("scrollY", 401); fireEvent.scroll(window);
  expect(screen.getByRole("button", { name: "Scroll to top" }).tabIndex).toBe(0);
  vi.stubGlobal("scrollY", 0); fireEvent.scroll(window);
  expect(screen.queryByRole("button")).toBeNull();
});

it.each([true, false])("honors reduced motion (%s) for an actual scroll action", reduced => {
  vi.stubGlobal("matchMedia", () => ({ matches: reduced }));
  render(<LocaleProvider initialLocale="en"><ScrollToTop /></LocaleProvider>);
  vi.stubGlobal("scrollY", 401); fireEvent.scroll(window);
  const button = screen.getByRole("button", { name: "Scroll to top" });
  act(() => { button.focus(); });
  vi.mocked(window.scrollTo).mockImplementation(() => { expect(document.activeElement).not.toBe(button); });
  fireEvent.click(button);
  expect(window.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: reduced ? "auto" : "smooth" });
});

it("uses the existing Russian label while preserving scroll visibility", () => {
  render(<LocaleProvider initialLocale="ru"><ScrollToTop /></LocaleProvider>);
  vi.stubGlobal("scrollY", 401); fireEvent.scroll(window);
  expect(screen.getByRole("button", { name: "Наверх" })).toBeTruthy();
});

function mountWithFocusTargets(rect = new DOMRect(240, 780, 44, 44)) {
  let targetRect = rect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.textContent === "Target") return targetRect;
    if (this.textContent === "Other") return new DOMRect(10, 100, 44, 44);
    return new DOMRect(240, 780, 44, 44);
  });
  const view = render(<LocaleProvider initialLocale="en"><button>Target</button><button>Other</button><ScrollToTop /></LocaleProvider>);
  vi.stubGlobal("scrollY", 401); fireEvent.scroll(window);
  return { ...view, moveTarget: () => { targetRect = new DOMRect(10, 100, 44, 44); } };
}

it("hides an overlapping floating control on real focus and restores it after blur", () => {
  mountWithFocusTargets();
  const floating = screen.getByRole("button", { name: "Scroll to top" });
  const target = screen.getByRole("button", { name: "Target" });
  act(() => { target.focus(); });
  expect(screen.queryByRole("button", { name: "Scroll to top" })).toBeNull();
  expect(floating.getAttribute("aria-hidden")).toBe("true");
  expect(floating.tabIndex).toBe(-1);
  expect(floating).toHaveProperty("disabled", true);
  act(() => { target.blur(); });
  expect(screen.getByRole("button", { name: "Scroll to top" })).toBeTruthy();
});

it("keeps nonoverlapping and own focus visible and recomputes on resize", () => {
  const { moveTarget } = mountWithFocusTargets();
  act(() => { screen.getByRole("button", { name: "Other" }).focus(); });
  const floating = screen.getByRole("button", { name: "Scroll to top" });
  act(() => { floating.focus(); });
  expect(screen.getByRole("button", { name: "Scroll to top" })).toBe(floating);
  act(() => { screen.getByRole("button", { name: "Target" }).focus(); });
  expect(screen.queryByRole("button", { name: "Scroll to top" })).toBeNull();
  moveTarget(); fireEvent.resize(window);
  expect(screen.getByRole("button", { name: "Scroll to top" })).toBeTruthy();
});

it("removes every installed visibility listener on unmount", () => {
  const add = vi.spyOn(window, "addEventListener");
  const remove = vi.spyOn(window, "removeEventListener");
  const { unmount } = mountWithFocusTargets();
  const listeners = add.mock.calls.filter(([name]) => ["scroll", "resize", "focusin", "focusout"].includes(name));
  expect(listeners.map(([name]) => name)).toEqual(expect.arrayContaining(["scroll", "resize", "focusin", "focusout"]));
  unmount();
  for (const [name, listener] of listeners) {
    expect(remove).toHaveBeenCalledWith(name, listener);
  }
});

it("hides the floating animation envelope beside a focused target", () => {
  mountWithFocusTargets(new DOMRect(240, 828, 44, 44));
  const target = screen.getByRole("button", { name: "Target" });
  target.style.outline = "none";
  act(() => { target.focus(); });
  expect(screen.queryByRole("button", { name: "Scroll to top" })).toBeNull();
});
