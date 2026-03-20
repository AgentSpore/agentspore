"use client";

import Link from "next/link";
import { useEffect, useState, useRef, useCallback } from "react";
import { useParams } from "next/navigation";
import { API_URL, FlowStep, FlowStepMessage, STEP_STATUS, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(139 92 246), transparent 70%)" }} />
      <div className="absolute bottom-20 -right-32 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

export default function StepChatPage() {
  const { id: flowId, stepId } = useParams<{ id: string; stepId: string }>();
  const [step, setStep] = useState<FlowStep | null>(null);
  const [messages, setMessages] = useState<FlowStepMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;

  const loadStep = useCallback(() => {
    if (!token) return;
    fetch(`${API_URL}/api/v1/flows/${flowId}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((flow) => {
        if (!flow) return;
        const s = (flow.steps || []).find((st: FlowStep) => st.id === stepId);
        if (s) setStep(s);
      })
      .catch(() => {});
  }, [flowId, stepId, token]);

  const loadInitialMessages = useCallback(() => {
    if (!token) return;
    fetch(`${API_URL}/api/v1/flows/${flowId}/steps/${stepId}/messages?limit=50`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((msgs: FlowStepMessage[]) => {
        setMessages(msgs.reverse());
        setHasMore(msgs.length === 50);
        setLoading(false);
        setTimeout(() => bottomRef.current?.scrollIntoView(), 100);
      })
      .catch(() => setLoading(false));
  }, [flowId, stepId, token]);

  // Poll for new messages only
  const pollNewMessages = useCallback(() => {
    if (!token) return;
    fetch(`${API_URL}/api/v1/flows/${flowId}/steps/${stepId}/messages?limit=10`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((msgs: FlowStepMessage[]) => {
        const newMsgs = msgs.reverse();
        setMessages(prev => {
          if (prev.length === 0) return newMsgs;
          const toAppend = newMsgs.filter(m => !prev.some(p => p.id === m.id));
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
      })
      .catch(() => {});
  }, [flowId, stepId, token]);

  // Load older messages on scroll up
  const loadOlder = useCallback(async () => {
    if (!hasMore || loadingMore || messages.length === 0 || !token) return;
    setLoadingMore(true);
    const oldestId = messages[0].id;
    const container = containerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;

    try {
      const res = await fetch(`${API_URL}/api/v1/flows/${flowId}/steps/${stepId}/messages?limit=50&before=${oldestId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data: FlowStepMessage[] = await res.json();
        setMessages(prev => [...data.reverse(), ...prev]);
        setHasMore(data.length === 50);
        requestAnimationFrame(() => {
          if (container) container.scrollTop = container.scrollHeight - prevHeight;
        });
      }
    } catch { /* ignore */ }
    setLoadingMore(false);
  }, [hasMore, loadingMore, messages, flowId, stepId, token]);

  const handleScroll = () => {
    const container = containerRef.current;
    if (container && container.scrollTop < 50) loadOlder();
  };

  useEffect(() => {
    loadStep();
    loadInitialMessages();
  }, [loadStep, loadInitialMessages]);

  // Poll only for active steps
  useEffect(() => {
    if (!step || ["approved", "failed", "skipped"].includes(step.status)) return;
    const interval = setInterval(() => { loadStep(); pollNewMessages(); }, 5000);
    return () => clearInterval(interval);
  }, [step?.status, loadStep, pollNewMessages]);

  const sendMessage = async () => {
    if (!token || !input.trim() || sending) return;
    setSending(true);
    try {
      await fetch(`${API_URL}/api/v1/flows/${flowId}/steps/${stepId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ content: input.trim() }),
      });
      setInput("");
      pollNewMessages();
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const sts = step ? STEP_STATUS[step.status] || STEP_STATUS.pending : STEP_STATUS.pending;

  // Group messages by date
  const groupedMessages: { date: string; msgs: FlowStepMessage[] }[] = [];
  messages.forEach((m) => {
    const date = new Date(m.created_at).toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric",
    });
    const last = groupedMessages[groupedMessages.length - 1];
    if (last && last.date === date) {
      last.msgs.push(m);
    } else {
      groupedMessages.push({ date, msgs: [m] });
    }
  });

  return (
    <div className="h-screen bg-[#0a0a0a] text-white flex flex-col relative">
      <DotGrid />
      <Header />

      {/* Step header */}
      <div className="relative z-10 border-b border-neutral-800/50 bg-[#0a0a0a]/80 backdrop-blur-md">
        <div className="max-w-3xl mx-auto px-6 py-4">
          <div className="flex items-center gap-1.5 mb-2">
            <Link href="/flows" className="text-[10px] font-mono text-neutral-600 hover:text-neutral-400 transition-colors">
              Flows
            </Link>
            <span className="text-[10px] text-neutral-700">/</span>
            <Link href={`/flows/${flowId}`} className="text-[10px] font-mono text-neutral-600 hover:text-neutral-400 transition-colors">
              Flow
            </Link>
            <span className="text-[10px] text-neutral-700">/</span>
            <span className="text-[10px] font-mono text-neutral-500 truncate max-w-[150px]">
              {step?.title || "Step"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <h1 className="text-lg font-bold truncate tracking-tight">{step?.title || "Loading..."}</h1>
              <div className="flex items-center gap-2 text-[11px] text-neutral-500 font-mono mt-1">
                <span className="text-violet-400/70">@{step?.agent_handle || "..."}</span>
                {step?.started_at && (
                  <>
                    <span className="text-neutral-800">|</span>
                    <span>started {timeAgo(step.started_at)}</span>
                  </>
                )}
              </div>
            </div>
            <span className={`text-[10px] px-2.5 py-0.5 rounded-full border font-mono ${sts.classes}`}>
              {sts.label}
            </span>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto relative z-10">
        <div className="max-w-3xl mx-auto px-6 py-4 space-y-1">
          {loadingMore && (
            <div className="flex justify-center py-3">
              <span className="text-[10px] text-neutral-600 font-mono tracking-widest">LOADING</span>
            </div>
          )}
          {loading && <p className="text-neutral-600 text-sm font-mono">Loading messages...</p>}

          {!loading && messages.length === 0 && (
            <div className="text-center py-16 space-y-2">
              <div className="w-10 h-10 rounded-full bg-neutral-800/50 border border-neutral-700/50 flex items-center justify-center mx-auto">
                <span className="text-neutral-600 text-sm font-mono">&gt;_</span>
              </div>
              <p className="text-neutral-600 text-sm font-mono">No messages yet</p>
              <p className="text-neutral-700 text-xs">Start the conversation below</p>
            </div>
          )}

          {groupedMessages.map((group) => (
            <div key={group.date}>
              <div className="flex items-center gap-3 py-4">
                <div className="flex-1 h-px bg-neutral-800/50" />
                <span className="text-[10px] text-neutral-600 font-mono tracking-wider">{group.date}</span>
                <div className="flex-1 h-px bg-neutral-800/50" />
              </div>
              {group.msgs.map((m) => {
                const isUser = m.sender_type === "user";
                const isSystem = m.sender_type === "system";
                return (
                  <div
                    key={m.id}
                    className={`py-2 ${isUser ? "text-right" : ""}`}
                  >
                    {isSystem ? (
                      <div className="text-[11px] text-neutral-600 italic text-center py-1.5 font-mono">
                        {m.content}
                      </div>
                    ) : (
                      <div className={`inline-block max-w-[80%] rounded-xl px-4 py-3 ${
                        isUser
                          ? "bg-violet-500/10 border border-violet-500/20 text-white ml-auto"
                          : "bg-neutral-900/50 border border-neutral-800/50 text-neutral-200"
                      }`}>
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="text-[10px] font-mono text-neutral-500">
                            {m.sender_name}
                          </span>
                          <span className="text-[10px] text-neutral-700 font-mono">
                            {new Date(m.created_at).toLocaleTimeString("en-US", {
                              hour: "2-digit", minute: "2-digit",
                            })}
                          </span>
                        </div>
                        <div className="text-sm whitespace-pre-wrap break-words leading-relaxed">
                          {m.content}
                        </div>
                        {m.file_url && (
                          <a
                            href={m.file_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-cyan-400 hover:text-cyan-300 mt-1.5 inline-block font-mono"
                          >
                            {m.file_name || "File"}
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input */}
      {step && ["ready", "active", "review"].includes(step.status) && (
        <div className="relative z-10 border-t border-neutral-800/50 bg-[#0a0a0a]/80 backdrop-blur-md">
          <div className="max-w-3xl mx-auto px-6 py-3">
            <div className="flex items-end gap-3">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Send a message..."
                rows={1}
                className="flex-1 bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-3 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors resize-none"
                maxLength={5000}
              />
              <button
                onClick={sendMessage}
                disabled={!input.trim() || sending}
                className="px-5 py-3 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0"
              >
                {sending ? "..." : "Send"}
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .fade-up {
          opacity: 0;
          animation: fadeUp 0.5s ease-out forwards;
        }
      `}</style>
    </div>
  );
}
