// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { Header } from "./Header";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

vi.mock("next/navigation", () => ({ usePathname: () => "/showcase", useRouter: () => ({ refresh: vi.fn() }) }));
const SYNTHETIC_USER = { id: "synthetic-user", name: "Synthetic Person", email: "person@example.test", avatar_url: null, token_balance: 0, is_admin: false };
beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => values.set(key, value)),
    removeItem: vi.fn((key: string) => values.delete(key)),
  });
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(["en", "ru"] as const)("keeps services primary and chat secondary in %s", locale => {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
  const { container } = render(<LocaleProvider initialLocale={locale}><Header /></LocaleProvider>);
  const services = locale === "en" ? "Services" : "Сервисы";
  const chat = locale === "en" ? "Chat" : "Чат";
  expect(screen.getByRole("link", { name: new RegExp(services) }).getAttribute("href")).toBe("/showcase");
  expect(screen.queryByRole("link", { name: new RegExp(chat) })).toBeNull();
  expect(container.querySelector(".animate-ping")).toBeNull();
  expect(screen.getByRole("link", { name: locale === "en" ? "Workspace" : "Рабочая область" }).getAttribute("href")).toBe("/workspace");
  expect(screen.getByRole("link", { name: /Connect agent|Подключить агента/ }).getAttribute("href")).toBe("/workspace#connect-agent");
  fireEvent.click(screen.getByRole("button", { name: locale === "en" ? "More" : "Ещё" }));
  expect(screen.getByRole("link", { name: new RegExp(chat) }).getAttribute("href")).toBe("/chat");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.getByRole("button", { name: locale === "en" ? "More" : "Ещё" })).toBe(document.activeElement);
  expect(screen.queryByRole("link", { name: new RegExp(chat) })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: locale === "en" ? "More" : "Ещё" }));
  fireEvent.click(screen.getByRole("link", { name: new RegExp(chat) }));
  expect(screen.queryByRole("link", { name: new RegExp(chat) })).toBeNull();
});

it.each(["en", "ru"] as const)("closes mobile navigation on Escape and restores focus in %s", locale => {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
  render(<LocaleProvider initialLocale={locale}><Header /></LocaleProvider>);
  const menu = screen.getByRole("button", { name: locale === "en" ? "Menu" : "Меню" });
  fireEvent.click(menu);
  expect(screen.getByRole("link", { name: locale === "en" ? "Chat" : "Чат" })).toBeTruthy();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("link", { name: locale === "en" ? "Chat" : "Чат" })).toBeNull();
  expect(document.activeElement).toBe(menu);
  expect(menu.getAttribute("aria-expanded")).toBe("false");
});

it("hydrates anonymous navigation without reading storage during SSR or fetching a profile", async () => {
  const element = <LocaleProvider initialLocale="en"><Header /></LocaleProvider>;
  const container = document.createElement("div");
  document.body.append(container);
  container.innerHTML = renderToString(element);
  expect(localStorage.getItem).not.toHaveBeenCalled();
  expect(container.textContent).not.toContain("Sign In");
  const onRecoverableError = vi.fn();
  render(element, { container, hydrate: true, onRecoverableError });
  expect(await screen.findByRole("link", { name: "Sign In" })).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  expect(onRecoverableError).not.toHaveBeenCalled();
});

it("keeps authenticated controls pending until the profile resolves", async () => {
  localStorage.setItem("access_token", "synthetic-access");
  let resolveProfile: (response: Response) => void = () => {};
  vi.mocked(fetch).mockImplementation(() => new Promise<Response>(resolve => { resolveProfile = resolve; }));
  render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>);
  expect(screen.queryByRole("link", { name: "Sign In" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Synthetic Person/ })).toBeNull();
  await act(async () => { resolveProfile(new Response(JSON.stringify(SYNTHETIC_USER))); });
  expect(await screen.findByRole("button", { name: /Synthetic Person/ })).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([false, true])("preserves token refresh outcome (success=%s)", async success => {
  localStorage.setItem("access_token", "synthetic-access");
  localStorage.setItem("refresh_token", "synthetic-refresh");
  vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 401 }));
  vi.mocked(fetch).mockResolvedValueOnce(success ? new Response(JSON.stringify({ access_token: "synthetic-renewed", refresh_token: "synthetic-next" })) : new Response(null, { status: 401 }));
  if (success) vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(SYNTHETIC_USER)));
  render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>);
  if (success) {
    expect(await screen.findByRole("button", { name: /Synthetic Person/ })).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(3);
  } else {
    expect(await screen.findByRole("link", { name: "Sign In" })).toBeTruthy();
    expect(localStorage.removeItem).toHaveBeenCalledWith("access_token");
    expect(localStorage.removeItem).toHaveBeenCalledWith("refresh_token");
  }
  expect(fetch).toHaveBeenCalledTimes(success ? 3 : 2);
});

it("settles a failed profile request without refreshing or clearing credentials", async () => {
  localStorage.setItem("access_token", "synthetic-access");
  vi.mocked(fetch).mockRejectedValue(new Error("Synthetic network failure"));
  render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>);
  expect(await screen.findByRole("link", { name: "Sign In" })).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(localStorage.removeItem).not.toHaveBeenCalled();
});

it("hides the old profile when the same tab logs out", async () => {
  localStorage.setItem("access_token", "synthetic-access");
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(SYNTHETIC_USER)));
  render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>);
  expect(await screen.findByRole("button", { name: /Synthetic Person/ })).toBeTruthy();
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(screen.queryByRole("button", { name: /Synthetic Person/ }) === null).toBe(true);
});

it("ignores a delayed profile after an owner replacement", async () => {
  localStorage.setItem("access_token", "synthetic-access");
  let finish: (response: Response) => void = () => {};
  vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; })).mockResolvedValue(new Response(JSON.stringify({ ...SYNTHETIC_USER, name: "Replacement Person" })));
  render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>);
  localStorage.setItem("access_token", "replacement-access");
  await act(async () => { window.dispatchEvent(new Event("storage")); });
  await act(async () => { finish(new Response(JSON.stringify(SYNTHETIC_USER))); });
  expect(screen.queryByRole("button", { name: /Synthetic Person/ }) === null).toBe(true);
  expect(await screen.findByRole("button", { name: /Replacement Person/ })).toBeTruthy();
});

it("detects direct same-tab logout on the bounded poll and cleans up", async () => {
  vi.useFakeTimers();
  localStorage.setItem("access_token", "synthetic-access");
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(SYNTHETIC_USER)));
  let unmount: () => void = () => {};
  await act(async () => { ({ unmount } = render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>)); });
  expect(screen.getByRole("button", { name: /Synthetic Person/ })).toBeTruthy();
  localStorage.removeItem("access_token");
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.queryByRole("button", { name: /Synthetic Person/ }) === null).toBe(true);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers();
});
