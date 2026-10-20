// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext, type AppRouterInstance } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import type { Locale } from "@/lib/i18n/locale";
import { sharedMessages } from "@/lib/i18n/shared";
import { ToastProvider, useToast } from "./Toast";

function unexpectedNavigation(): never {
  throw new Error("Unexpected navigation from toast test");
}

const router: AppRouterInstance = {
  back: unexpectedNavigation, forward: unexpectedNavigation, refresh: unexpectedNavigation,
  push: unexpectedNavigation, replace: unexpectedNavigation, prefetch: unexpectedNavigation,
  bfcacheId: "toast-test",
};
const CLOCK_START = 1_800_000_000_000;
const frames = new Map<number, FrameRequestCallback>();
let frameId = 0;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
  vi.setSystemTime(CLOCK_START);
  frames.clear();
  frameId = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames.delete(id); });
});

afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function ToastActions() {
  const { toast } = useToast();
  return <>
    <button onClick={() => toast("Long toast", "info", 4000)}>Long</button>
    <button onClick={() => toast("Short toast", "warning", 2000)}>Short</button>
  </>;
}

function renderToasts(locale: Locale = "en") {
  return render(
    <AppRouterContext.Provider value={router}>
      <LocaleProvider initialLocale={locale}>
        <ToastProvider><ToastActions /></ToastProvider>
      </LocaleProvider>
    </AppRouterContext.Provider>
  );
}

function frameAt(now: number) {
  vi.setSystemTime(CLOCK_START + now);
  const pending = [...frames.values()];
  frames.clear();
  act(() => { pending.forEach(callback => callback(now)); });
}

function toastCard(message: string) {
  const card = screen.getByText(message).closest(".toast-card");
  if (!card) throw new Error("Toast message has no rendered card");
  return card;
}

function progressWidth(message: string) {
  return toastCard(message).querySelector<HTMLElement>("[style]")?.style.width;
}

it("shrinks progress from wall time delivered by RAF, clamping a late frame at zero", () => {
  renderToasts();
  fireEvent.click(screen.getByRole("button", { name: "Long" }));
  expect(progressWidth("Long toast")).toBe("100%");
  frameAt(1000);
  expect(progressWidth("Long toast")).toBe("75%");
  frameAt(3000);
  expect(progressWidth("Long toast")).toBe("25%");
  frameAt(5000);
  expect(progressWidth("Long toast")).toBe("0%");
});

it("keeps concurrent toast progress tied to each creation time and custom duration", () => {
  renderToasts();
  fireEvent.click(screen.getByRole("button", { name: "Long" }));
  frameAt(1000);
  fireEvent.click(screen.getByRole("button", { name: "Short" }));
  expect(progressWidth("Long toast")).toBe("75%");
  expect(progressWidth("Short toast")).toBe("100%");
  frameAt(2500);
  expect(progressWidth("Long toast")).toBe("37.5%");
  expect(progressWidth("Short toast")).toBe("25%");
});

it("starts exit at the custom duration and removes the card after 320ms", () => {
  renderToasts();
  fireEvent.click(screen.getByRole("button", { name: "Short" }));
  act(() => { vi.advanceTimersByTime(1999); });
  expect(toastCard("Short toast").classList.contains("toast-exit")).toBe(false);
  act(() => { vi.advanceTimersByTime(1); });
  expect(toastCard("Short toast").classList.contains("toast-exit")).toBe(true);
  act(() => { vi.advanceTimersByTime(319); });
  expect(screen.queryByText("Short toast")).not.toBeNull();
  act(() => { vi.advanceTimersByTime(1); });
  expect(screen.queryByText("Short toast")).toBeNull();
  expect(frames.size).toBe(0);
});

describe.each(["en", "ru"] as const)("manual dismissal in %s", locale => {
  it("exits through the localized dismiss button without dismissing another toast", () => {
    renderToasts(locale);
    fireEvent.click(screen.getByRole("button", { name: "Long" }));
    fireEvent.click(screen.getByRole("button", { name: "Short" }));
    fireEvent.click(screen.getAllByRole("button", { name: sharedMessages[locale].dismiss })[0]);
    expect(toastCard("Long toast").classList.contains("toast-exit")).toBe(true);
    expect(toastCard("Short toast").classList.contains("toast-exit")).toBe(false);
    act(() => { vi.advanceTimersByTime(320); });
    expect(screen.queryByText("Long toast")).toBeNull();
    expect(screen.queryByText("Short toast")).not.toBeNull();
  });
});

it("cancels scheduled RAF callbacks and auto-dismiss timers on unmount", () => {
  const { unmount } = renderToasts();
  fireEvent.click(screen.getByRole("button", { name: "Long" }));
  fireEvent.click(screen.getByRole("button", { name: "Short" }));
  expect(frames.size).toBe(2);
  expect(vi.getTimerCount()).toBe(2);
  unmount();
  expect(frames.size).toBe(0);
  expect(vi.getTimerCount()).toBe(0);
});
