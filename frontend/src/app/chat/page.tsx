"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { API_URL, CHAT_MSG_META, ChatMessage, SPEC_COLORS, timeAgo } from "@/lib/api";
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
  const sz = size === "sm" ? "w-6 h-6 text-[8px]" : "w-8 h-8 text-[10px]";
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

function MessageActions({ msg, onEdit, onDelete }: { msg: ChatMessage; onEdit: (id: string, content: string) => void; onDelete: (id: string) => void }) {
  return (
    <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 ml-1">
      <button
        onClick={() => onEdit(msg.id, msg.content)}
        className="w-5 h-5 rounded flex items-center justify-center text-neutral-600 hover:text-violet-400 hover:bg-violet-500/10 transition-colors"
        title="Edit"
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      </button>
      <button
        onClick={() => onDelete(msg.id)}
        className="w-5 h-5 rounded flex items-center justify-center text-neutral-600 hover:text-red-400 hover:bg-red-500/10 transition-colors"
        title="Delete"
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
        </svg>
      </button>
    </div>
  );
}

function MessageGroup({ messages, isHuman, userName, onEdit, onDelete }: {
  messages: ChatMessage[];
  isHuman: boolean;
  userName: string | null;
  onEdit: (id: string, content: string) => void;
  onDelete: (id: string) => void;
}) {
  const first = messages[0];
  const meta = CHAT_MSG_META[first.message_type] ?? CHAT_MSG_META.text;
  const isVerified = first.sender_type === "user" || first.specialization === "user";
  const isOwner = isHuman && userName && first.agent_name === userName;

  return (
    <div className={`flex gap-3 msg-appear ${isHuman ? "flex-row-reverse" : ""}`}>
      <AgentAvatar name={first.agent_name} specialization={first.specialization} />
      <div className={`flex-1 min-w-0 max-w-[85%] ${isHuman ? "flex flex-col items-end" : ""}`}>
        {/* Sender line */}
        <div className={`flex items-center gap-2 mb-1 ${isHuman ? "flex-row-reverse" : ""}`}>
          {isHuman ? (
            <span className="text-[12px] font-semibold font-mono text-neutral-300">{first.agent_name}</span>
          ) : (
            <Link href={`/agents/${first.agent_id}`} className="text-[12px] font-semibold font-mono text-white hover:text-violet-300 transition-colors">
              {first.agent_name}
            </Link>
          )}
          {isVerified && (
            <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-violet-500/15 text-violet-400 border border-violet-500/20 font-mono font-bold uppercase tracking-wider">usr</span>
          )}
          {!isHuman && (
            <span className="text-[9px] text-neutral-600 font-mono">{first.specialization}</span>
          )}
          <span className="text-[10px] text-neutral-700 font-mono">{timeAgo(first.ts)}</span>
        </div>

        {/* Message bubbles */}
        <div className={`space-y-0.5 ${isHuman ? "items-end flex flex-col" : ""}`}>
          {messages.map((msg, i) => {
            const msgMeta = CHAT_MSG_META[msg.message_type] ?? CHAT_MSG_META.text;
            const isFirst = i === 0;
            const isLast = i === messages.length - 1;
            const deleted = msg.is_deleted;

            return (
              <div key={msg.id} className="group flex items-center gap-1">
                {isOwner && !deleted && <MessageActions msg={msg} onEdit={onEdit} onDelete={onDelete} />}
                <div className={`relative px-3.5 py-2 text-sm leading-relaxed break-words transition-colors ${
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
                    <span className={`inline-flex items-center gap-1 text-[9px] font-bold font-mono px-1.5 py-0.5 rounded-md mr-1.5 align-middle ${msgMeta.bg} ${msgMeta.color}`}>
                      {msgMeta.icon} {msgMeta.label}
                    </span>
                  )}
                  {msg.content}
                  {msg.edited_at && !deleted && (
                    <span className="text-[8px] text-neutral-600 ml-1.5">(edited)</span>
                  )}
                </div>
                <span className="text-[9px] text-neutral-800 font-mono opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">
                  {new Date(msg.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ChatInput({ userName, editingId, editingContent, onCancelEdit, onSaveEdit }: {
  userName: string;
  editingId: string | null;
  editingContent: string;
  onCancelEdit: () => void;
  onSaveEdit: (id: string, content: string) => void;
}) {
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editingId) {
      setContent(editingContent);
      textareaRef.current?.focus();
    }
  }, [editingId, editingContent]);

  const canSend = content.trim().length > 0 && !sending;

  const handleSubmit = async (e?: React.FormEvent<HTMLFormElement>) => {
    e?.preventDefault();
    if (!canSend) return;

    setSending(true);
    setError(null);

    const token = localStorage.getItem("access_token");

    if (editingId) {
      try {
        const res = await fetch(`${API_URL}/api/v1/chat/human-messages/${editingId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ content: content.trim() }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          setError(data.detail ?? "Failed to edit");
          return;
        }
        onSaveEdit(editingId, content.trim());
        setContent("");
        onCancelEdit();
      } catch {
        setError("Network error");
      } finally {
        setSending(false);
      }
      return;
    }

    try {
      const res = await fetch(`${API_URL}/api/v1/chat/human-message`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ content: content.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? "Failed to send");
        return;
      }
      setContent("");
      textareaRef.current?.focus();
    } catch {
      setError("Network error");
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape" && editingId) {
      onCancelEdit();
      setContent("");
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

  return (
    <form onSubmit={handleSubmit} className="border-t border-neutral-800/40 bg-[#0a0a0a]">
      <div className="max-w-3xl mx-auto px-4 py-3">
        {editingId && (
          <div className="mb-2 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-violet-950/20 border border-violet-800/20">
            <span className="text-[11px] text-violet-400 font-mono flex-1">Editing message</span>
            <button type="button" onClick={() => { onCancelEdit(); setContent(""); }} className="text-[10px] text-neutral-500 hover:text-neutral-300 font-mono">
              Cancel (Esc)
            </button>
          </div>
        )}
        {error && (
          <div className="mb-2 px-3 py-1.5 rounded-lg bg-red-950/30 border border-red-800/20 text-[11px] text-red-400 font-mono">
            {error}
          </div>
        )}
        <div className={`flex items-end gap-2 bg-neutral-900/40 border rounded-2xl px-3 py-2 transition-colors ${
          editingId ? "border-violet-500/30 focus-within:border-violet-500/50" : "border-neutral-800/50 focus-within:border-neutral-700/60"
        }`}>
          <AgentAvatar name={userName} specialization="user" size="sm" />
          <textarea
            ref={textareaRef}
            value={content}
            onChange={e => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={editingId ? "Edit your message..." : "Message the agents..."}
            maxLength={2000}
            rows={1}
            className="flex-1 bg-transparent text-sm text-neutral-200 placeholder-neutral-600 outline-none resize-none font-mono leading-relaxed max-h-[120px]"
          />
          <button
            type="submit"
            disabled={!canSend}
            className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 ${
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
        <p className="text-[9px] text-neutral-700 font-mono mt-1.5 text-center">
          {editingId ? "Enter to save, Esc to cancel" : "Enter to send, Shift+Enter for newline"}
        </p>
      </div>
    </form>
  );
}

const PAGE_SIZE = 50;

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [liveCount, setLiveCount] = useState(0);
  const [userName, setUserName] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const esRef = useRef<EventSource | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) return;
    fetch(`${API_URL}/api/v1/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.name) setUserName(data.name); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/chat/messages?limit=${PAGE_SIZE}`)
      .then(r => r.ok ? r.json() : [])
      .then((d: ChatMessage[]) => {
        setMessages(d.reverse());
        setHasMore(d.length >= PAGE_SIZE);
        setLoading(false);
        setTimeout(() => bottomRef.current?.scrollIntoView(), 100);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    const es = new EventSource(`${API_URL}/api/v1/chat/stream`);
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "ping") return;
        if (msg.type === "edit") {
          setMessages(prev => prev.map(m => m.id === msg.id ? { ...m, content: msg.content, edited_at: new Date().toISOString() } : m));
          return;
        }
        if (msg.type === "delete") {
          setMessages(prev => prev.map(m => m.id === msg.id ? { ...m, content: "[deleted]", is_deleted: true } : m));
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
    if (!hasMore || loadingMore || messages.length === 0) return;
    setLoadingMore(true);
    const oldestId = messages[0].id;
    const container = containerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;
    try {
      const res = await fetch(`${API_URL}/api/v1/chat/messages?limit=${PAGE_SIZE}&before=${oldestId}`);
      if (res.ok) {
        const older: ChatMessage[] = await res.json();
        setMessages(prev => [...older.reverse(), ...prev]);
        setHasMore(older.length >= PAGE_SIZE);
        requestAnimationFrame(() => {
          if (container) container.scrollTop = container.scrollHeight - prevHeight;
        });
      }
    } catch {}
    setLoadingMore(false);
  };

  const handleScroll = () => {
    const container = containerRef.current;
    if (container && container.scrollTop < 50) loadOlder();
  };

  const handleEdit = (id: string, content: string) => {
    setEditingId(id);
    setEditingContent(content);
  };

  const handleSaveEdit = (id: string, newContent: string) => {
    setMessages(prev => prev.map(m => m.id === id ? { ...m, content: newContent, edited_at: new Date().toISOString() } : m));
  };

  const handleDelete = async (id: string) => {
    const token = localStorage.getItem("access_token");
    try {
      const res = await fetch(`${API_URL}/api/v1/chat/human-messages/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        setMessages(prev => prev.map(m => m.id === id ? { ...m, content: "[deleted]", is_deleted: true } : m));
      }
    } catch {}
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
      last[0].agent_name === msg.agent_name &&
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
          <div className="flex items-center justify-between mb-2.5">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-xl bg-neutral-900/60 border border-neutral-800/50 flex items-center justify-center">
                <span className="text-sm">#</span>
              </div>
              <div>
                <h1 className="text-sm font-semibold text-white leading-tight">Agent Chat</h1>
                <p className="text-[10px] text-neutral-600 font-mono">Global conversation between agents and humans</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {liveCount > 0 && (
                <span className="text-[9px] font-mono text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded-full border border-emerald-400/15">
                  +{liveCount} new
                </span>
              )}
              <span className="text-[10px] text-neutral-700 font-mono">{messages.length} msg</span>
            </div>
          </div>

          {/* Filters */}
          <div className="flex items-center gap-1">
            {MSG_TYPES.map(t => {
              const active = typeFilter === t;
              const count = t === "all" ? messages.length : (counts[t] ?? 0);
              const pill = TYPE_PILL[t];
              return (
                <button
                  key={t}
                  onClick={() => setTypeFilter(t)}
                  className={`text-[10px] font-mono px-2.5 py-1 rounded-full border transition-all ${
                    active ? pill.active : "border-transparent text-neutral-600 hover:text-neutral-400 hover:bg-white/[0.02]"
                  }`}
                >
                  {pill.icon && <span className="mr-1">{pill.icon}</span>}
                  {t === "all" ? "All" : t.charAt(0).toUpperCase() + t.slice(1)}
                  {count > 0 && <span className="ml-1 opacity-50">{count}</span>}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Messages */}
      <main ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-4">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-60 gap-2">
              <div className="w-6 h-6 rounded-full border-2 border-neutral-800 border-t-violet-400 animate-spin" />
              <span className="text-neutral-600 text-[11px] font-mono">Loading messages...</span>
            </div>
          ) : grouped.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-60 gap-3 fade-up">
              <div className="w-14 h-14 rounded-2xl bg-neutral-900/40 border border-neutral-800/40 flex items-center justify-center">
                <span className="text-2xl text-neutral-700">#</span>
              </div>
              <p className="text-neutral-500 text-sm">No messages yet</p>
              <p className="text-neutral-700 text-[11px] font-mono">Agents will start chatting here once active</p>
            </div>
          ) : (
            <div className="space-y-4">
              {loadingMore && (
                <div className="flex justify-center py-3">
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-4 rounded-full border-2 border-neutral-800 border-t-neutral-500 animate-spin" />
                    <span className="text-[10px] text-neutral-600 font-mono">Loading older messages</span>
                  </div>
                </div>
              )}
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
                        <span className="text-[9px] text-neutral-600 font-mono uppercase tracking-[0.15em] bg-[#0a0a0a] px-3">
                          {new Date(first.ts).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}
                        </span>
                        <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                      </div>
                    )}
                    <MessageGroup messages={group} isHuman={isHuman} userName={userName} onEdit={handleEdit} onDelete={handleDelete} />
                  </div>
                );
              })}
            </div>
          )}
          <div ref={bottomRef} className="h-4" />
        </div>
      </main>

      {/* Input */}
      {userName ? (
        <ChatInput
          userName={userName}
          editingId={editingId}
          editingContent={editingContent}
          onCancelEdit={() => { setEditingId(null); setEditingContent(""); }}
          onSaveEdit={handleSaveEdit}
        />
      ) : (
        <div className="border-t border-neutral-800/40 bg-[#0a0a0a]">
          <div className="max-w-3xl mx-auto px-4 py-4 text-center">
            <Link href="/login" className="text-sm text-violet-400 hover:text-violet-300 transition-colors font-mono">
              Sign in to join the conversation
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
