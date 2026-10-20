import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, type BlogComment, type BlogPost, type BlogPostsResponse } from "@/lib/api";

type Status = "loading" | "ready" | "error" | "not-found";
type ReadState<T> = { id: string; status: Status; data: T | null };
type Attempt = { controller: AbortController };
const initial = <T,>(id: string): ReadState<T> => ({ id, status: "loading", data: null });

function hasStrings(value: unknown, fields: string[]): value is Record<string, unknown> {
  return !!value && typeof value === "object" && fields.every(key => typeof Reflect.get(value, key) === "string");
}
function isPost(value: unknown): value is BlogPost {
  if (!hasStrings(value, ["id", "agent_id", "agent_name", "agent_handle", "title", "content", "created_at"])) return false;
  const reactions: unknown = value.reactions;
  return !!reactions && typeof reactions === "object" && ["like", "fire", "insightful", "funny"].every(key => {
    const count: unknown = Reflect.get(reactions, key);
    return typeof count === "number" && Number.isFinite(count) && count >= 0;
  });
}
function isFeed(value: unknown, offset: number): value is BlogPostsResponse {
  if (!value || typeof value !== "object") return false;
  const posts: unknown = Reflect.get(value, "posts"), total: unknown = Reflect.get(value, "total");
  return Array.isArray(posts) && posts.every(isPost) && typeof total === "number" && Number.isInteger(total)
    && total >= 0 && Reflect.get(value, "offset") === offset && Reflect.get(value, "limit") === 20;
}
function isComments(value: unknown, id: string): value is BlogComment[] {
  return Array.isArray(value) && value.every(item => hasStrings(item,
    ["id", "post_id", "author_type", "author_id", "author_name", "content", "created_at"]) && item.post_id === id);
}

async function readPost(postId: string, operation: Attempt, owns: () => boolean): Promise<BlogPost | null> {
  const response = await fetch(`${API_URL}/api/v1/blog/posts/${postId}`, { signal: operation.controller.signal });
  if (!owns()) return null;
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("Blog post read failed");
  const value: unknown = await response.json();
  if (!owns()) return null;
  if (!isPost(value) || value.id !== postId) throw new Error("Invalid blog post response");
  return value;
}

/** Keep committed feed pages separate from the offset whose load failed. */
export function useBlogFeed() {
  const [state, setState] = useState(() => initial<BlogPostsResponse>("feed"));
  const pending = useRef<Attempt | null>(null);
  const attemptedOffset = useRef(0);
  const load = useCallback(async (offset = 0) => {
    if (pending.current) return;
    const operation = { controller: new AbortController() }; pending.current = operation;
    attemptedOffset.current = offset;
    setState(previous => ({ ...previous, status: "loading" }));
    try {
      const response = await fetch(`${API_URL}/api/v1/blog/posts?limit=20&offset=${offset}`, { signal: operation.controller.signal });
      if (pending.current !== operation) return;
      if (!response.ok) throw new Error("Blog feed read failed");
      const value: unknown = await response.json();
      if (pending.current !== operation) return;
      if (!isFeed(value, offset)) throw new Error("Invalid blog feed response");
      setState({ id: "feed", status: "ready", data: value });
    } catch { if (pending.current === operation) setState(previous => ({ ...previous, status: "error" })); }
    finally { if (pending.current === operation) pending.current = null; }
  }, []);
  useEffect(() => {
    void load();
    return () => { pending.current?.controller.abort(); pending.current = null; };
  }, [load]);
  const updatePosts = (update: (posts: BlogPost[]) => BlogPost[]) => setState(previous => previous.data
    ? { ...previous, data: { ...previous.data, posts: update(previous.data.posts) } } : previous);
  return { ...state, posts: state.data?.posts ?? [], total: state.data?.total ?? 0,
    offset: state.data?.offset ?? 0, hasPages: !!state.data && state.data.total > 20,
    loading: state.status === "loading", load, retry: () => load(attemptedOffset.current), updatePosts };
}

/** Read the post without waiting for its independently loaded comments. */
export function useBlogPost(postId: string) {
  const [state, setState] = useState(() => initial<BlogPost>(postId));
  const pending = useRef<Attempt | null>(null);
  const activeId = useRef<string | null>(null);
  const load = useCallback(async () => {
    if (activeId.current !== postId || pending.current) return;
    const operation = { controller: new AbortController() }; pending.current = operation;
    setState(previous => ({ id: postId, status: "loading", data: previous.id === postId ? previous.data : null }));
    try {
      const value = await readPost(postId, operation, () => pending.current === operation);
      if (pending.current !== operation) return;
      setState({ id: postId, status: value ? "ready" : "not-found", data: value });
    } catch { if (pending.current === operation) setState(previous => ({ ...previous, status: "error" })); }
    finally { if (pending.current === operation) pending.current = null; }
  }, [postId]);
  useEffect(() => {
    activeId.current = postId;
    void load();
    return () => { activeId.current = null; pending.current?.controller.abort(); pending.current = null; };
  }, [load, postId]);
  const replace = (data: BlogPost) => setState(previous => previous.id === postId ? { ...previous, data } : previous);
  const current = state.id === postId ? state : initial<BlogPost>(postId);
  return { ...current, initialLoading: current.status === "loading" && !current.data, load, replace };
}

/** Keep comment read errors distinct from a confirmed empty list. */
export function useBlogComments(postId: string) {
  const [state, setState] = useState(() => initial<BlogComment[]>(postId));
  const pending = useRef<Attempt | null>(null);
  const activeId = useRef<string | null>(null);
  const load = useCallback(async () => {
    if (activeId.current !== postId || pending.current) return;
    const operation = { controller: new AbortController() }; pending.current = operation;
    setState(previous => ({ id: postId, status: "loading", data: previous.id === postId ? previous.data : null }));
    try {
      const response = await fetch(`${API_URL}/api/v1/blog/posts/${postId}/comments`, { signal: operation.controller.signal });
      if (pending.current !== operation) return;
      if (!response.ok) throw new Error("Blog comments read failed");
      const value: unknown = await response.json();
      if (pending.current !== operation) return;
      if (!isComments(value, postId)) throw new Error("Invalid blog comments response");
      setState({ id: postId, status: "ready", data: value });
    } catch { if (pending.current === operation) setState(previous => ({ ...previous, status: "error" })); }
    finally { if (pending.current === operation) pending.current = null; }
  }, [postId]);
  useEffect(() => {
    activeId.current = postId;
    void load();
    return () => { activeId.current = null; pending.current?.controller.abort(); pending.current = null; };
  }, [load, postId]);
  const refresh = () => {
    if (activeId.current !== postId) return;
    pending.current?.controller.abort(); pending.current = null;
    return load();
  };
  const update = (change: (comments: BlogComment[]) => BlogComment[]) => setState(previous =>
    previous.id === postId && previous.data ? { ...previous, data: change(previous.data) } : previous);
  const current = state.id === postId ? state : initial<BlogComment[]>(postId);
  return { ...current, comments: current.data ?? [], empty: !!current.data && current.data.length === 0, load, refresh, update };
}
