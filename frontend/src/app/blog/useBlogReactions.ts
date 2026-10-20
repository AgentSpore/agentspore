"use client";

import { useEffect, useMemo, useState, useSyncExternalStore, type Dispatch, type SetStateAction } from 'react';
import { API_URL, type BlogReactions } from '@/lib/api';
import { fetchWithAuth, getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from '@/lib/auth';

/** Existing global reaction kinds, shared by the feed and detail controls. */
export const blogReactionKinds = ['like', 'fire', 'insightful', 'funny'] as const;
/** Existing localized labels for each global reaction kind. */
export const blogReactionLabels = { like: 'blogLike', fire: 'blogFire', insightful: 'blogInsightful', funny: 'blogFunny' } as const;
type Reaction = typeof blogReactionKinds[number];
type Outcome = 'pending' | 'login' | 'error' | 'counts';
/** Visible outcome of one post and reaction pair. */
export type BlogReactionState = { postId: string; reaction: Reaction; outcome: Outcome };
type State = { owner: Owner | null; pairs: Record<string, BlogReactionState>; counts: Record<string, BlogReactions> };
type Operation = { key: string; postId: string; reaction: Reaction; controller: AbortController; accepted: boolean };
type Owner = { scope: string; generation: number; isActive: () => boolean; activate: () => void; dispose: () => void; writes: Map<string, Operation>; reads: Map<string, number>; setState: Dispatch<SetStateAction<State>> };
const empty = (owner: Owner | null): State => ({ owner, pairs: {}, counts: {} });

function createOwner(scope: string, generation: number, setState: Owner['setState']): Owner {
  let active = false;
  const writes = new Map<string, Operation>(), reads = new Map<string, number>();
  return { scope, generation, setState, writes, reads, isActive: () => active,
    activate: () => { active = true; },
    dispose: () => {
      active = false;
      writes.forEach(operation => operation.controller.abort());
      writes.clear(); reads.clear();
    } };
}
function owns(owner: Owner, operation: Operation) {
  return owner.isActive() && owner.generation === getAuthSnapshot().generation && owner.writes.get(operation.key) === operation;
}
function publish(owner: Owner, operation: Operation, outcome: Outcome | null, counts?: BlogReactions) {
  if (!owns(owner, operation)) return;
  owner.setState(previous => {
    const current = previous.owner === owner ? previous : empty(owner);
    const pairs = { ...current.pairs };
    if (outcome) pairs[operation.key] = { postId: operation.postId, reaction: operation.reaction, outcome };
    else delete pairs[operation.key];
    return { ...current, pairs, counts: counts ? { ...current.counts, [operation.postId]: counts } : current.counts };
  });
}
function isCounts(value: unknown, postId: string): value is { id: string; reactions: BlogReactions } {
  if (typeof value !== 'object' || value === null || Reflect.get(value, 'id') !== postId) return false;
  const counts: unknown = Reflect.get(value, 'reactions');
  if (typeof counts !== 'object' || counts === null) return false;
  return blogReactionKinds.every(kind => {
    const count: unknown = Reflect.get(counts, kind);
    return typeof count === 'number' && Number.isSafeInteger(count) && count >= 0;
  });
}
async function writeToggle(owner: Owner, operation: Operation): Promise<boolean> {
  if (!getAuthSnapshot().accessToken) { publish(owner, operation, 'login'); return false; }
  const url = `${API_URL}/api/v1/blog/posts/${operation.postId}/reactions`;
  const response = await fetchWithAuth(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reaction: operation.reaction }), signal: operation.controller.signal });
  if (!owns(owner, operation)) return false;
  if (response.status === 401) { publish(owner, operation, 'login'); return false; }
  if (response.status !== 409) {
    if (response.status !== 201) throw new Error('Reaction write failed');
    return true;
  }
  const removal = await fetchWithAuth(`${url}/${operation.reaction}`, { method: 'DELETE', signal: operation.controller.signal });
  if (!owns(owner, operation)) return false;
  if (removal.status === 401) { publish(owner, operation, 'login'); return false; }
  if (removal.status !== 200) throw new Error('Reaction removal failed');
  return true;
}
function ownsRead(owner: Owner, operation: Operation, attempt: number) {
  return owns(owner, operation) && owner.reads.get(operation.postId) === attempt;
}
async function refreshCounts(owner: Owner, operation: Operation) {
  if (!owns(owner, operation)) return;
  const attempt = (owner.reads.get(operation.postId) ?? 0) + 1;
  owner.reads.set(operation.postId, attempt);
  try {
    // Reload post reactions
    const response = await fetch(`${API_URL}/api/v1/blog/posts/${operation.postId}`, { signal: operation.controller.signal });
    if (!ownsRead(owner, operation, attempt)) return;
    if (!response.ok) throw new Error('Reaction counts read failed');
    const value: unknown = await response.json();
    if (!ownsRead(owner, operation, attempt)) return;
    if (!isCounts(value, operation.postId)) throw new Error('Invalid reaction counts response');
    publish(owner, operation, null, value.reactions);
  } catch (error) {
    if (ownsRead(owner, operation, attempt)) throw error;
  }
}
async function react(owner: Owner, postId: string, reaction: Reaction, countsOnly: boolean) {
  const key = `${postId}:${reaction}`;
  if (!owner.isActive() || owner.generation !== getAuthSnapshot().generation || owner.writes.has(key)) return;
  const operation: Operation = { key, postId, reaction, controller: new AbortController(), accepted: countsOnly };
  owner.writes.set(key, operation); publish(owner, operation, 'pending');
  try {
    if (!countsOnly) {
      if (!await writeToggle(owner, operation)) return;
      operation.accepted = true;
    }
    await refreshCounts(owner, operation);
    publish(owner, operation, null);
  } catch { /* ignore */ publish(owner, operation, operation.accepted ? 'counts' : 'error'); }
  finally { if (owner.writes.get(key) === operation) owner.writes.delete(key); }
}

/** Own global blog toggles and canonical count recovery for the current page and session. */
export function useBlogReactions(scopeKey: string) {
  const session = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const [state, setState] = useState(() => empty(null));
  const owner = useMemo(() => createOwner(scopeKey, session.generation, setState), [scopeKey, session.generation]);
  useEffect(() => {
    owner.activate();
    return owner.dispose;
  }, [owner]);
  const current = state.owner === owner ? state : empty(owner);
  const run = (postId: string, reaction: Reaction, countsOnly = false) => react(owner, postId, reaction, countsOnly);
  return { counts: current.counts, pairs: current.pairs, react: (postId: string, reaction: Reaction) => run(postId, reaction),
    retry: (postId: string, reaction: Reaction) => run(postId, reaction, current.pairs[`${postId}:${reaction}`]?.outcome === 'counts') };
}
