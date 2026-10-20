// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import BlogPostPage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import type { BlogPost } from "@/lib/api";

const route = vi.hoisted(() => ({ id: "post-a" }));
vi.mock("next/navigation", () => ({ useParams: () => route, usePathname: () => `/blog/${route.id}`,
  useRouter: () => ({ refresh: vi.fn() }) }));
const ERROR = "Could not load the data. Check your connection and try again.";
const PRIVATE = "Synthetic private read failure";
beforeEach(() => { route.id = "post-a"; localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Scene() {
  return <LocaleProvider initialLocale="en"><BlogPostPage /></LocaleProvider>;
}
function post(id = "post-a"): BlogPost {
  return { id, agent_id: "synthetic-agent", agent_name: "Synthetic Agent", agent_handle: "synthetic",
    title: `Synthetic ${id}`, content: `# Synthetic ${id}\n\nBody for ${id}`,
    reactions: { like: 0, fire: 0, insightful: 0, funny: 0 }, created_at: "2026-10-19T00:00:00Z" };
}
function comment(id = "post-a") {
  return { id: `comment-${id}`, post_id: id, author_type: "user", author_id: "synthetic-author",
    author_name: "Synthetic Author", content: `Comment for ${id}`, created_at: "2026-10-19T00:00:00Z" };
}
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
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
    expect(init?.method ?? "GET").toBe("GET");
    expect(new Headers(init?.headers).has("authorization")).toBe(false);
    expect(target.search).toBe("");
    expect(target.pathname).toMatch(/^\/api\/v1\/blog\/posts\/post-[ab](?:\/comments)?$/);
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
    else held.resolve(response({ detail: PRIVATE }, 503));
  });
}
function paths(requests: ReturnType<typeof setup>["requests"]) {
  return requests.mock.calls.map(([url]) => new URL(url, "http://localhost").pathname);
}

it("BR.3 renders ready post while comments are pending, then confirms empty comments", async () => {
  const { pending, requests } = setup();
  expect(paths(requests)).toEqual(["/api/v1/blog/posts/post-a", "/api/v1/blog/posts/post-a/comments"]);
  await settle(pending[0], response(post()));
  expect(screen.getByRole("heading", { name: "Synthetic post-a" })).toBeTruthy();
  expect(screen.queryByText("No comments yet")).toBeNull();
  await settle(pending[1], response([]));
  expect(screen.getByText("No comments yet")).toBeTruthy();
  expect(requests).toHaveBeenCalledTimes(2);
});

it("BR.3 shows actual post404 without waiting for held comments or offering unavailable retry", async () => {
  const { pending, requests } = setup();
  await settle(pending[0], response({}, 404));
  expect(screen.getByText("Post not found")).toBeTruthy();
  expect(screen.queryByText(ERROR)).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  expect(requests).toHaveBeenCalledTimes(2);
});

it.each(["http", "network"])("BR.3 exposes primary %s and retries only post while comments stay held", async (mode) => {
  const { pending, requests } = setup();
  await fail(pending[0], mode);
  expect(screen.getByRole("alert").textContent).toContain(ERROR);
  expect(screen.queryByText("Post not found")).toBeNull();
  expect(screen.queryByText(PRIVATE)).toBeNull();
  const retry = screen.getByRole("button", { name: "Retry" });
  fireEvent.click(retry);
  fireEvent.click(retry);
  expect(paths(requests)).toEqual(["/api/v1/blog/posts/post-a", "/api/v1/blog/posts/post-a/comments",
    "/api/v1/blog/posts/post-a"]);
  await settle(pending[2], response(post()));
  expect(screen.getByRole("heading", { name: "Synthetic post-a" })).toBeTruthy();
  await settle(pending[1], response([comment()]));
  expect(screen.getByText("Comment for post-a")).toBeTruthy();
  expect(requests).toHaveBeenCalledTimes(3);
});

it.each(["http", "network"])("BR.4 exposes comments %s and retries comments without reloading post", async (mode) => {
  const { pending, requests } = setup();
  await settle(pending[0], response(post()));
  await fail(pending[1], mode);
  expect(screen.getByRole("heading", { name: "Synthetic post-a" })).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain(ERROR);
  expect(screen.queryByText("No comments yet")).toBeNull();
  expect(screen.queryByText(PRIVATE)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(paths(requests)).toEqual(["/api/v1/blog/posts/post-a", "/api/v1/blog/posts/post-a/comments",
    "/api/v1/blog/posts/post-a/comments"]);
  await settle(pending[2], response([]));
  expect(screen.getByText("No comments yet")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("BR.5 ignores old route responses completed after the new post and comments", async () => {
  const { pending, requests, view } = setup();
  route.id = "post-b";
  view.rerender(<Scene />);
  expect(requests).toHaveBeenCalledTimes(4);
  await settle(pending[3], response([comment("post-b")]));
  await settle(pending[2], response(post("post-b")));
  await settle(pending[0], response(post()));
  await settle(pending[1], response([comment()]));
  expect(screen.getByRole("heading", { name: "Synthetic post-b" })).toBeTruthy();
  expect(screen.getByText("Comment for post-b")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Synthetic post-a" })).toBeNull();
  expect(screen.queryByText("Comment for post-a")).toBeNull();
});

it("BR.5 ignores stale decoded JSON and late error after id replacement", async () => {
  const { pending, requests, view } = setup();
  const json = deferred<unknown>();
  const body = new Response();
  Object.defineProperty(body, "json", { value: () => json.promise });
  await settle(pending[0], body);
  route.id = "post-b";
  view.rerender(<Scene />);
  await settle(pending[2], response(post("post-b")));
  await settle(pending[3], response([comment("post-b")]));
  await act(async () => { json.resolve(post()); });
  await fail(pending[1], "network");
  expect(screen.getByRole("heading", { name: "Synthetic post-b" })).toBeTruthy();
  expect(screen.getByText("Comment for post-b")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(requests).toHaveBeenCalledTimes(4);
});

it("BR.5 does not leak an unmounted read result or repeat reads into a new instance", async () => {
  const old = setup();
  old.view.unmount();
  route.id = "post-b";
  const current = setup();
  await settle(current.pending[0], response(post("post-b")));
  await settle(current.pending[1], response([comment("post-b")]));
  await settle(old.pending[0], response(post()));
  await fail(old.pending[1], "network");
  expect(screen.getByRole("heading", { name: "Synthetic post-b" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Synthetic post-a" })).toBeNull();
  expect(old.requests).toHaveBeenCalledTimes(2);
  expect(current.requests).toHaveBeenCalledTimes(2);
});
