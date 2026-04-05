"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Agent, API_URL, DirectMessage, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

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

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) return;
    fetch(`${API_URL}/api/v1/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(u => setUserName(u.name))
      .catch(() => setUserName(null));
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/agents/${id}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then((a: Agent) => { setAgent(a); setLoading(false); })
      .catch(() => { setError("Agent not found"); setLoading(false); });
  }, [id]);

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
    } catch {}
  }, [agent?.handle]);

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
    } catch {}
  }, [agent?.handle]);

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
    } catch {}
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

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }, [content]);

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="w-6 h-6 rounded-full border-2 border-neutral-800 border-t-violet-400 animate-spin" />
        <span className="text-neutral-600 text-[11px] font-mono">Loading chat...</span>
      </div>
    </div>
  );

  if (error || !agent) return (
    <div className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
      <div className="w-12 h-12 rounded-2xl bg-red-950/20 border border-red-800/20 flex items-center justify-center">
        <span className="text-red-400 text-lg">!</span>
      </div>
      <div className="text-red-400 text-sm font-mono">{error || "Not found"}</div>
      <Link href="/" className="text-neutral-500 text-[11px] hover:text-white font-mono transition-colors">&larr; Back to dashboard</Link>
    </div>
  );

  // Group consecutive messages from same sender within 5 min
  const grouped: DirectMessage[][] = [];
  messages.forEach(msg => {
    const last = grouped[grouped.length - 1];
    if (
      last &&
      last[0].sender_type === msg.sender_type &&
      last[0].from_name === msg.from_name &&
      Math.abs(new Date(msg.created_at).getTime() - new Date(last[last.length - 1].created_at).getTime()) < 300000
    ) {
      last.push(msg);
    } else {
      grouped.push([msg]);
    }
  });

  return (
    <div className="h-screen bg-[#0a0a0a] text-white flex flex-col">
      <style jsx global>{`
        @keyframes msg-appear {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .msg-appear { animation: msg-appear 0.2s ease-out both; }
        @keyframes fade-up {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .fade-up { animation: fade-up 0.4s ease-out both; }
      `}</style>

      <Header />

      {/* Agent info bar */}
      <div className="border-b border-neutral-800/40 bg-[#0a0a0a]/95 backdrop-blur-md relative z-10">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-3">
          {/* Agent avatar + info */}
          <Link href={`/agents/${id}`} className="flex items-center gap-3 hover:opacity-80 transition-opacity">
            <div className="relative">
              <div className="w-9 h-9 rounded-full bg-gradient-to-br from-cyan-500/25 to-cyan-700/25 border border-cyan-500/20 flex items-center justify-center">
                <span className="text-[11px] font-bold font-mono text-cyan-300 uppercase">{agent.name.slice(0, 2)}</span>
              </div>
              {agent.is_active && (
                <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-[#0a0a0a] flex items-center justify-center">
                  <div className="w-2 h-2 rounded-full bg-emerald-400" />
                </div>
              )}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-white leading-tight">{agent.name}</span>
                {agent.handle && <span className="text-[10px] text-neutral-600 font-mono">@{agent.handle}</span>}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-neutral-600 font-mono">{agent.specialization}</span>
                <span className="text-neutral-800">&middot;</span>
                <span className={`text-[9px] font-mono ${agent.is_active ? "text-emerald-400" : "text-neutral-600"}`}>
                  {agent.is_active ? "online" : "offline"}
                </span>
              </div>
            </div>
          </Link>

          <div className="flex-1" />

          <span className="text-[10px] text-neutral-700 font-mono">{messages.length} messages</span>
        </div>
      </div>

      {/* Messages */}
      <main ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
          {loadingMore && (
            <div className="flex justify-center py-3">
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded-full border-2 border-neutral-800 border-t-neutral-500 animate-spin" />
                <span className="text-[10px] text-neutral-600 font-mono">Loading older messages</span>
              </div>
            </div>
          )}
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3 fade-up">
              <div className="w-14 h-14 rounded-2xl bg-neutral-900/40 border border-neutral-800/40 flex items-center justify-center">
                <span className="text-2xl text-neutral-700">&equiv;</span>
              </div>
              <p className="text-neutral-500 text-sm">No messages yet</p>
              <p className="text-neutral-700 text-[11px] font-mono text-center max-w-xs">
                Send a message to {agent.name}. The agent will receive it at its next heartbeat.
              </p>
            </div>
          ) : grouped.map((group, gi) => {
            const first = group[0];
            const prev = gi > 0 ? grouped[gi - 1][grouped[gi - 1].length - 1] : null;
            const showDate = !prev || new Date(first.created_at).toDateString() !== new Date(prev.created_at).toDateString();
            const isAgent = first.sender_type === "agent";

            return (
              <div key={first.id} className="msg-appear">
                {showDate && (
                  <div className="flex items-center gap-3 my-5">
                    <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                    <span className="text-[9px] text-neutral-600 font-mono uppercase tracking-[0.15em] bg-[#0a0a0a] px-3">
                      {new Date(first.created_at).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}
                    </span>
                    <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                  </div>
                )}

                <div className={`flex gap-3 ${isAgent ? "" : "flex-row-reverse"}`}>
                  {/* Avatar */}
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-gradient-to-br ${
                    isAgent
                      ? "from-cyan-500/25 to-cyan-700/25 border border-cyan-500/20"
                      : "from-violet-500/25 to-violet-700/25 border border-violet-500/20"
                  }`}>
                    <span className={`text-[10px] font-bold uppercase font-mono ${isAgent ? "text-cyan-300" : "text-violet-300"}`}>
                      {(first.from_name || "?").slice(0, 2)}
                    </span>
                  </div>

                  <div className={`flex-1 min-w-0 max-w-[80%] ${isAgent ? "" : "flex flex-col items-end"}`}>
                    {/* Sender line */}
                    <div className={`flex items-center gap-2 mb-1 ${isAgent ? "" : "flex-row-reverse"}`}>
                      <span className={`text-[12px] font-semibold font-mono ${isAgent ? "text-cyan-300" : "text-violet-300"}`}>
                        {first.from_name}
                      </span>
                      <span className="text-[9px] text-neutral-700 font-mono uppercase">{isAgent ? "agent" : "you"}</span>
                      <span className="text-[10px] text-neutral-700 font-mono">{timeAgo(first.created_at)}</span>
                    </div>

                    {/* Bubbles */}
                    <div className={`space-y-0.5 ${isAgent ? "" : "flex flex-col items-end"}`}>
                      {group.map((msg, mi) => {
                        const isFirst = mi === 0;
                        const isLast = mi === group.length - 1;

                        return (
                          <div key={msg.id} id={`dm-${msg.id}`} className="group flex items-end gap-2">
                            <div className={`relative px-3.5 py-2.5 text-sm leading-relaxed break-words ${
                              isAgent
                                ? `bg-neutral-900/60 border border-neutral-800/40 text-neutral-300 ${
                                    isFirst && isLast ? "rounded-2xl rounded-tl-md" :
                                    isFirst ? "rounded-2xl rounded-tl-md rounded-bl-md" :
                                    isLast ? "rounded-2xl rounded-tl-md" :
                                    "rounded-2xl rounded-l-md"
                                  }`
                                : `bg-violet-500/10 border border-violet-500/12 text-neutral-200 ${
                                    isFirst && isLast ? "rounded-2xl rounded-tr-md" :
                                    isFirst ? "rounded-2xl rounded-tr-md rounded-br-md" :
                                    isLast ? "rounded-2xl rounded-tr-md" :
                                    "rounded-2xl rounded-r-md"
                                  }`
                            }`}>
                              {msg.reply_to && (
                                <div
                                  className="mb-2 pl-3 border-l-2 border-neutral-700/40 cursor-pointer hover:border-violet-400/40 transition-colors"
                                  onClick={() => {
                                    const el = document.getElementById(`dm-${msg.reply_to!.id}`);
                                    if (el) {
                                      el.scrollIntoView({ behavior: "smooth", block: "center" });
                                      el.classList.add("ring-1", "ring-violet-400/30");
                                      setTimeout(() => el.classList.remove("ring-1", "ring-violet-400/30"), 1500);
                                    }
                                  }}
                                >
                                  <p className="text-[11px] text-neutral-600 leading-snug line-clamp-2">{msg.reply_to.content}</p>
                                </div>
                              )}
                              <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                                p: ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
                                strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
                                a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-cyan-400 hover:text-cyan-300 underline underline-offset-2">{children}</a>,
                                code: ({ children }) => <code className="bg-white/[0.06] px-1 py-0.5 rounded text-[11px] text-violet-300">{children}</code>,
                                ul: ({ children }) => <ul className="list-disc list-inside space-y-0.5 my-1">{children}</ul>,
                                ol: ({ children }) => <ol className="list-decimal list-inside space-y-0.5 my-1">{children}</ol>,
                                pre: ({ children }) => <pre className="bg-black/30 rounded-lg p-2 my-1 overflow-x-auto text-[11px]">{children}</pre>,
                              }}>{msg.content}</ReactMarkdown>
                            </div>
                            <span className="text-[9px] text-neutral-800 font-mono opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap mb-1">
                              {new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                            </span>
                          </div>
                        );
                      })}
                    </div>
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
        <form onSubmit={handleSend} className="border-t border-neutral-800/40 bg-[#0a0a0a]">
          <div className="max-w-3xl mx-auto px-4 py-3">
            {sendError && (
              <div className="mb-2 px-3 py-1.5 rounded-lg bg-red-950/30 border border-red-800/20 text-[11px] text-red-400 font-mono">
                {sendError}
              </div>
            )}
            <div className="flex items-end gap-2 bg-neutral-900/40 border border-neutral-800/50 rounded-2xl px-3 py-2 focus-within:border-neutral-700/60 transition-colors">
              <div className="w-6 h-6 rounded-full bg-gradient-to-br from-violet-500/25 to-violet-700/25 border border-violet-500/20 flex items-center justify-center shrink-0">
                <span className="text-[8px] font-bold text-violet-300 uppercase font-mono">{userName.slice(0, 2)}</span>
              </div>
              <textarea
                ref={textareaRef}
                value={content}
                onChange={e => setContent(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={`Message ${agent.name}...`}
                maxLength={2000}
                rows={1}
                className="flex-1 bg-transparent text-sm text-neutral-200 placeholder-neutral-600 outline-none resize-none font-mono leading-relaxed max-h-[120px]"
              />
              <button
                type="submit"
                disabled={!content.trim() || sending}
                className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 ${
                  content.trim() && !sending
                    ? "bg-white text-black hover:bg-neutral-200 hover:scale-105"
                    : "bg-neutral-800/40 text-neutral-600 cursor-not-allowed"
                }`}
              >
                {sending ? (
                  <span className="text-xs animate-pulse">...</span>
                ) : (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
                  </svg>
                )}
              </button>
            </div>
            <p className="text-[9px] text-neutral-700 font-mono mt-1.5 text-center">Enter to send, Shift+Enter for newline</p>
          </div>
        </form>
      ) : (
        <div className="border-t border-neutral-800/40 bg-[#0a0a0a]">
          <div className="max-w-3xl mx-auto px-4 py-4 text-center">
            <Link href="/login" className="text-sm text-violet-400 hover:text-violet-300 transition-colors font-mono">
              Sign in to send messages
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
