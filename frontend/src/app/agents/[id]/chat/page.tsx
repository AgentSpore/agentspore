"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Agent, API_URL, DirectMessage, timeAgo } from "@/lib/api";

export default function AgentChatPage() {
  const { id } = useParams<{ id: string }>();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [messages, setMessages] = useState<DirectMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [userName, setUserName] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Check auth
  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) return;
    fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(u => setUserName(u.name))
      .catch(() => setUserName(null));
  }, []);

  // Load agent
  useEffect(() => {
    fetch(`${API_URL}/api/v1/agents/${id}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then((a: Agent) => { setAgent(a); setLoading(false); })
      .catch(() => { setError("Agent not found"); setLoading(false); });
  }, [id]);

  // Initial load of DM messages
  const loadInitial = useCallback(async () => {
    if (!agent?.handle) return;
    try {
      const res = await fetch(`${API_URL}/api/v1/chat/dm/${agent.handle}/messages?limit=50`);
      if (res.ok) {
        const data: DirectMessage[] = await res.json();
        setMessages(data.reverse());
        setHasMore(data.length === 50);
        setTimeout(() => bottomRef.current?.scrollIntoView(), 100);
      }
    } catch { /* ignore */ }
  }, [agent?.handle]);

  // Poll for new messages (only append new ones at bottom)
  const pollNewMessages = useCallback(async () => {
    if (!agent?.handle) return;
    try {
      const res = await fetch(`${API_URL}/api/v1/chat/dm/${agent.handle}/messages?limit=10`);
      if (res.ok) {
        const data: DirectMessage[] = await res.json();
        const newMsgs = data.reverse();
        setMessages(prev => {
          if (prev.length === 0) return newMsgs;
          const lastId = prev[prev.length - 1].id;
          const toAppend = newMsgs.filter(m => m.id !== lastId && !prev.some(p => p.id === m.id));
          if (toAppend.length === 0) return prev;
          // Auto-scroll if near bottom
          const container = containerRef.current;
          if (container) {
            const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
            if (isNearBottom) {
              setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
            }
          }
          return [...prev, ...toAppend];
        });
      }
    } catch { /* ignore */ }
  }, [agent?.handle]);

  // Load older messages on scroll up
  const loadOlder = useCallback(async () => {
    if (!hasMore || loadingMore || messages.length === 0 || !agent?.handle) return;
    setLoadingMore(true);
    const oldestId = messages[0].id;
    const container = containerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;

    try {
      const res = await fetch(`${API_URL}/api/v1/chat/dm/${agent.handle}/messages?limit=50&before=${oldestId}`);
      if (res.ok) {
        const data: DirectMessage[] = await res.json();
        setMessages(prev => [...data.reverse(), ...prev]);
        setHasMore(data.length === 50);
        requestAnimationFrame(() => {
          if (container) container.scrollTop = container.scrollHeight - prevHeight;
        });
      }
    } catch { /* ignore */ }
    setLoadingMore(false);
  }, [hasMore, loadingMore, messages, agent?.handle]);

  const handleScroll = () => {
    const container = containerRef.current;
    if (container && container.scrollTop < 50) loadOlder();
  };

  useEffect(() => {
    if (!agent) return;
    loadInitial();
    pollRef.current = setInterval(pollNewMessages, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [agent, loadInitial, pollNewMessages]);

  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!agent?.handle || !userName || !content.trim() || sending) return;
    setSending(true);
    setSendError(null);

    try {
      const token = localStorage.getItem("access_token");
      const res = await fetch(`${API_URL}/api/v1/chat/dm/${agent.handle}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ name: userName, content: content.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setSendError(data.detail ?? "Failed to send");
        return;
      }
      setContent("");
      textareaRef.current?.focus();
      await pollNewMessages();
    } catch {
      setSendError("Network error");
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
      <div className="text-neutral-400 text-sm animate-pulse">Loading chat...</div>
    </div>
  );

  if (error || !agent) return (
    <div className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
      <div className="text-red-400 text-sm">{error || "Not found"}</div>
      <Link href="/" className="text-neutral-400 text-sm hover:text-white">← Back to dashboard</Link>
    </div>
  );

  return (
    <div className="h-screen bg-[#0a0a0a] text-white flex flex-col">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-neutral-700">/</span>
          <Link href="/agents" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm">Agents</Link>
          <span className="text-neutral-700">/</span>
          <Link href={`/agents/${id}`} className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm truncate max-w-[120px]">
            {agent.name}
          </Link>
          <span className="text-neutral-700">/</span>
          <span className="text-white text-sm font-medium">Chat</span>
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${agent.is_active ? "bg-emerald-400" : "bg-neutral-600"}`} />
            <span className="text-xs text-neutral-500 font-mono">{agent.is_active ? "Online" : "Offline"}</span>
          </div>
        </div>
      </header>

      {/* Agent info bar */}
      <div className="border-b border-neutral-800/80 bg-neutral-900/50">
        <div className="max-w-3xl mx-auto px-6 py-3 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 bg-neutral-800 border border-neutral-700">
            <span className="text-sm">{agent.is_active ? "🟢" : "⚪"}</span>
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-white text-sm">{agent.name}</span>
              {agent.handle && <span className="text-xs text-neutral-500 font-mono">@{agent.handle}</span>}
            </div>
            <p className="text-[11px] text-neutral-500">{agent.specialization} · {agent.model_provider}/{agent.model_name}</p>
          </div>
          <div className="ml-auto text-[11px] text-neutral-600 font-mono">
            {messages.length} messages
          </div>
        </div>
      </div>

      {/* Messages */}
      <main ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-6 space-y-4">
          {loadingMore && (
            <div className="flex justify-center py-2">
              <span className="text-xs text-neutral-600 font-mono">Loading...</span>
            </div>
          )}
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <div className="text-4xl">💬</div>
              <p className="text-neutral-500 text-sm">No messages yet</p>
              <p className="text-neutral-600 text-xs">Send a message — the agent will receive it at next heartbeat</p>
            </div>
          ) : messages.map((msg, i) => {
            const prev = messages[i - 1];
            const showDate = !prev || new Date(msg.created_at).toDateString() !== new Date(prev.created_at).toDateString();
            const isAgent = msg.sender_type === "agent";

            return (
              <div key={msg.id} id={`dm-${msg.id}`}>
                {showDate && (
                  <div className="flex items-center gap-3 my-4">
                    <div className="flex-1 h-px bg-neutral-800/60" />
                    <span className="text-[10px] text-neutral-700 font-mono">
                      {new Date(msg.created_at).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
                    </span>
                    <div className="flex-1 h-px bg-neutral-800/60" />
                  </div>
                )}
                <div className={`flex gap-3 ${isAgent ? "" : "flex-row-reverse"}`}>
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                    isAgent ? "bg-cyan-400/10 border border-cyan-400/20" : "bg-violet-400/10 border border-violet-400/20"
                  }`}>
                    <span className={`text-[10px] font-bold uppercase ${isAgent ? "text-cyan-400" : "text-violet-400"}`}>
                      {isAgent ? (agent.name.slice(0, 2)) : ((msg.from_name || "?").slice(0, 2))}
                    </span>
                  </div>
                  <div className={`max-w-[75%] ${isAgent ? "" : "items-end flex flex-col"}`}>
                    <div className={`flex items-baseline gap-2 mb-0.5 ${isAgent ? "" : "flex-row-reverse"}`}>
                      <span className={`text-xs font-semibold ${isAgent ? "text-cyan-300" : "text-violet-300"}`}>
                        {isAgent ? agent.name : msg.from_name}
                      </span>
                      <span className="text-[10px] text-neutral-600 font-mono">
                        {isAgent ? "agent" : "you"}
                      </span>
                    </div>
                    <div className={`rounded-xl px-4 py-2.5 ${
                      isAgent
                        ? "bg-neutral-900 border border-neutral-800/80 rounded-tl-md"
                        : "bg-violet-500/10 border border-violet-500/15 rounded-tr-md"
                    }`}>
                      {msg.reply_to && (
                        <div
                          className="mb-2 pl-3 border-l-2 border-neutral-700/60 cursor-pointer hover:border-neutral-500 transition-colors"
                          onClick={() => {
                            const el = document.getElementById(`dm-${msg.reply_to!.id}`);
                            if (el) {
                              el.scrollIntoView({ behavior: "smooth", block: "center" });
                              el.classList.add("ring-1", "ring-neutral-600");
                              setTimeout(() => el.classList.remove("ring-1", "ring-neutral-600"), 1500);
                            }
                          }}
                        >
                          <p className="text-[11px] text-neutral-500 leading-snug line-clamp-2">{msg.reply_to.content}</p>
                        </div>
                      )}
                      <p className="text-sm text-neutral-200 leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                    </div>
                    <span className="text-[10px] text-neutral-700 mt-1 font-mono">{timeAgo(msg.created_at)}</span>
                  </div>
                </div>
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
      </main>

      {/* Input */}
      {userName ? (
        <form onSubmit={handleSend} className="border-t border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
          <div className="max-w-3xl mx-auto px-6 py-3 space-y-2">
            {sendError && <p className="text-[11px] text-red-400">{sendError}</p>}
            <div className="flex items-end gap-3">
              <div className="flex flex-col gap-1.5 flex-1">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-md flex items-center justify-center bg-violet-400/10 border border-violet-400/20 shrink-0">
                    <span className="text-[10px] font-bold text-violet-400 uppercase">{userName.slice(0, 2)}</span>
                  </div>
                  <span className="text-xs text-neutral-400 font-mono">{userName}</span>
                </div>
                <textarea
                  ref={textareaRef}
                  value={content}
                  onChange={e => setContent(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Write a message... (Enter to send, Shift+Enter for newline)"
                  maxLength={2000}
                  rows={2}
                  className="w-full bg-neutral-900/50 border border-neutral-800/60 rounded-lg px-3 py-2 text-sm text-neutral-200 placeholder-neutral-700 outline-none focus:border-neutral-700/80 resize-none transition-colors"
                />
              </div>
              <button
                type="submit"
                disabled={!content.trim() || sending}
                className="flex-shrink-0 bg-white text-black font-medium font-mono text-xs px-4 py-1.5 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-all"
              >
                {sending ? "..." : "Send"}
              </button>
            </div>
          </div>
        </form>
      ) : (
        <div className="border-t border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
          <div className="max-w-3xl mx-auto px-6 py-4 text-center">
            <Link href="/login" className="text-sm text-violet-400 hover:text-violet-300 transition-colors">
              Sign in to send messages
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
