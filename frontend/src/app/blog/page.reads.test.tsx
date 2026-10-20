// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import BlogFeedPage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import type { BlogPost } from "@/lib/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/blog", useRouter: () => ({ refresh: vi.fn() }) }));
const ERROR = "Could not load the data. Check your connection and try again.";
const PRIVATE = "Synthetic private read failure";
beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Scene() {
  return <LocaleProvider initialLocale="en"><BlogFeedPage /></LocaleProvider>;
}
function post(offset: number): BlogPost {
  return { id: `post-${offset}`, agent_id: "synthetic-agent", agent_name: "Synthetic Agent", agent_handle: "synthetic",
    title: `Page ${offset} publication`, content: "Synthetic body", created_at: "2026-10-20T00:00:00Z",
    reactions: { like: 0, fire: 0, insightful: 0, funny: 0 } };
}
function response(offset = 0, total = 41) {
  return new Response(JSON.stringify({ posts: [post(offset)], total, offset, limit: 20 }));
}
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error("Uninitialized response resolver"); };
  let reject: (reason: Error) => void = () => { throw new Error("Uninitialized response rejection"); };
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function setup() {
  const pending: ReturnType<typeof deferred<Response>>[] = [];
  const requests = vi.fn((input: string, init?: RequestInit) => {
    const target = new URL(input, "http://localhost");
    expect(target.pathname).toBe("/api/v1/blog/posts");
    expect(init?.method ?? "GET").toBe("GET");
    expect(new Headers(init?.headers).has("authorization")).toBe(false);
    expect(target.searchParams.get("limit")).toBe("20");
    expect([...target.searchParams.keys()].sort()).toEqual(["limit", "offset"]);
    const off = Number(target.searchParams.get("offset"));
    expect(Number.isInteger(off) && off >= 0 && off % 20 === 0).toBe(true);
    const held = deferred<Response>();
    pending.push(held);
    return held.promise;
  });
  vi.stubGlobal("fetch", requests);
  const view = render(<Scene />);
  return { pending, requests, view };
}
async function settle(held: ReturnType<typeof deferred<Response>>, value: Response) {
  await act(async () => { held.resolve(value); });
}
async function fail(held: ReturnType<typeof deferred<Response>>, mode: string) {
  await act(async () => {
    if (mode === "network") held.reject(new Error(PRIVATE));
    else held.resolve(new Response(JSON.stringify({ detail: PRIVATE }), { status: 503 }));
  });
}
function offsets(requests: ReturnType<typeof setup>["requests"]) {
  return requests.mock.calls.map(([url]) => new URL(url, "http://localhost").searchParams.get("offset"));
}

it("BR.1 keeps initial totals unknown until an explicit successful empty response", async () => {
  const { pending, requests } = setup();
  const main = within(screen.getByRole("main"));
  expect(main.queryByText("0", { exact: true })).toBeNull();
  expect(main.queryByText("No blog posts yet")).toBeNull();
  await settle(pending[0], new Response(JSON.stringify({ posts: [], total: 0, limit: 20, offset: 0 })));
  expect(main.getByText("No blog posts yet")).toBeTruthy();
  expect(main.getByText("0", { exact: true })).toBeTruthy();
  expect(requests).toHaveBeenCalledTimes(1);
});

it.each(["http", "network"])("BR.1 exposes initial %s failure and retries only the feed", async (mode) => {
  const { pending, requests } = setup();
  await fail(pending[0], mode);
  expect(screen.getByRole("alert").textContent).toContain(ERROR);
  expect(screen.queryByText("No blog posts yet")).toBeNull();
  expect(screen.queryByText(PRIVATE)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(offsets(requests)).toEqual(["0", "0"]);
  await settle(pending[1], new Response(JSON.stringify({ posts: [], total: 0, limit: 20, offset: 0 })));
  expect(screen.getByText("No blog posts yet")).toBeTruthy();
  expect(within(screen.getByRole("main")).queryByRole("alert")).toBeNull();
});

it.each(["http", "network"])("BR.2 preserves loaded page and retries failed offset after %s", async (mode) => {
  const { pending, requests } = setup();
  await settle(pending[0], response());
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await fail(pending[1], mode);
  expect(screen.getByRole("heading", { name: "Page 0 publication" })).toBeTruthy();
  expect(screen.getByText("Page 1 of 3")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain(ERROR);
  expect(screen.queryByText(PRIVATE)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(offsets(requests)).toEqual(["0", "20", "20"]);
  await settle(pending[2], response(20));
  expect(screen.getByRole("heading", { name: "Page 20 publication" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Page 0 publication" })).toBeNull();
  expect(screen.getByText("Page 2 of 3")).toBeTruthy();
});

it("BR.2 prevents duplicate pending navigation and preserves existing page boundaries", async () => {
  const { pending, requests } = setup();
  await settle(pending[0], response());
  expect(screen.getByRole("button", { name: "Prev" })).toHaveProperty("disabled", true);
  const next = screen.getByRole("button", { name: "Next" });
  fireEvent.click(next);
  expect(next).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Prev" })).toHaveProperty("disabled", true);
  fireEvent.click(next);
  expect(requests).toHaveBeenCalledTimes(2);
  await settle(pending[1], response(20));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await settle(pending[2], response(40));
  expect(screen.getByRole("button", { name: "Next" })).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Prev" }));
  await settle(pending[3], response(20));
  expect(offsets(requests)).toEqual(["0", "20", "40", "20"]);
});

it("BR.5 leaves current mount intact when old unmounted JSON completes", async () => {
  const old = setup();
  const json = deferred<unknown>();
  const body = new Response();
  Object.defineProperty(body, "json", { value: () => json.promise });
  await settle(old.pending[0], body);
  old.view.unmount();
  const current = setup();
  await settle(current.pending[0], new Response(JSON.stringify({
    posts: [{ ...post(0), id: "current-post", title: "Current publication" }], total: 41, limit: 20, offset: 0,
  })));
  await act(async () => { json.resolve({ posts: [post(0)], total: 41, offset: 0, limit: 20 }); });
  expect(screen.getByRole("heading", { name: "Current publication" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Page 0 publication" })).toBeNull();
  expect(old.requests).toHaveBeenCalledTimes(1);
  expect(current.requests).toHaveBeenCalledTimes(1);
});

it("BR.5 keeps settled feed reads stable across parent rerender and locale change", async () => {
  const { pending, requests, view } = setup();
  await settle(pending[0], response());
  view.rerender(<Scene />);
  fireEvent.click(screen.getAllByRole("button", { name: "Русский" })[0]);
  expect(screen.getByRole("heading", { name: "Page 0 publication" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Далее" })).toBeTruthy();
  expect(requests).toHaveBeenCalledTimes(1);
});
