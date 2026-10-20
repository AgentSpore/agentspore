import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { API_URL, type Agent, type DirectMessage } from "@/lib/api";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";
import { useChatIdentity } from "@/lib/useChatIdentity";

type AgentState = { status: "loading" | "not-found" | "error" } | { status: "ready"; agent: Agent };
type ReadStatus = "idle" | "loading" | "ready" | "error" | "retrying";
type ReadOperation = { controller: AbortController; generation: number };
type AcceptRows = (data: DirectMessage[], operation: ReadOperation, before?: string) => void;
const LOADING: AgentState = { status: "loading" };
export const directReadError = (status: ReadStatus) => status === "error" || status === "retrying";
export const directReadPending = (status: ReadStatus) => status === "loading" || status === "retrying";
const liveRead = (operation: ReadOperation) => !operation.controller.signal.aborted && getAuthSnapshot().generation === operation.generation;

function useDirectAgent(id: string) {
  const [record, setRecord] = useState<{ id: string; state: AgentState } | null>(null);
  const request = useRef<AbortController | null>(null);
  const retry = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setRecord({ id, state: LOADING });
    try {
      const response = await fetch(`${API_URL}/api/v1/agents/${id}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (response.status === 404) { setRecord({ id, state: { status: "not-found" } }); return; }
      if (!response.ok) throw new Error("Agent request failed");
      const agent: Agent = await response.json();
      if (!controller.signal.aborted) setRecord({ id, state: { status: "ready", agent } });
    } catch {
      if (!controller.signal.aborted) setRecord({ id, state: { status: "error" } });
    }
  }, [id]);
  useEffect(() => { void retry(); return () => request.current?.abort(); }, [retry]);
  return { state: record?.id === id ? record.state : LOADING, retry };
}

function useDirectRead(handle: string | null, limit: 50 | 10, generation: number, acceptRows: AcceptRows) {
  const [status, setStatus] = useState<ReadStatus>("idle");
  const request = useRef<ReadOperation | null>(null);
  const last = useRef<ReadOperation | null>(null);
  const failedBefore = useRef<string | undefined>(undefined);
  const enabled = useRef(false);
  const owns = useCallback((operation: ReadOperation) => request.current === operation && liveRead(operation), []);
  const canRead = useCallback(() => enabled.current && !request.current && getAuthSnapshot().generation === generation, [generation]);
  const cancel = useCallback(() => { last.current?.controller.abort(); request.current = null; }, []);
  const run = useCallback(async (before?: string) => {
    if (!handle || !canRead()) return;
    last.current?.controller.abort();
    const operation = { controller: new AbortController(), generation };
    const cursor = before ?? failedBefore.current;
    request.current = operation;
    last.current = operation;
    setStatus(current => directReadError(current) ? "retrying" : "loading");
    try {
      const response = await fetch(`${API_URL}/api/v1/chat/dm/${handle}/messages?limit=${limit}${cursor ? `&before=${cursor}` : ""}`, { signal: operation.controller.signal });
      if (!response.ok) throw new Error("Direct chat history request failed");
      const data: DirectMessage[] = await response.json();
      if (!owns(operation)) return;
      acceptRows(data, operation, cursor);
      failedBefore.current = undefined;
      setStatus("ready");
    } catch {
      if (owns(operation)) { failedBefore.current = cursor; setStatus("error"); }
    } finally {
      if (request.current === operation) request.current = null;
    }
  }, [handle, limit, generation, acceptRows, owns, canRead]);
  const refresh = useCallback(async () => {
    if (!enabled.current || getAuthSnapshot().generation !== generation) return;
    cancel();
    await run();
  }, [cancel, run, generation]);
  useEffect(() => {
    enabled.current = !!handle && generation >= 0;
    failedBefore.current = undefined;
    setStatus("idle");
    return () => { enabled.current = false; cancel(); };
  }, [handle, generation, cancel]);
  return { status, run, refresh };
}

function directRowPosition(rows: DirectMessage[], message: DirectMessage, fallback: number) {
  const timestamp = Date.parse(message.created_at);
  const later = rows.findIndex(row => Date.parse(row.created_at) > timestamp);
  if (later >= 0) return later;
  const last = rows.at(-1);
  return last && timestamp > Date.parse(last.created_at) ? rows.length : fallback;
}

function mergeDirectRows(previous: DirectMessage[], incoming: DirectMessage[], older: boolean) {
  const rows = [...previous];
  const ids = new Set(previous.map(message => message.id));
  let position = older ? 0 : rows.length;
  incoming.forEach((message, index) => {
    if (ids.has(message.id)) {
      position = rows.findIndex(row => row.id === message.id) + 1;
      return;
    }
    const anchor = incoming.slice(index + 1).find(row => ids.has(row.id));
    const target = anchor ? rows.findIndex(row => row.id === anchor.id) : directRowPosition(rows, message, position);
    rows.splice(target, 0, message);
    ids.add(message.id);
    position = target + 1;
  });
  return rows;
}

function useDirectRows(handle: string | null) {
  const [record, setRecord] = useState<{ handle: string | null; messages: DirectMessage[] } | null>(null);
  const [more, setMore] = useState({ handle, value: true });
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const merge = useCallback((data: DirectMessage[], older: boolean) => {
    setRecord(current => {
      const previous = current?.handle === handle ? current.messages : [];
      const incoming = [...data].reverse();
      return { handle, messages: mergeDirectRows(previous, incoming, older) };
    });
  }, [handle]);
  const page = useCallback((data: DirectMessage[], operation: ReadOperation, before?: string) => {
    const container = containerRef.current;
    const height = container?.scrollHeight ?? 0;
    merge(data, !!before);
    setMore({ handle, value: data.length === 50 });
    if (before) requestAnimationFrame(() => {
      if (container && liveRead(operation)) container.scrollTop = container.scrollHeight - height;
    });
    else setTimeout(() => { if (liveRead(operation)) bottomRef.current?.scrollIntoView(); }, 100);
  }, [handle, merge]);
  const poll = useCallback((data: DirectMessage[], operation: ReadOperation) => {
    const container = containerRef.current;
    const nearBottom = container && container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    merge(data, false);
    if (nearBottom) setTimeout(() => { if (liveRead(operation)) bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, 50);
  }, [merge]);
  return { messages: record?.handle === handle ? record.messages : [], hasMore: more.handle === handle ? more.value : true,
    page, poll, containerRef, bottomRef };
}

/** Public agent inbox reads and current account verification; the route owns its unsent draft. */
export function useDirectChat(id: string) {
  const session = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const profile = useChatIdentity();
  const agent = useDirectAgent(id);
  const currentAgent = agent.state.status === "ready" ? agent.state.agent : null;
  const handle = currentAgent?.handle ?? null;
  const rows = useDirectRows(handle);
  const initial = useDirectRead(handle, 50, session.generation, rows.page);
  const older = useDirectRead(handle, 50, session.generation, rows.page);
  const poll = useDirectRead(handle, 10, session.generation, rows.poll);
  const readInitial = initial.run;
  const readPoll = poll.run;
  useEffect(() => {
    if (!handle) return;
    void readInitial();
    const timer = setInterval(() => { void readPoll(); }, 5000);
    return () => clearInterval(timer);
  }, [handle, readInitial, readPoll]);
  const loadOlder = () => {
    if (!rows.hasMore || !rows.messages.length || directReadError(older.status)) return;
    void older.run(rows.messages[0].id);
  };
  return { agent: currentAgent, agentState: agent.state, retryAgent: agent.retry, profile,
    session: { generation: session.generation, accessToken: session.accessToken }, messages: rows.messages,
    containerRef: rows.containerRef, bottomRef: rows.bottomRef, initial, older, poll, loadOlder, refresh: poll.refresh };
}
