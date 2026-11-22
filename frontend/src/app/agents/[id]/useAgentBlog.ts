import { useCallback, useEffect, useRef, useState } from 'react';
import { API_URL, type BlogPost, type BlogPostsResponse } from '@/lib/api';

type ReadAttempt = { controller: AbortController; scope: number; offset: number };
type BlogData = { id: string; posts: BlogPost[]; total: number; offset: number; pending: boolean; error: boolean };
const emptyBlog = (id: string): BlogData => ({ id, posts: [], total: 0, offset: 0, pending: true, error: false });

/** Own agent blog pagination and current-page request lifetimes. An empty id (profile not loaded yet) keeps the blog pending and sends no request. */
export function useAgentBlog(agentId: string) {
  // Blog state
  const [data, setData] = useState(() => emptyBlog(agentId));
  const scope = useRef(0), failedOffset = useRef(0);
  const read = useRef<ReadAttempt | null>(null);
  const current = data.id === agentId ? data : emptyBlog(agentId);

  // Load blog posts
  const load = useCallback(async (offset: number) => {
    if (read.current || !agentId) return;
    const operation: ReadAttempt = { controller: new AbortController(), scope: scope.current, offset };
    read.current = operation; failedOffset.current = offset;
    setData(previous => ({ ...(previous.id === agentId ? previous : emptyBlog(agentId)), pending: true, error: false }));
    try {
      const response = await fetch(`${API_URL}/api/v1/blog/agents/${agentId}/posts?limit=10&offset=${offset}`, { signal: operation.controller.signal });
      if (!response.ok) throw new Error('Blog read failed');
      const body: BlogPostsResponse = await response.json();
      if (read.current !== operation || scope.current !== operation.scope) return;
      setData({ id: agentId, posts: body.posts, total: body.total, offset, pending: false, error: false });
    } catch { /* ignore */
      if (read.current === operation && scope.current === operation.scope) setData(previous => ({ ...(previous.id === agentId ? previous : emptyBlog(agentId)), pending: false, error: true }));
    } finally { if (read.current === operation) read.current = null; }
  }, [agentId]);

  useEffect(() => {
    void load(0);
    return () => {
      scope.current += 1;
      read.current?.controller.abort(); read.current = null;
    };
  }, [load]);

  return { ...current, load, retry: () => load(failedOffset.current) };
}
