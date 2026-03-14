"use client";

import Link from "next/link";
import { useEffect, useState, useRef, useCallback } from "react";
import { useParams } from "next/navigation";
import { API_URL, FlowStep, FlowStepMessage, STEP_STATUS, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

export default function StepChatPage() {
  const { id: flowId, stepId } = useParams<{ id: string; stepId: string }>();
  const [step, setStep] = useState<FlowStep | null>(null);
  const [messages, setMessages] = useState<FlowStepMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
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

  const loadMessages = useCallback(() => {
    if (!token) return;
    fetch(`${API_URL}/api/v1/flows/${flowId}/steps/${stepId}/messages`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((msgs: FlowStepMessage[]) => {
        setMessages(msgs);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [flowId, stepId, token]);

  useEffect(() => {
    loadStep();
    loadMessages();
  }, [loadStep, loadMessages]);

  // Poll only for active steps
  useEffect(() => {
    if (!step || ["approved", "failed", "skipped"].includes(step.status)) return;
    const interval = setInterval(() => { loadStep(); loadMessages(); }, 5000);
    return () => clearInterval(interval);
  }, [step?.status, loadStep, loadMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

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
      loadMessages();
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
    <div className="min-h-screen bg-[#0a0a0a] text-white flex flex-col">
      <Header />

      {/* Step header */}
      <div className="border-b border-neutral-800/80 bg-[#0a0a0a]">
        <div className="max-w-3xl mx-auto px-6 py-4">
          <div className="flex items-center gap-2 text-xs text-neutral-600 mb-1">
            <Link href={`/flows/${flowId}`} className="hover:text-neutral-400 transition-colors">
              ← Back to Flow
            </Link>
          </div>
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <h1 className="text-lg font-bold truncate">{step?.title || "Loading..."}</h1>
              <div className="flex items-center gap-2 text-xs text-neutral-500 font-mono mt-0.5">
                <span>@{step?.agent_handle || "..."}</span>
                {step?.started_at && (
                  <>
                    <span className="text-neutral-700">·</span>
                    <span>started {timeAgo(step.started_at)}</span>
                  </>
                )}
              </div>
            </div>
            <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${sts.classes}`}>
              {sts.label}
            </span>
          </div>

        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-4 space-y-1">
          {loading && <p className="text-neutral-600 text-sm">Loading messages...</p>}

          {!loading && messages.length === 0 && (
            <div className="text-center py-10 text-neutral-600 text-sm">
              No messages yet. Start the conversation.
            </div>
          )}

          {groupedMessages.map((group) => (
            <div key={group.date}>
              <div className="flex items-center gap-3 py-3">
                <div className="flex-1 h-px bg-neutral-800/80" />
                <span className="text-[10px] text-neutral-600 font-mono">{group.date}</span>
                <div className="flex-1 h-px bg-neutral-800/80" />
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
                      <div className="text-xs text-neutral-600 italic text-center py-1">
                        {m.content}
                      </div>
                    ) : (
                      <div className={`inline-block max-w-[80%] rounded-xl px-3.5 py-2.5 ${
                        isUser
                          ? "bg-white/10 text-white ml-auto"
                          : "bg-neutral-800/50 text-neutral-200"
                      }`}>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[10px] font-mono text-neutral-500">
                            {m.sender_name}
                          </span>
                          <span className="text-[10px] text-neutral-700">
                            {new Date(m.created_at).toLocaleTimeString("en-US", {
                              hour: "2-digit", minute: "2-digit",
                            })}
                          </span>
                        </div>
                        <div className="text-sm whitespace-pre-wrap break-words">
                          {m.content}
                        </div>
                        {m.file_url && (
                          <a
                            href={m.file_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-blue-400 hover:text-blue-300 mt-1 inline-block"
                          >
                            {m.file_name || "File"} ↗
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
        <div className="border-t border-neutral-800/80 bg-[#0a0a0a]">
          <div className="max-w-3xl mx-auto px-6 py-3">
            <div className="flex items-end gap-3">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Send a message..."
                rows={1}
                className="flex-1 bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors resize-none"
                maxLength={5000}
              />
              <button
                onClick={sendMessage}
                disabled={!input.trim() || sending}
                className="px-4 py-3 rounded-xl text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0"
              >
                {sending ? "..." : "Send"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
