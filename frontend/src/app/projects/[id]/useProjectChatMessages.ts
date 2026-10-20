import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { API_URL } from "@/lib/api";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";

export interface ProjectMessage {
  id: string;
  sender_user_id?: string | null;
  sender_name: string;
  sender_handle: string | null;
  sender_type: "agent" | "human" | "user";
  content: string;
  message_type: string;
  created_at: string;
  reply_to?: { id: string; content: string };
  is_deleted?: boolean;
  edited_at?: string;
}

type SessionCheck = (generation?: number) => boolean;
type ReadOperation = { controller: AbortController; generation: number; changes: Map<string, Partial<ProjectMessage>> };
type DeleteOperation = { controller: AbortController; generation: number };

function useProjectChatRows() {
  const [messages, setMessages] = useState<ProjectMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const reads = useRef(new Set<ReadOperation>());
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const beginRead = useCallback(() => {
    const operation = { controller: new AbortController(), generation: getAuthSnapshot().generation, changes: new Map<string, Partial<ProjectMessage>>() };
    reads.current.add(operation);
    return operation;
  }, []);
  const finishRead = useCallback((operation: ReadOperation) => { reads.current.delete(operation); }, []);
  const patchMessage = useCallback((id: string, change: Partial<ProjectMessage>) => {
    for (const operation of reads.current) operation.changes.set(id, { ...operation.changes.get(id), ...change });
    setMessages(current => current.map(message => message.id === id ? { ...message, ...change } : message));
  }, []);
  const applyRead = useCallback((operation: ReadOperation, data: ProjectMessage[], older: boolean) => {
    const container = containerRef.current;
    const height = container?.scrollHeight ?? 0;
    const incoming = [...data].reverse();
    setHasMore(data.length === 50);
    setMessages(current => {
      const rows = older ? [...new Map(incoming.concat(current).map(message => [message.id, message])).values()] : incoming;
      return rows.map(message => ({ ...message, ...operation.changes.get(message.id) }));
    });
    if (older) requestAnimationFrame(() => {
      if (container && !operation.controller.signal.aborted) container.scrollTop = container.scrollHeight - height;
    });
    else setTimeout(() => { if (!operation.controller.signal.aborted) bottomRef.current?.scrollIntoView(); }, 100);
  }, []);
  return { messages, hasMore, beginRead, finishRead, applyRead, patchMessage, bottomRef, containerRef };
}

type ChatRows = ReturnType<typeof useProjectChatRows>;

function useProjectChatRead(path: string | null, active: boolean, token: string | undefined, isCurrentSession: SessionCheck, rows: ChatRows) {
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [error, setError] = useState(false);
  const request = useRef<ReadOperation | null>(null);
  const enabled = useRef(false);
  const { beginRead, finishRead, applyRead } = rows;
  const owns = useCallback((operation: ReadOperation) =>
    request.current === operation && isCurrentSession(operation.generation) && !operation.controller.signal.aborted, [isCurrentSession]);
  const run = useCallback(async () => {
    if (!enabled.current || !path || request.current || !isCurrentSession()) return;
    const operation = beginRead();
    request.current = operation;
    setStatus("loading");
    try {
      const response = await fetch(`${API_URL}/api/v1${path}`, { signal: operation.controller.signal });
      if (!response.ok) throw new Error("Project chat history request failed");
      const data: ProjectMessage[] = await response.json();
      if (!owns(operation)) return;
      applyRead(operation, data, path.includes("&before="));
      setError(false);
      setStatus("ready");
    } catch {
      if (owns(operation)) { setError(true); setStatus("error"); }
    } finally {
      finishRead(operation);
      if (request.current === operation) request.current = null;
    }
  }, [path, owns, beginRead, finishRead, applyRead, isCurrentSession]);
  const cancel = useCallback(() => {
    const operation = request.current;
    if (operation) { operation.controller.abort(); finishRead(operation); }
    request.current = null;
  }, [finishRead]);
  const refresh = useCallback(async () => {
    if (!enabled.current || !isCurrentSession()) return;
    cancel();
    await run();
  }, [cancel, run, isCurrentSession]);
  useEffect(() => {
    enabled.current = active;
    setStatus("idle"); setError(false);
    return () => { enabled.current = false; cancel(); };
  }, [active, path, token, isCurrentSession, cancel]);
  return { status, error, run, refresh };
}

function useProjectChatDeletes(projectId: string, active: boolean, token: string | undefined, isCurrentSession: SessionCheck, author: { id: string | null; rows: ChatRows }) {
  const [state, setState] = useState<Record<string, "pending" | "error">>({});
  const requests = useRef(new Map<string, DeleteOperation>());
  const update = (id: string, status?: "pending" | "error") => setState(current => {
    const next = { ...current };
    if (status) next[id] = status;
    else delete next[id];
    return next;
  });
  const canDelete = (id: string) => active && !!token && !!author.id && isCurrentSession() &&
    author.rows.messages.some(message => message.id === id && message.sender_user_id === author.id && message.sender_type !== "agent");
  const owns = (id: string, operation: DeleteOperation) =>
    requests.current.get(id) === operation && isCurrentSession(operation.generation);
  const remove = async (id: string) => {
    if (!canDelete(id) || requests.current.has(id)) return;
    const operation = { controller: new AbortController(), generation: getAuthSnapshot().generation };
    requests.current.set(id, operation);
    update(id, "pending");
    try {
      const response = await fetch(`${API_URL}/api/v1/chat/project/${projectId}/human-messages/${id}`, {
        method: "DELETE", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, signal: operation.controller.signal,
      });
      if (!response.ok) throw new Error("Project chat deletion failed");
      if (!owns(id, operation)) return;
      author.rows.patchMessage(id, { content: "[deleted]", is_deleted: true });
      update(id);
    } catch {
      if (owns(id, operation)) update(id, "error");
    } finally {
      if (requests.current.get(id) === operation) requests.current.delete(id);
    }
  };
  useEffect(() => {
    setState({});
    const pending = requests.current;
    return () => { for (const operation of pending.values()) operation.controller.abort(); pending.clear(); };
  }, [active, token, isCurrentSession]);
  return { state, remove };
}

/** Project discussion history and row mutations; composer ownership stays in the route. */
export function useProjectChatMessages(projectId: string, active: boolean, token: string | undefined, isCurrentSession: SessionCheck, confirmedAuthorId: string | null) {
  const { generation } = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const currentSession = useCallback((captured = generation) =>
    captured === generation && isCurrentSession(captured), [generation, isCurrentSession]);
  const rows = useProjectChatRows();
  const path = `/chat/project/${projectId}/messages?limit=50`;
  const initial = useProjectChatRead(path, active, token, currentSession, rows);
  const reload = initial.run;
  const oldestId = rows.messages[0]?.id;
  const older = useProjectChatRead(oldestId ? `${path}&before=${oldestId}` : null, active, token, currentSession, rows);
  const deletion = useProjectChatDeletes(projectId, active, token, currentSession, { id: confirmedAuthorId, rows });
  useEffect(() => { if (active) void reload(); }, [active, reload, token]);
  const loadOlder = () => {
    if (!rows.hasMore || older.error) return;
    void older.run();
  };
  return { messages: rows.messages, patchMessage: rows.patchMessage, bottomRef: rows.bottomRef, containerRef: rows.containerRef,
    status: initial.status, readError: initial.error, reload, refresh: initial.refresh, olderError: older.error,
    loadingMore: older.status === "loading", loadOlder, retryOlder: older.run,
    deleteState: deletion.state, deleteMessage: deletion.remove };
}
