// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import BlogPostPage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import type { BlogComment, BlogPost } from "@/lib/api";

const route = vi.hoisted(() => ({ id: "post-a" }));
vi.mock("next/navigation", () => ({
  useParams: () => route, usePathname: () => `/blog/${route.id}`,
  useRouter: () => ({ refresh: vi.fn() }),
}));
const storage = new Map<string, string>();

beforeEach(() => {
  route.id = "post-a";
  storage.clear();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
    removeItem: (key: string) => { storage.delete(key); },
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Scene() {
  return <LocaleProvider initialLocale="en"><BlogPostPage /></LocaleProvider>;
}

// BlogService.get_post/get_comments serialize these fields for public readers.
function post(id = "post-a"): BlogPost {
  return { id, agent_id: "synthetic-agent", agent_name: "Synthetic Agent", agent_handle: "synthetic",
    title: `Synthetic ${id}`, content: `# Synthetic ${id}\n\nBody for ${id}`,
    reactions: { like: 0, fire: 0, insightful: 0, funny: 0 }, created_at: "2026-10-19T00:00:00Z" };
}
function comment(id = "post-a", content = `Comment for ${id}`): BlogComment {
  return { id: `comment-${id}`, post_id: id, author_type: "user", author_id: "synthetic-author",
    author_name: "Synthetic Author", content, created_at: "2026-10-19T00:00:00Z" };
}
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}
function deferredResponse() {
  let resolve: (value: Response) => void = () => { throw new Error("Response resolver not initialized"); };
  const promise = new Promise<Response>((complete) => { resolve = complete; });
  return { promise, resolve: (value: Response) => { resolve(value); } };
}
function stubResponses(responses: (Response | Promise<Response>)[]) {
  const stalled = deferredResponse();
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") throw new Error(`Unexpected ${init.method} ${url}`);
    return Promise.resolve(responses.shift() ?? stalled.promise);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

it("starts post and comment requests together and waits for both before rendering", async () => {
  const pendingPost = deferredResponse();
  const pendingComments = deferredResponse();
  const requests = stubResponses([pendingPost.promise, pendingComments.promise]);
  render(<Scene />);
  expect(requests.mock.calls.map(([url]) => new URL(url, "http://localhost").pathname)).toEqual([
    "/api/v1/blog/posts/post-a", "/api/v1/blog/posts/post-a/comments",
  ]);
  await act(async () => { pendingPost.resolve(response(post())); });
  expect(screen.queryByRole("heading", { name: "Synthetic post-a" })).toBeNull();
  await act(async () => { pendingComments.resolve(response([comment()])); });
  expect(screen.getByRole("heading", { name: "Synthetic post-a" })).toBeTruthy();
  expect(screen.getByText("Body for post-a")).toBeTruthy();
  expect(screen.getByText("Comment for post-a")).toBeTruthy();
  expect(requests).toHaveBeenCalledTimes(2);
});

it("keeps settled requests stable across parent rerenders and locale changes", async () => {
  const requests = stubResponses([response(post()), response([comment()])]);
  const view = render(<Scene />);
  await act(async () => {});
  expect(requests).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("heading", { name: "Synthetic post-a" })).toBeTruthy();
  view.rerender(<Scene />);
  fireEvent.click(screen.getAllByRole("button", { name: "Русский" })[0]);
  await act(async () => {});
  expect(screen.getByText("Комментарии")).toBeTruthy();
  expect(screen.getByText("Comment for post-a")).toBeTruthy();
  expect(requests).toHaveBeenCalledTimes(2);
});

it("loads one new post and comments pair after a completed route change", async () => {
  const requests = stubResponses([
    response(post()), response([comment()]), response(post("post-b")), response([comment("post-b")]),
  ]);
  const view = render(<Scene />);
  await act(async () => {});
  expect(requests).toHaveBeenCalledTimes(2);
  route.id = "post-b";
  view.rerender(<Scene />);
  await act(async () => {});
  expect(requests).toHaveBeenCalledTimes(4);
  expect(requests.mock.calls.slice(2).map(([url]) => new URL(url, "http://localhost").pathname)).toEqual([
    "/api/v1/blog/posts/post-b", "/api/v1/blog/posts/post-b/comments",
  ]);
  expect(screen.getByRole("heading", { name: "Synthetic post-b" })).toBeTruthy();
  expect(screen.getByText("Comment for post-b")).toBeTruthy();
  expect(screen.queryByText("Comment for post-a")).toBeNull();
});

it("refreshes only comments after posting and clears the submitted draft", async () => {
  storage.set("access_token", "synthetic-browser-test");
  let posted = false;
  let calls = 0;
  const stalled = deferredResponse();
  const requests = vi.fn(async (url: string, init?: RequestInit) => {
    if (++calls > 5) return stalled.promise;
    if (url.endsWith("/api/v1/auth/me")) return response({ id: "synthetic-author", name: "Synthetic Author",
      email: "synthetic@example.invalid", avatar_url: null, token_balance: 0, is_admin: false });
    if (url.endsWith("/comments") && init?.method === "POST") {
      expect(JSON.parse(String(init.body))).toEqual({ content: "New comment" });
      posted = true;
      return response({ id: "comment-new" }, 201);
    }
    if (url.endsWith("/comments")) return response(posted ? [comment("post-a", "New comment")] : []);
    if (url.endsWith("/posts/post-a")) return response(post());
    throw new Error(`Unexpected HTTP request: ${url}`);
  });
  vi.stubGlobal("fetch", requests);
  render(<Scene />);
  const draft = await screen.findByRole("textbox");
  fireEvent.change(draft, { target: { value: " New comment " } });
  fireEvent.click(screen.getByRole("button", { name: /^Post$/ }));
  await screen.findByText("New comment");
  expect(draft).toHaveProperty("value", "");
  expect(requests).toHaveBeenCalledTimes(5);
  expect(requests.mock.calls.filter(([url, init]) => url.endsWith("/comments") && !init?.method)).toHaveLength(2);
  expect(requests.mock.calls.filter(([url]) => url.endsWith("/posts/post-a"))).toHaveLength(1);
});

it.each(["404", "network"])("finishes initial loading with the existing not-found state after %s", async (failure) => {
  const failedPost = failure === "404" ? response({}, 404) : Promise.reject(new Error("Synthetic network failure"));
  const requests = stubResponses([failedPost, response({}, 503)]);
  render(<Scene />);
  expect(await screen.findByText("Post not found")).toBeTruthy();
  expect(screen.getByRole("main").id).toBe("main-content");
  expect(requests).toHaveBeenCalledTimes(2);
});

it("renders the post with the existing empty-comments state when comments fail", async () => {
  const requests = stubResponses([response(post()), response({}, 503)]);
  render(<Scene />);
  expect(await screen.findByRole("heading", { name: "Synthetic post-a" })).toBeTruthy();
  expect(screen.getByText("No comments yet")).toBeTruthy();
  expect(requests).toHaveBeenCalledTimes(2);
});
