// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ScrollToTop from "./ScrollToTop";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
beforeEach(() => { vi.stubGlobal("scrollY", 0); vi.stubGlobal("scrollTo", vi.fn()); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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
  fireEvent.click(screen.getByRole("button", { name: "Scroll to top" }));
  expect(window.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: reduced ? "auto" : "smooth" });
});

it("uses the existing Russian label while preserving scroll visibility", () => {
  render(<LocaleProvider initialLocale="ru"><ScrollToTop /></LocaleProvider>);
  vi.stubGlobal("scrollY", 401); fireEvent.scroll(window);
  expect(screen.getByRole("button", { name: "Наверх" })).toBeTruthy();
});
