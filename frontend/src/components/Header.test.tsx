// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { Header } from "./Header";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

const route = vi.hoisted(() => ({ pathname: "/showcase" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname, useRouter: () => ({ refresh: vi.fn() }) }));
const SYNTHETIC_USER = { id: "synthetic-user", name: "Synthetic Person", email: "person@example.test", avatar_url: null, token_balance: 0, is_admin: false };
beforeEach(() => {
  route.pathname = "/showcase";
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


it.each(["en", "ru"] as const)("exposes account disclosure state and restores focus on Escape in %s", async locale => {
  localStorage.setItem("access_token", "synthetic-access");
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(SYNTHETIC_USER)));
  render(<LocaleProvider initialLocale={locale}><Header /></LocaleProvider>);
  const account = await screen.findByRole("button", { name: /Synthetic Person/ });
  expect(account.getAttribute("aria-expanded")).toBe("false");
  account.focus();
  fireEvent.click(account);
  expect(account.getAttribute("aria-expanded")).toBe("true");
  const panelId = account.getAttribute("aria-controls");
  expect(panelId).toBeTruthy();
  const profile = screen.getByRole("link", { name: locale === "en" ? "My Profile" : "Мой профиль" });
  expect(document.getElementById(panelId ?? "")?.contains(profile)).toBe(true);
  profile.focus();
  fireEvent.keyDown(profile, { key: "Escape" });
  expect(screen.queryByRole("link", { name: locale === "en" ? "My Profile" : "Мой профиль" })).toBeNull();
  expect(account.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(account);
  expect(screen.queryByRole("menu")).toBeNull();
});

it.each(["en", "ru"] as const)("preserves account pointer dismissal and closed Escape focus in %s", async locale => {
  localStorage.setItem("access_token", "synthetic-access");
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(SYNTHETIC_USER)));
  render(<LocaleProvider initialLocale={locale}><Header /></LocaleProvider>);
  const account = await screen.findByRole("button", { name: /Synthetic Person/ });
  const more = screen.getByRole("button", { name: locale === "en" ? "More" : "Ещё" });
  more.focus();
  fireEvent.keyDown(more, { key: "Escape" });
  expect(document.activeElement).toBe(more);
  fireEvent.click(account);
  fireEvent.mouseDown(document.body);
  expect(account.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(account);
  fireEvent.click(screen.getByRole("link", { name: locale === "en" ? "My Agents" : "Мои агенты" }));
  expect(account.getAttribute("aria-expanded")).toBe("false");
});

it.each(["en", "ru"] as const)("restores the originating disclosure when account and More are open in %s", async locale => {
  localStorage.setItem("access_token", "synthetic-access");
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(SYNTHETIC_USER)));
  render(<LocaleProvider initialLocale={locale}><Header /></LocaleProvider>);
  const account = await screen.findByRole("button", { name: /Synthetic Person/ });
  const more = screen.getByRole("button", { name: locale === "en" ? "More" : "Ещё" });
  const profileName = locale === "en" ? "My Profile" : "Мой профиль";
  const chatName = locale === "en" ? "Chat" : "Чат";
  for (const origin of ["account", "more"]) {
    fireEvent.click(account);
    fireEvent.click(more);
    const link = screen.getByRole("link", { name: origin === "account" ? profileName : chatName });
    link.focus();
    fireEvent.keyDown(link, { key: "Escape" });
    expect(account.getAttribute("aria-expanded")).toBe("false");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(origin === "account" ? account : more);
  }
});


it.each(["en", "ru"] as const)("keeps Dashboard primary and groups all mobile routes in %s", locale => {
  render(<LocaleProvider initialLocale={locale}><Header /></LocaleProvider>);
  const dashboard = locale === "en" ? "Dashboard" : "Обзор";
  expect(screen.getByRole("link", { name: dashboard }).getAttribute("href")).toBe("/dashboard");
  fireEvent.click(screen.getByRole("button", { name: locale === "en" ? "More" : "Ещё" }));
  expect(screen.getAllByRole("link", { name: dashboard })).toHaveLength(1);
  fireEvent.keyDown(document, { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: locale === "en" ? "Menu" : "Меню" }));
  const groups = [
    { name: locale === "en" ? "Services for people" : "Сервисы для людей", routes: ["/showcase"] },
    { name: locale === "en" ? "Platform" : "Платформа", routes: ["/workspace", "/projects", "/agents", "/dashboard"] },
    { name: locale === "en" ? "Community and research" : "Сообщество и исследования", routes: ["/chat", "/battles", "/teams", "/blog"] },
  ];
  for (const group of groups) {
    expect(screen.getByRole("heading", { name: group.name })).toBeTruthy();
    const navigation = screen.getByRole("navigation", { name: group.name });
    expect(within(navigation).getAllByRole("link").map(link => link.getAttribute("href"))).toEqual(group.routes);
    for (const link of within(navigation).getAllByRole("link")) expect(link.textContent).not.toMatch(/^[>/$!^+@~]/);
  }
  fireEvent.click(within(screen.getByRole("navigation", { name: groups[2].name })).getByRole("link", { name: locale === "en" ? "Blog" : "Блог" }));
  expect(screen.queryByRole("navigation", { name: groups[0].name })).toBeNull();
});

it.each(["/projects/detail", "/projects-old"])("marks only the current route segment for %s", pathname => {
  route.pathname = pathname;
  render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>);
  expect(screen.getByRole("link", { name: "Projects" }).getAttribute("aria-current")).toBe(pathname === "/projects/detail" ? "page" : null);
});


it("keeps mobile navigation open and translates its groups when the locale changes", () => {
  render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>);
  const toggle = screen.getByRole("button", { name: "Menu" });
  fireEvent.click(toggle);
  expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).toBeTruthy();
  fireEvent.click(screen.getAllByRole("button", { name: "Русский" })[1]);
  expect(screen.getByRole("heading", { name: "Сервисы для людей" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Платформа" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Меню" }).getAttribute("aria-expanded")).toBe("true");
});

it("preserves mobile account controls and closes navigation when the profile is opened", async () => {
  localStorage.setItem("access_token", "synthetic-access");
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(SYNTHETIC_USER)));
  render(<LocaleProvider initialLocale="en"><Header /></LocaleProvider>);
  await screen.findByRole("button", { name: /Synthetic Person/ });
  const menu = screen.getByRole("button", { name: "Menu" });
  fireEvent.click(menu);
  expect(screen.getByRole("button", { name: /Sign Out/ })).toBeTruthy();
  expect(screen.getByRole("link", { name: /For AI agents/ }).getAttribute("href")).toContain("/skill.md");
  fireEvent.click(screen.getByRole("link", { name: "My Profile" }));
  expect(menu.getAttribute("aria-expanded")).toBe("false");
  expect(localStorage.removeItem).not.toHaveBeenCalled();
});
