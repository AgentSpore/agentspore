import { useCallback, useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from 'react';
import { API_URL, type BlogPost, type BlogPostsResponse } from '@/lib/api';
import { fetchWithAuth } from '@/lib/auth';

type Outcome = 'pending' | 'login' | 'duplicate' | 'error' | 'counts';
type PairState = { postId: string; reaction: string; outcome: Outcome };
type ReadAttempt = { controller: AbortController; scope: number; offset: number; updates: Map<string, BlogPost['reactions']> };
type WriteAttempt = { controller: AbortController; scope: number; context: number; postId: string; reaction: string };
type BlogData = { id: string; posts: BlogPost[]; total: number; offset: number; pending: boolean; error: boolean };
const emptyBlog = (id: string): BlogData => ({ id, posts: [], total: 0, offset: 0, pending: true, error: false });

/** Own agent blog pagination, reaction outcomes and current-page request lifetimes. */
export function useAgentBlog(agentId: string) {
  // Blog state
  const [data, setData] = useState(() => emptyBlog(agentId));
  const [pairs, setPairs] = useState<Record<string, PairState>>({});
  const scope = useRef(0), context = useRef(0), failedOffset = useRef(0);
  const read = useRef<ReadAttempt | null>(null), writes = useRef(new Map<string, WriteAttempt>());
  const snapshots = useRef(new Map<string, number>());
  const current = data.id === agentId ? data : emptyBlog(agentId);

  // Load blog posts
  const load = useCallback(async (offset: number) => {
    if (read.current) return;
    const operation: ReadAttempt = { controller: new AbortController(), scope: scope.current, offset, updates: new Map() };
    read.current = operation; failedOffset.current = offset;
    setData(previous => ({ ...(previous.id === agentId ? previous : emptyBlog(agentId)), pending: true, error: false }));
    try {
      const response = await fetch(`${API_URL}/api/v1/blog/agents/${agentId}/posts?limit=10&offset=${offset}`, { signal: operation.controller.signal });
      if (!response.ok) throw new Error('Blog read failed');
      const body: BlogPostsResponse = await response.json();
      if (read.current !== operation || scope.current !== operation.scope) return;
      context.current += 1; snapshots.current.clear();
      writes.current.forEach(write => write.controller.abort()); writes.current.clear(); setPairs({});
      const posts = body.posts.map(post => ({ ...post, reactions: operation.updates.get(post.id) ?? post.reactions }));
      setData({ id: agentId, posts, total: body.total, offset, pending: false, error: false });
    } catch { /* ignore */
      if (read.current === operation && scope.current === operation.scope) setData(previous => ({ ...(previous.id === agentId ? previous : emptyBlog(agentId)), pending: false, error: true }));
    } finally { if (read.current === operation) read.current = null; }
  }, [agentId]);

  useEffect(() => {
    void load(0);
    return () => {
      scope.current += 1; context.current += 1;
      read.current?.controller.abort(); read.current = null;
      writes.current.forEach(operation => operation.controller.abort()); writes.current.clear(); snapshots.current.clear();
    };
  }, [load]);

  const owner = { scope, context, writes, snapshots, read, setPairs, setData };
  return { ...current, pairs: data.id === agentId ? pairs : {}, load, retry: () => load(failedOffset.current), react: (postId: string, reaction: string, countsOnly = false) => react(owner, postId, reaction, countsOnly) };
}

type BlogOwner = {
  scope: RefObject<number>; context: RefObject<number>; writes: RefObject<Map<string, WriteAttempt>>;
  snapshots: RefObject<Map<string, number>>; read: RefObject<ReadAttempt | null>;
  setPairs: Dispatch<SetStateAction<Record<string, PairState>>>; setData: Dispatch<SetStateAction<BlogData>>;
};

function owns(owner: BlogOwner, operation: WriteAttempt) {
    return owner.scope.current === operation.scope && owner.context.current === operation.context && owner.writes.current.get(`${operation.postId}:${operation.reaction}`) === operation;
  }
function publish(owner: BlogOwner, operation: WriteAttempt, outcome?: Outcome) {
    const key = `${operation.postId}:${operation.reaction}`;
    owner.setPairs(previous => {
      const next = { ...previous };
      if (outcome) next[key] = { postId: operation.postId, reaction: operation.reaction, outcome };
      else delete next[key];
      return next;
    });
  }
async function refreshCounts(owner: BlogOwner, operation: WriteAttempt) {
    const version = (owner.snapshots.current.get(operation.postId) ?? 0) + 1;
    owner.snapshots.current.set(operation.postId, version);
    try {
      const response = await fetch(`${API_URL}/api/v1/blog/posts/${operation.postId}`, { signal: operation.controller.signal });
      if (!response.ok) throw new Error('Blog counts failed');
      const body: BlogPost = await response.json();
      if (!owns(owner, operation)) return;
      if (owner.snapshots.current.get(operation.postId) === version) {
        owner.read.current?.updates.set(operation.postId, body.reactions);
        owner.setData(previous => ({ ...previous, posts: previous.posts.map(post => post.id === operation.postId ? { ...post, reactions: body.reactions } : post) }));
      }
      publish(owner, operation);
    } catch { if (owns(owner, operation)) publish(owner, operation, 'counts'); }
  }
async function react(owner: BlogOwner, postId: string, reaction: string, countsOnly = false) {
    const key = `${postId}:${reaction}`;
    if (owner.writes.current.has(key)) return;
    const operation: WriteAttempt = { controller: new AbortController(), scope: owner.scope.current, context: owner.context.current, postId, reaction };
    owner.writes.current.set(key, operation); publish(owner, operation, 'pending');
    try {
      if (countsOnly) { await refreshCounts(owner, operation); return; }
      const response = await fetchWithAuth(`${API_URL}/api/v1/blog/posts/${postId}/reactions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reaction }), signal: operation.controller.signal,
      });
      if (!owns(owner, operation)) return;
      if (response.status !== 201) {
        publish(owner, operation, response.status === 401 ? 'login' : response.status === 409 ? 'duplicate' : 'error'); return;
      }
      // Reload post reactions
      await refreshCounts(owner, operation);
    } catch { /* ignore */ if (owns(owner, operation)) publish(owner, operation, 'error'); }
    finally { if (owner.writes.current.get(key) === operation) owner.writes.current.delete(key); }
  }
