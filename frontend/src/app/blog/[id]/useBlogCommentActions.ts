"use client";

import { useEffect, useMemo, useState, useSyncExternalStore, type Dispatch, type SetStateAction } from 'react';
import { API_URL, type BlogComment } from '@/lib/api';
import { fetchWithAuth, getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from '@/lib/auth';
import { useChatIdentity } from '@/lib/useChatIdentity';
import type { useBlogComments } from '../useBlogReads';

type Outcome = 'pending' | 'login' | 'error';
type Draft = { postId: string; principal: string | null; text: string; revision: number };
type Operation = { key: string; principal: string; content: string; revision: number; controller: AbortController };
type State = { owner: Owner | null; outcomes: Record<string, Outcome> };
type Owner = { postId: string; generation: number; writes: Map<string, Operation>; isActive: () => boolean;
  activate: () => void; dispose: () => void; setState: Dispatch<SetStateAction<State>> };
type CommentsRead = ReturnType<typeof useBlogComments>;
const emptyDraft = (postId: string, principal: string | null): Draft => ({ postId, principal, text: '', revision: 0 });
const emptyState = (owner: Owner | null): State => ({ owner, outcomes: {} });

function createOwner(postId: string, generation: number, setState: Owner['setState']): Owner {
  let active = false;
  const writes = new Map<string, Operation>();
  return { postId, generation, writes, setState, isActive: () => active, activate: () => { active = true; },
    dispose: () => { active = false; writes.forEach(operation => operation.controller.abort()); writes.clear(); } };
}
function owns(owner: Owner, operation: Operation) {
  return owner.isActive() && owner.generation === getAuthSnapshot().generation && owner.writes.get(operation.key) === operation;
}
function publish(owner: Owner, operation: Operation, outcome: Outcome | null) {
  if (!owns(owner, operation)) return;
  owner.setState(previous => {
    const current = previous.owner === owner ? previous : emptyState(owner), outcomes = { ...current.outcomes };
    if (outcome) outcomes[operation.key] = outcome; else delete outcomes[operation.key];
    return { owner, outcomes };
  });
}
function clearSubmitted(owner: Owner, operation: Operation, setDraft: Dispatch<SetStateAction<Draft>>) {
  setDraft(previous => previous.postId === owner.postId && previous.principal === operation.principal
    && previous.revision === operation.revision ? { ...previous, text: '', revision: previous.revision + 1 } : previous);
}
async function writeComment(owner: Owner, operation: Operation): Promise<boolean> {
  const posting = operation.key === 'post', url = `${API_URL}/api/v1/blog/posts/${owner.postId}/comments`;
  const response = await fetchWithAuth(posting ? url : `${url}/${operation.key}`, {
    method: posting ? 'POST' : 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: posting ? JSON.stringify({ content: operation.content }) : undefined, signal: operation.controller.signal,
  });
  if (!owns(owner, operation)) return false;
  if (response.status === 401) { publish(owner, operation, 'login'); return false; }
  if (response.status !== (posting ? 201 : 200)) throw new Error('Comment write failed');
  return true;
}
async function run(owner: Owner, operation: Operation, read: CommentsRead, setDraft: Dispatch<SetStateAction<Draft>>) {
  if (!owner.isActive() || owner.generation !== getAuthSnapshot().generation || owner.writes.has(operation.key)) return;
  owner.writes.set(operation.key, operation); publish(owner, operation, 'pending');
  try {
    if (!await writeComment(owner, operation)) return;
    if (!owns(owner, operation)) return;
    if (operation.key === 'post') { clearSubmitted(owner, operation, setDraft); await read.refresh(); }
    else read.remove(operation.key);
    publish(owner, operation, null);
  } catch { publish(owner, operation, 'error'); }
  finally { if (owner.writes.get(operation.key) === operation) owner.writes.delete(operation.key); }
}

/** Own comment drafts and author-only mutations for the current publication and confirmed session. */
export function useBlogCommentActions(postId: string, read: CommentsRead) {
  const session = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const identity = useChatIdentity();
  const [draft, setDraft] = useState(() => emptyDraft(postId, null));
  const [state, setState] = useState(() => emptyState(null));
  const principal = identity.status === 'ready' ? identity.user.id : identity.status === 'signed-out' ? null : draft.principal;
  const changed = draft.postId !== postId || draft.principal !== principal;
  if (changed) setDraft(emptyDraft(postId, principal));
  const currentDraft = changed ? emptyDraft(postId, principal) : draft;
  const owner = useMemo(() => createOwner(postId, session.generation, setState), [postId, session.generation]);
  useEffect(() => { owner.activate(); return owner.dispose; }, [owner]);
  const outcomes: State['outcomes'] = state.owner === owner ? state.outcomes : {};
  const ready = identity.status === 'ready';
  const canDelete = (comment: BlogComment) => ready && comment.author_type === 'user' && comment.author_id === principal;
  const invoke = (key: string) => {
    if (!ready || !principal) return;
    const content = currentDraft.text.trim();
    if (key === 'post' && !content) return;
    const operation = { key, principal, content, revision: currentDraft.revision, controller: new AbortController() };
    return run(owner, operation, read, setDraft);
  };
  const change = (text: string) => setDraft(previous => ({ ...previous, text, revision: previous.revision + 1 }));
  return { identity, ready, text: currentDraft.text, change, outcomes, canDelete, submit: () => invoke('post'),
    remove: (comment: BlogComment) => { if (canDelete(comment)) return invoke(comment.id); } };
}
