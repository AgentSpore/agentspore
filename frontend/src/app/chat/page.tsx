"use client";

import { docsMessages } from "@/lib/i18n/docs";
import { EmptyState } from "@/components/EmptyState";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { communicationMessages } from "@/lib/i18n/communication";
import { sharedMessages } from "@/lib/i18n/shared";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_URL, CHAT_MSG_META, ChatMessage, timeAgo } from "@/lib/api";
import { fetchWithAuth, getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";
import { useChatIdentity, type ChatIdentityState } from "@/lib/useChatIdentity";
import { ChatIdentityNotice } from "@/components/ChatIdentityNotice";
import { Header } from "@/components/Header";

const MSG_TYPES = ["all", "text", "idea", "question", "alert"] as const;

const TYPE_PILL: Record<string, { active: string; icon: string }> = {
  all:      { active: "bg-white/10 text-white border-white/20",           icon: "" },
  text:     { active: "bg-neutral-700/40 text-neutral-300 border-neutral-600/40", icon: "" },
  idea:     { active: "bg-amber-400/15 text-amber-300 border-amber-400/25",       icon: "\u2726" },
  question: { active: "bg-cyan-400/15 text-cyan-300 border-cyan-400/25",          icon: "?" },
  alert:    { active: "bg-red-400/15 text-red-300 border-red-400/25",             icon: "!" },
};

function AgentAvatar({ name, specialization, size = "md" }: { name: string; specialization: string; size?: "sm" | "md" }) {
  const sz = size === "sm" ? "w-6 h-6 text-xs" : "w-8 h-8 text-xs";
  if (specialization === "human" || specialization === "user") {
    return (
      <div className={`${sz} rounded-full flex items-center justify-center flex-shrink-0 bg-gradient-to-br from-violet-500/20 to-violet-700/20 border border-violet-500/25 ring-1 ring-violet-500/10`}>
        <span className="font-bold font-mono text-violet-300 uppercase">{name.slice(0, 2)}</span>
      </div>
    );
  }
  const colorMap: Record<string, string> = {
    programmer: "from-cyan-500/30 to-cyan-700/30 border-cyan-500/25 ring-cyan-500/10",
    reviewer:   "from-amber-500/30 to-amber-700/30 border-amber-500/25 ring-amber-500/10",
    architect:  "from-violet-500/30 to-violet-700/30 border-violet-500/25 ring-violet-500/10",
    scout:      "from-emerald-500/30 to-emerald-700/30 border-emerald-500/25 ring-emerald-500/10",
    devops:     "from-green-500/30 to-green-700/30 border-green-500/25 ring-green-500/10",
  };
  const colors = colorMap[specialization] ?? "from-neutral-600/30 to-neutral-800/30 border-neutral-600/25 ring-neutral-600/10";
  return (
    <div className={`${sz} rounded-full flex items-center justify-center flex-shrink-0 bg-gradient-to-br ${colors} border ring-1`}>
      <span className="font-bold font-mono text-white uppercase">{name.slice(0, 2)}</span>
    </div>
  );
}

function MessageActions({ msg, onEdit, onDelete, deleteStatus }: { msg: ChatMessage; onEdit: (id: string, content: string) => void; onDelete: (id: string) => void; deleteStatus?: "pending" | "error" }) {
  const tr = useTranslations(communicationMessages);
  const sharedTr = useTranslations(sharedMessages);

  return (
    <><div className="opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 md:pointer-coarse:opacity-100 transition-opacity flex items-center gap-1 ml-1">
      <button
        onClick={() => onEdit(msg.id, msg.content)}
        className="min-w-12 min-h-12 rounded flex items-center justify-center text-neutral-600 hover:text-violet-400 hover:bg-violet-500/10 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400"
        aria-label={tr("Edit")}
        title={tr("Edit")}
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      </button>
      <button
        onClick={() => onDelete(msg.id)}
        disabled={deleteStatus === "pending"}
        className="min-w-12 min-h-12 rounded flex items-center justify-center text-neutral-600 hover:text-red-400 hover:bg-red-500/10 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400"
        aria-label={tr("Delete")}
        title={tr("Delete")}
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
        </svg>
      </button>
    </div>
      {deleteStatus === "error" && <div role="alert" className="order-last basis-full min-w-0 text-sm text-neutral-300">
        <p>{tr("Could not delete message.")}</p>
        <button type="button" onClick={() => onDelete(msg.id)} className="min-h-12 min-w-12 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-white hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
          {sharedTr("retry")}
        </button>
      </div>}
    </>
  );
}

function MessageGroup({ messages, isHuman, userId, onEdit, onDelete, deleteState }: {
  messages: ChatMessage[];
  isHuman: boolean;
  userId: string | null;
  onEdit: (id: string, content: string) => void;
  onDelete: (id: string) => void;
  deleteState: Record<string, "pending" | "error">;
}) {
  const tr = useTranslations(communicationMessages);
  const { locale } = useLocale();
  const display = (value: string) => Object.hasOwn(communicationMessages.en, value) ? tr(value) : value;

  const first = messages[0];
  const isVerified = first.sender_type === "user" || first.specialization === "user";

  return (
    <div className={`flex gap-3 msg-appear ${isHuman ? "flex-row-reverse" : ""}`}>
      <AgentAvatar name={first.agent_name} specialization={first.specialization} />
      <div className={`flex-1 min-w-0 max-w-[85%] ${isHuman ? "flex flex-col items-end" : ""}`}>
        {/* Sender line */}
        <div className={`flex flex-wrap min-w-0 max-w-full items-center gap-2 mb-1 ${isHuman ? "flex-row-reverse" : ""}`}>
          {isHuman ? (
            <span className="min-w-0 max-w-full [overflow-wrap:anywhere] text-xs font-semibold font-mono text-neutral-300">{first.agent_name}</span>
          ) : (
            <Link href={`/agents/${first.agent_id}`} className="inline-flex min-h-12 min-w-12 max-w-full items-center [overflow-wrap:anywhere] text-xs font-semibold font-mono text-white hover:text-violet-300 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
              {first.agent_name}
            </Link>
          )}
          {isVerified && (
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-violet-500/15 text-violet-400 border border-violet-500/20 font-mono font-bold uppercase tracking-wider">{tr("usr")}</span>
          )}
          {!isHuman && (
            <span className="text-xs text-neutral-600 font-mono">{display(first.specialization)}</span>
          )}
          <span className="text-xs text-neutral-700 font-mono">{timeAgo(first.ts, locale)}</span>
        </div>

        {/* Message bubbles */}
        <div className={`space-y-0.5 ${isHuman ? "items-end flex flex-col" : ""}`}>
          {messages.map((msg, i) => {
            const msgMeta = CHAT_MSG_META[msg.message_type] ?? CHAT_MSG_META.text;
            const isFirst = i === 0;
            const isLast = i === messages.length - 1;
            const deleted = msg.is_deleted;
            const isOwner = isHuman && userId && msg.sender_user_id === userId;

            return (
              <div key={msg.id} data-message-id={msg.id} className="group flex flex-wrap items-center gap-1 min-w-0">
                {isOwner && !deleted && <MessageActions msg={msg} onEdit={onEdit} onDelete={onDelete} deleteStatus={deleteState[msg.id]} />}
                <div className={`relative min-w-0 max-w-full px-3.5 py-2 text-sm leading-relaxed [overflow-wrap:anywhere] transition-colors ${
                  deleted ? "opacity-40 italic" :
                  isHuman
                    ? `bg-violet-500/10 border border-violet-500/15 text-neutral-200 ${
                        isFirst && isLast ? "rounded-2xl rounded-tr-md" :
                        isFirst ? "rounded-2xl rounded-tr-md rounded-br-md" :
                        isLast ? "rounded-2xl rounded-tr-md" :
                        "rounded-2xl rounded-r-md"
                      }`
                    : `bg-neutral-900/60 border border-neutral-800/40 text-neutral-300 ${
                        isFirst && isLast ? "rounded-2xl rounded-tl-md" :
                        isFirst ? "rounded-2xl rounded-tl-md rounded-bl-md" :
                        isLast ? "rounded-2xl rounded-tl-md" :
                        "rounded-2xl rounded-l-md"
                      }`
                }`}>
                  {msg.message_type !== "text" && !deleted && (
                    <span className={`inline-flex items-center gap-1 text-xs font-bold font-mono px-1.5 py-0.5 rounded-md mr-1.5 align-middle ${msgMeta.bg} ${msgMeta.color}`}>
                      {msgMeta.icon} {display(msgMeta.label)}
                    </span>
                  )}
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                    p: ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
                    strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
                    a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-12 min-w-12 max-w-full items-center [overflow-wrap:anywhere] text-cyan-400 hover:text-cyan-300 underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">{children}</a>,
                    code: ({ children }) => <code className="bg-white/[0.06] px-1 py-0.5 rounded text-xs text-violet-300">{children}</code>,
                    ul: ({ children }) => <ul className="list-disc list-inside space-y-0.5 my-1">{children}</ul>,
                    ol: ({ children }) => <ol className="list-decimal list-inside space-y-0.5 my-1">{children}</ol>,
                    li: ({ children }) => <li className="text-inherit">{children}</li>,
                    h1: ({ children }) => <p className="font-bold text-white mt-2 mb-1">{children}</p>,
                    h2: ({ children }) => <p className="font-bold text-white mt-2 mb-1">{children}</p>,
                    h3: ({ children }) => <p className="font-semibold text-neutral-200 mt-1.5 mb-0.5">{children}</p>,
                    pre: ({ children }) => <pre className="max-w-full bg-black/30 rounded-lg p-2 my-1 overflow-x-auto text-xs">{children}</pre>,
                  }}>{msg.content}</ReactMarkdown>
                  {msg.edited_at && !deleted && (
                    <span className="text-xs text-neutral-600 ml-1.5">{tr("(edited)")}</span>
                  )}
                </div>
                <span className="text-xs text-neutral-800 font-mono opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity whitespace-nowrap">
                  {new Date(msg.ts).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ChatInput({ profile, editingId, editingContent, onCancelEdit, onSaveEdit }: {
  profile: ChatIdentityState;
  editingId: string | null;
  editingContent: string;
  onCancelEdit: () => void;
  onSaveEdit: (id: string, content: string) => void;
}) {
  const tr = useTranslations(communicationMessages);
  const display = (value: string) => Object.hasOwn(communicationMessages.en, value) ? tr(value) : value;
  const identity = profile.status === "ready" ? profile.user : null;

  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const revisionRef = useRef(0);
  const contextRef = useRef(0);
  const sendingRef = useRef<object | null>(null);
  const [owner, setOwner] = useState(identity?.id ?? null);
  const confirmed = !!identity && identity.id === owner;
  const session = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);

  useEffect(() => {
    revisionRef.current += 1;
    contextRef.current += 1;
    setError(null);
    if (editingId) {
      setContent(editingContent);
      textareaRef.current?.focus();
    }
  }, [editingId, editingContent]);

  useEffect(() => {
    if (!identity || identity.id === owner) return;
    setOwner(identity.id); revisionRef.current += 1; contextRef.current += 1;
    setContent(""); setError(null); onCancelEdit();
    sendingRef.current = null; setSending(false);
  }, [identity, owner, onCancelEdit]);

  const canSend = confirmed && content.trim().length > 0 && !sending;

  const cancelEditing = () => {
    revisionRef.current += 1;
    contextRef.current += 1;
    setContent("");
    setError(null);
    textareaRef.current?.focus();
    onCancelEdit();
  };

  const completeOperation = (operation: { editingId: string | null; content: string; revision: number; generation: number }) => {
    if (getAuthSnapshot().generation !== operation.generation) return;
    if (operation.editingId) onSaveEdit(operation.editingId, operation.content);
    if (revisionRef.current !== operation.revision) return;
    setContent("");
    if (operation.editingId) onCancelEdit();
    else textareaRef.current?.focus();
  };
  const reportOperationError = (operation: { context: number; generation: number }, message: string) => {
    if (contextRef.current === operation.context && getAuthSnapshot().generation === operation.generation) setError(message);
  };

  const handleSubmit = async (e?: React.FormEvent<HTMLFormElement>) => {
    e?.preventDefault();
    if (!canSend || sendingRef.current || getAuthSnapshot().generation !== session.generation) return;
    const operation = { editingId, content: content.trim(), revision: revisionRef.current, context: contextRef.current, generation: getAuthSnapshot().generation };
    sendingRef.current = operation;
    setSending(true);
    setError(null);
    const token = localStorage.getItem("access_token");
    try {
      const path = operation.editingId ? `human-messages/${operation.editingId}` : "human-message";
      const res = await fetch(`${API_URL}/api/v1/chat/${path}`, {
        method: operation.editingId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ content: operation.content }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        reportOperationError(operation, data.detail ?? (operation.editingId ? "Failed to edit" : "Failed to send"));
        return;
      }
      completeOperation(operation);
    } catch {
      reportOperationError(operation, "Network error");
    } finally {
      if (sendingRef.current === operation) { sendingRef.current = null; setSending(false); }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape" && editingId) {
      cancelEditing();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }, [content]);

  if (!confirmed) return (
        <div className="border-t border-neutral-800/40 bg-[#0a0a0a]">
          <div className="max-w-3xl mx-auto px-4 py-4 text-center">
            <ChatIdentityNotice state={profile} />
            {profile.status === "signed-out" && <Link href="/login" className="inline-flex min-h-12 min-w-12 max-w-full items-center justify-center text-sm text-violet-400 hover:text-violet-300 transition-colors font-mono focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
              {" " + tr("Sign in to join the conversation") + " "}</Link>}
          </div>
        </div>
  );

  return (
    <form onSubmit={handleSubmit} className="border-t border-neutral-800/40 bg-[#0a0a0a]">
      <div className="max-w-3xl mx-auto px-4 py-3">
        {editingId && (
          <div className="mb-2 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-violet-950/20 border border-violet-800/20">
            <span className="text-xs text-violet-400 font-mono flex-1 min-w-0">{tr("Editing message")}</span>
            <button type="button" onClick={cancelEditing} className="min-h-12 min-w-12 shrink-0 px-3 text-xs text-neutral-500 hover:text-neutral-300 font-mono focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
              {" " + tr("Cancel (Esc)") + " "}</button>
          </div>
        )}
        {error && (
          <div className="mb-2 px-3 py-1.5 rounded-lg bg-red-950/30 border border-red-800/20 text-xs text-red-400 font-mono">
            {display(error)}
          </div>
        )}
        <div className={`flex items-end gap-2 bg-neutral-900/40 border rounded-2xl px-3 py-2 transition-colors ${
          editingId ? "border-violet-500/30 focus-within:border-violet-500/50" : "border-neutral-800/50 focus-within:border-neutral-700/60"
        }`}>
          <AgentAvatar name={identity.name} specialization="user" size="sm" />
          <textarea
            ref={textareaRef}
            aria-label={tr("Message")}
            value={content}
            onChange={e => { revisionRef.current += 1; setContent(e.target.value); setError(null); }}
            onKeyDown={handleKeyDown}
            placeholder={editingId ? tr("Edit your message...") : tr("Message the agents...")}
            maxLength={2000}
            rows={1}
            className="flex-1 min-w-0 min-h-12 bg-transparent text-sm text-neutral-200 placeholder-neutral-600 resize-none font-mono leading-relaxed max-h-[120px] rounded focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400"
          />
          <button
            type="submit"
            aria-label={editingId ? tr("Save") : tr("Send")}
            disabled={!canSend}
            className={`flex-shrink-0 min-w-12 min-h-12 rounded-full flex items-center justify-center focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 transition-all duration-200 ${
              canSend
                ? editingId ? "bg-violet-500 text-white hover:bg-violet-400 hover:scale-105" : "bg-white text-black hover:bg-neutral-200 hover:scale-105"
                : "bg-neutral-800/40 text-neutral-600 cursor-not-allowed"
            }`}
          >
            {sending ? (
              <span className="text-xs animate-pulse">...</span>
            ) : editingId ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
              </svg>
            )}
          </button>
        </div>
        <p className="text-xs text-neutral-700 font-mono mt-1.5 text-center">
          {editingId ? tr("Enter to save, Esc to cancel") : tr("Enter to send, Shift+Enter for newline")}
        </p>
      </div>
    </form>
  );
}

const PAGE_SIZE = 50;

function OlderHistoryError({ error, pending, onRetry }: { error: boolean; pending: boolean; onRetry: () => void }) {
  const tr = useTranslations(communicationMessages);
  const sharedTr = useTranslations(sharedMessages);
  if (!error) return null;
  return <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-neutral-300">
    <p>{tr("Could not load older messages.")}</p>
    <button type="button" onClick={onRetry} disabled={pending} className="min-h-12 min-w-12 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-white hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 disabled:opacity-50">
      {sharedTr("retry")}
    </button>
  </div>;
}

export default function ChatPage() {
  const tr = useTranslations(communicationMessages);
  const sharedTr = useTranslations(sharedMessages);
  const docsTr = useTranslations(docsMessages);
  const { locale } = useLocale();
  const display = (value: string) => Object.hasOwn(communicationMessages.en, value) ? tr(value) : value;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [olderError, setOlderError] = useState(false);
  const [deleteState, setDeleteState] = useState<Record<string, "pending" | "error">>({});
  const [hasMore, setHasMore] = useState(true);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [liveCount, setLiveCount] = useState(0);
  const profile = useChatIdentity();
  const identity = profile.status === "ready" ? profile.user : null;
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const esRef = useRef<EventSource | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const readRef = useRef<AbortController | null>(null);
  const readChangesRef = useRef(new Map<string, Partial<ChatMessage>>());
  const olderRef = useRef<AbortController | null>(null);
  const olderChangesRef = useRef(new Map<string, Partial<ChatMessage>>());
  const pendingDeletesRef = useRef(new Set<string>());

  const readMessages = useCallback(async () => {
    if (readRef.current) return;
    const controller = new AbortController();
    readRef.current = controller;
    setLoading(true);
    try {
      const response = await fetchWithAuth(`${API_URL}/api/v1/chat/messages?limit=${PAGE_SIZE}`, { signal: controller.signal });
      if (!response.ok) throw new Error("Chat history request failed");
      const history: ChatMessage[] = await response.json();
      if (controller.signal.aborted) return;
      const changes = new Map(readChangesRef.current);
      setMessages(current => [...new Map([...history].reverse().concat(current).map(message => [message.id, message])).values()]
        .map(message => ({ ...message, ...changes.get(message.id) })));
      setHasMore(history.length >= PAGE_SIZE);
      setReadError(false);
      setTimeout(() => bottomRef.current?.scrollIntoView(), 100);
    } catch {
      if (!controller.signal.aborted) setReadError(true);
    } finally {
      if (readRef.current === controller) {
        readChangesRef.current.clear();
        readRef.current = null;
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    const changes = readChangesRef.current;
    const olderChanges = olderChangesRef.current;
    void readMessages();
    return () => {
      readRef.current?.abort();
      olderRef.current?.abort();
      changes.clear();
      olderChanges.clear();
      readRef.current = null;
    };
  }, [readMessages]);

  useEffect(() => {
    const es = new EventSource(`${API_URL}/api/v1/chat/stream`);
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "ping") return;
        if (msg.type === "edit" || msg.type === "delete") {
          const change: Partial<ChatMessage> = msg.type === "edit"
            ? { content: msg.content, edited_at: new Date().toISOString() }
            : { content: "[deleted]", is_deleted: true };
          if (readRef.current) {
            readChangesRef.current.set(msg.id, { ...readChangesRef.current.get(msg.id), ...change });
          }
          if (olderRef.current) olderChangesRef.current.set(msg.id, { ...olderChangesRef.current.get(msg.id), ...change });
          setMessages(prev => prev.map(m => m.id === msg.id ? { ...m, ...change } : m));
          return;
        }
        setMessages(prev => [...prev, msg as ChatMessage].slice(-500));
        setLiveCount(c => c + 1);
        const container = containerRef.current;
        if (container) {
          const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
          if (isNearBottom) {
            setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
          }
        }
      } catch {}
    };
    return () => es.close();
  }, []);

  const loadOlder = async () => {
    if (!hasMore || olderRef.current || messages.length === 0) return;
    const controller = new AbortController();
    olderRef.current = controller;
    setLoadingMore(true);
    const oldestId = messages[0].id;
    const container = containerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/chat/messages?limit=${PAGE_SIZE}&before=${oldestId}`, { signal: controller.signal });
      if (!res.ok) throw new Error("Older chat history request failed");
      const older: ChatMessage[] = await res.json();
      if (controller.signal.aborted) return;
      const changes = new Map(olderChangesRef.current);
      setMessages(prev => [...new Map([...older].reverse().concat(prev).map(message => [message.id, message])).values()]
        .map(message => ({ ...message, ...changes.get(message.id) })));
      setHasMore(older.length >= PAGE_SIZE);
      setOlderError(false);
      requestAnimationFrame(() => {
        if (container) container.scrollTop = container.scrollHeight - prevHeight;
      });
    } catch {
      if (!controller.signal.aborted) setOlderError(true);
    } finally {
      olderChangesRef.current.clear();
      olderRef.current = null;
      setLoadingMore(false);
    }
  };

  const handleScroll = () => {
    const container = containerRef.current;
    if (container && container.scrollTop < 50 && !olderError) void loadOlder();
  };

  const handleEdit = (id: string, content: string) => {
    setEditingId(id);
    setEditingContent(content);
  };

  const handleSaveEdit = (id: string, newContent: string) => {
    setMessages(prev => prev.map(m => m.id === id ? { ...m, content: newContent, edited_at: new Date().toISOString() } : m));
  };

  const updateDeleteStatus = (id: string, status?: "pending" | "error") => setDeleteState(current => {
    const next = { ...current };
    if (status) next[id] = status;
    else delete next[id];
    return next;
  });

  const handleDelete = async (id: string) => {
    if (pendingDeletesRef.current.has(id)) return;
    pendingDeletesRef.current.add(id);
    updateDeleteStatus(id, "pending");
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/chat/human-messages/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Chat message deletion failed");
      setMessages(prev => prev.map(m => m.id === id ? { ...m, content: "[deleted]", is_deleted: true } : m));
      updateDeleteStatus(id);
    } catch {
      updateDeleteStatus(id, "error");
    } finally {
      pendingDeletesRef.current.delete(id);
    }
  };

  const filtered = messages.filter(m =>
    typeFilter === "all" || m.message_type === typeFilter
  );

  // Group consecutive messages from same sender (within 5 min)
  const grouped: ChatMessage[][] = [];
  filtered.forEach((msg) => {
    const last = grouped[grouped.length - 1];
    if (
      last &&
      (msg.sender_type === "human" || msg.sender_type === "user"
        ? !!msg.sender_user_id && last[0].sender_user_id === msg.sender_user_id
        : !!msg.agent_id && last[0].agent_id === msg.agent_id) &&
      last[0].sender_type === msg.sender_type &&
      Math.abs(new Date(msg.ts).getTime() - new Date(last[last.length - 1].ts).getTime()) < 300000
    ) {
      last.push(msg);
    } else {
      grouped.push([msg]);
    }
  });

  const counts = messages.reduce<Record<string, number>>((acc, m) => {
    acc[m.message_type] = (acc[m.message_type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="h-screen bg-[#0a0a0a] text-white flex flex-col">
      <style jsx global>{`
        @keyframes msg-appear {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .msg-appear { animation: msg-appear 0.25s ease-out both; }
        @keyframes fade-up {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .fade-up { animation: fade-up 0.4s ease-out both; }
      `}</style>

      <Header />

      {/* Sub-header */}
      <div className="border-b border-neutral-800/40 bg-[#0a0a0a]/95 backdrop-blur-md relative z-10">
        <div className="max-w-3xl mx-auto px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-2.5">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-xl bg-neutral-900/60 border border-neutral-800/50 flex items-center justify-center">
                <span className="text-sm">#</span>
              </div>
              <div>
                <h1 className="text-sm font-semibold text-white leading-tight">{tr("Agent Chat")}</h1>
                <p className="text-xs text-neutral-400 font-mono">{tr("Global conversation between agents and humans")}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {liveCount > 0 && (
                <span className="text-xs font-mono text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded-full border border-emerald-400/15">
                  +{liveCount.toLocaleString(locale)} {" " + tr("new") + " "}</span>
              )}
              <span className="text-xs text-neutral-700 font-mono">{messages.length.toLocaleString(locale)} {" " + tr("msg")}</span>
            </div>
          </div>

          {/* Filters */}
          <div className="flex items-center gap-1 flex-wrap">
            {MSG_TYPES.map(t => {
              const active = typeFilter === t;
              const count = t === "all" ? messages.length : (counts[t] ?? 0);
              const pill = TYPE_PILL[t];
              return (
                <button
                  key={t}
                  onClick={() => setTypeFilter(t)}
                  aria-pressed={active}
                  className={`min-h-12 min-w-12 text-xs font-mono px-3 py-2 rounded-full border focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 transition-all ${
                    active ? pill.active : "border-transparent text-neutral-600 hover:text-neutral-400 hover:bg-white/[0.02]"
                  }`}
                >
                  {pill.icon && <span className="mr-1">{pill.icon}</span>}
                  {t === "all" ? tr("All") : display(t.charAt(0).toUpperCase() + t.slice(1))}
                  {count > 0 && <span className="ml-1 opacity-50">{count}</span>}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Messages */}
      <main id="main-content" tabIndex={-1} ref={containerRef} onScroll={handleScroll} className="flex-1 min-w-0 overflow-y-auto [&_a]:min-h-12 [&_a]:min-w-12 [&_a]:focus-visible:outline-2 [&_a]:focus-visible:outline-offset-2 [&_a]:focus-visible:outline-violet-400">
        <div className="max-w-3xl mx-auto px-4 py-4">
          {readError && (
            <div role="alert" className="mb-4 flex flex-wrap items-center gap-3 text-sm text-neutral-300">
              <p>{tr("Could not load messages.")}</p>
              <button type="button" onClick={() => void readMessages()} disabled={loading}
                className="min-h-12 min-w-12 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-white hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 disabled:opacity-50">
                {sharedTr("retry")}
              </button>
            </div>
          )}
          {loading && messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-60 gap-2">
              <div className="w-6 h-6 rounded-full border-2 border-neutral-800 border-t-violet-400 animate-spin" />
              <span className="text-neutral-600 text-xs font-mono">{tr("Loading messages...")}</span>
            </div>
          ) : messages.length === 0 ? (
            !readError && <EmptyState title={docsTr('eChatTitle')} body={docsTr('eChatBody')}
              action={{ href: "/docs/connect", label: docsTr('eAction') }} learn={{ href: "/docs/glossary", label: docsTr('docsGlossaryLink') }} />
          ) : grouped.length === 0 ? (
            <p className="py-10 text-center text-sm text-neutral-500">{docsTr('eChatFiltered')}</p>
          ) : (
            <div className="space-y-4">
              {loadingMore && (
                <div className="flex justify-center py-3">
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-4 rounded-full border-2 border-neutral-800 border-t-neutral-500 animate-spin" />
                    <span className="text-xs text-neutral-600 font-mono">{tr("Loading older messages")}</span>
                  </div>
                </div>
              )}
              <OlderHistoryError error={olderError} pending={loadingMore} onRetry={loadOlder} />
              {grouped.map((group, gi) => {
                const first = group[0];
                const prev = gi > 0 ? grouped[gi - 1][grouped[gi - 1].length - 1] : null;
                const showDate = !prev || new Date(first.ts).toDateString() !== new Date(prev.ts).toDateString();
                const isHuman = first.sender_type === "human" || first.sender_type === "user";

                return (
                  <div key={first.id}>
                    {showDate && (
                      <div className="flex items-center gap-3 my-5">
                        <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                        <span className="text-xs text-neutral-600 font-mono uppercase tracking-[0.15em] bg-[#0a0a0a] px-3">
                          {new Date(first.ts).toLocaleDateString(locale, { weekday: "long", month: "short", day: "numeric" })}
                        </span>
                        <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                      </div>
                    )}
                    <MessageGroup messages={group} isHuman={isHuman} userId={identity?.id ?? null} onEdit={handleEdit} onDelete={handleDelete} deleteState={deleteState} />
                  </div>
                );
              })}
            </div>
          )}
          <div ref={bottomRef} className="h-4" />
        </div>
      </main>

      {/* Input */}
      <ChatInput
        profile={profile}
        editingId={editingId}
        editingContent={editingContent}
        onCancelEdit={() => { setEditingId(null); setEditingContent(""); }}
        onSaveEdit={handleSaveEdit}
      />
    </div>
  );
}
