"use client";

import Link from "next/link";
import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import {
  API_URL,
  MixerSession,
  MixerChunk,
  MixerChunkMessage,
  MixerAuditEntry,
  MIXER_STATUS,
  CHUNK_STATUS,
  timeAgo,
} from "@/lib/api";
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

type Tab = "chunks" | "audit";

const CHUNK_STATUS_COLORS: Record<string, string> = {
  pending: "text-neutral-400 border-neutral-700/60",
  running: "text-cyan-400 border-cyan-500/30",
  review: "text-orange-400 border-orange-500/30",
  approved: "text-emerald-400 border-emerald-500/30",
  rejected: "text-red-400 border-red-500/30",
  failed: "text-red-400 border-red-500/30",
};

const SESSION_STATUS_COLORS: Record<string, string> = {
  draft: "text-neutral-400 border-neutral-700/60",
  running: "text-cyan-400 border-cyan-500/30",
  assembling: "text-violet-400 border-violet-500/30",
  completed: "text-emerald-400 border-emerald-500/30",
  cancelled: "text-orange-400 border-orange-500/30",
};

export default function MixerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [session, setSession] = useState<MixerSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [tab, setTab] = useState<Tab>("chunks");

  // Assembly
  const [assemblePass, setAssemblePass] = useState("");
  const [assembledOutput, setAssembledOutput] = useState<string | null>(null);
  const [assembleError, setAssembleError] = useState("");

  // Chunk actions
  const [rejectChunkId, setRejectChunkId] = useState<string | null>(null);
  const [rejectFeedback, setRejectFeedback] = useState("");

  // Messages
  const [expandedChunk, setExpandedChunk] = useState<string | null>(null);
  const [messages, setMessages] = useState<MixerChunkMessage[]>([]);
  const [newMessage, setNewMessage] = useState("");
  const [chunkHasMore, setChunkHasMore] = useState(true);
  const [chunkLoadingMore, setChunkLoadingMore] = useState(false);
  const chunkContainerRef = useRef<HTMLDivElement>(null);
  const chunkBottomRef = useRef<HTMLDivElement>(null);

  // Audit
  const [audit, setAudit] = useState<MixerAuditEntry[]>([]);

  // Chunk builder (draft)
  const [newChunkTitle, setNewChunkTitle] = useState("");
  const [newChunkAgentId, setNewChunkAgentId] = useState("");
  const [newChunkInstructions, setNewChunkInstructions] = useState("");
  const [agents, setAgents] = useState<{ id: string; handle: string; name: string; specialization: string; model_provider: string }[]>([]);

  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;

  const loadSession = useCallback(() => {
    if (!token || !id) return;
    fetch(`${API_URL}/api/v1/mixer/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setSession(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [id, token]);

  useEffect(() => {
    loadSession();
    const interval = setInterval(loadSession, 5000);
    return () => clearInterval(interval);
  }, [loadSession]);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/agents/leaderboard`)
      .then((r) => r.json())
      .then((d) => setAgents(Array.isArray(d) ? d : []))
      .catch(() => {});
  }, []);

  // Load messages for expanded chunk (initial load)
  useEffect(() => {
    if (!expandedChunk || !token) return;
    setChunkHasMore(true);
    fetch(`${API_URL}/api/v1/mixer/${id}/chunks/${expandedChunk}/messages?limit=50`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((data: MixerChunkMessage[]) => {
        setMessages(data.reverse());
        setChunkHasMore(data.length === 50);
        setTimeout(() => chunkBottomRef.current?.scrollIntoView(), 100);
      })
      .catch(() => {});
  }, [expandedChunk, id, token]);

  const loadOlderChunkMessages = async () => {
    if (!chunkHasMore || chunkLoadingMore || messages.length === 0 || !expandedChunk || !token) return;
    setChunkLoadingMore(true);
    const oldestId = messages[0].id;
    const container = chunkContainerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;

    try {
      const res = await fetch(`${API_URL}/api/v1/mixer/${id}/chunks/${expandedChunk}/messages?limit=50&before=${oldestId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data: MixerChunkMessage[] = await res.json();
        setMessages(prev => [...data.reverse(), ...prev]);
        setChunkHasMore(data.length === 50);
        requestAnimationFrame(() => {
          if (container) container.scrollTop = container.scrollHeight - prevHeight;
        });
      }
    } catch { /* ignore */ }
    setChunkLoadingMore(false);
  };

  const handleChunkScroll = () => {
    const container = chunkContainerRef.current;
    if (container && container.scrollTop < 50) loadOlderChunkMessages();
  };

  // Load audit log
  useEffect(() => {
    if (tab !== "audit" || !token) return;
    fetch(`${API_URL}/api/v1/mixer/${id}/audit`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then(setAudit)
      .catch(() => {});
  }, [tab, id, token]);

  const sessionAction = async (action: string) => {
    if (!token) return;
    setActionLoading(action);
    try {
      await fetch(`${API_URL}/api/v1/mixer/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      });
      loadSession();
    } finally {
      setActionLoading("");
    }
  };

  const approveChunk = async (chunkId: string) => {
    if (!token) return;
    setActionLoading(`approve-${chunkId}`);
    try {
      await fetch(`${API_URL}/api/v1/mixer/${id}/chunks/${chunkId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      });
      loadSession();
    } finally {
      setActionLoading("");
    }
  };

  const handleReject = async () => {
    if (!token || !rejectChunkId) return;
    setActionLoading(`reject-${rejectChunkId}`);
    try {
      await fetch(`${API_URL}/api/v1/mixer/${id}/chunks/${rejectChunkId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ feedback: rejectFeedback }),
      });
      setRejectChunkId(null);
      setRejectFeedback("");
      loadSession();
    } finally {
      setActionLoading("");
    }
  };

  const handleAssemble = async () => {
    if (!token) return;
    setAssembleError("");
    setActionLoading("assemble");
    try {
      const res = await fetch(`${API_URL}/api/v1/mixer/${id}/assemble`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ passphrase: assemblePass }),
      });
      const data = await res.json();
      if (!res.ok) {
        setAssembleError(data.detail || "Assembly failed");
      } else {
        setAssembledOutput(data.assembled_output);
        loadSession();
      }
    } finally {
      setActionLoading("");
    }
  };

  const sendMessage = async () => {
    if (!token || !expandedChunk || !newMessage.trim()) return;
    await fetch(`${API_URL}/api/v1/mixer/${id}/chunks/${expandedChunk}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ content: newMessage.trim() }),
    });
    setNewMessage("");
    // Reload recent messages and append new
    const res = await fetch(`${API_URL}/api/v1/mixer/${id}/chunks/${expandedChunk}/messages?limit=10`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (res.ok) {
      const data: MixerChunkMessage[] = await res.json();
      const newMsgs = data.reverse();
      setMessages(prev => {
        const toAppend = newMsgs.filter(m => !prev.some(p => p.id === m.id));
        return toAppend.length > 0 ? [...prev, ...toAppend] : prev;
      });
      setTimeout(() => chunkBottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    }
  };

  const addChunk = async () => {
    if (!token || !newChunkTitle.trim() || !newChunkAgentId) return;
    setActionLoading("add-chunk");
    try {
      await fetch(`${API_URL}/api/v1/mixer/${id}/chunks`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          agent_id: newChunkAgentId,
          title: newChunkTitle.trim(),
          instructions: newChunkInstructions.trim() || null,
        }),
      });
      setNewChunkTitle("");
      setNewChunkAgentId("");
      setNewChunkInstructions("");
      loadSession();
    } finally {
      setActionLoading("");
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white relative">
        <DotGrid />
        <Header />
        <main className="relative max-w-4xl mx-auto px-6 py-12">
          <div className="flex items-center gap-3 py-20 justify-center">
            <div className="w-4 h-4 border-2 border-violet-500/30 border-t-violet-400 rounded-full animate-spin" />
            <span className="text-neutral-600 text-sm font-mono">Loading session...</span>
          </div>
        </main>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white relative">
        <DotGrid />
        <Header />
        <main className="relative max-w-4xl mx-auto px-6 py-12">
          <p className="text-neutral-500 text-center py-20">Session not found.</p>
        </main>
      </div>
    );
  }

  const st = MIXER_STATUS[session.status] || MIXER_STATUS.draft;
  const sessionStatusColor = SESSION_STATUS_COLORS[session.status] || SESSION_STATUS_COLORS.draft;
  const chunks = session.chunks || [];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <main className="relative max-w-4xl mx-auto px-6 py-12 space-y-8">
        {/* Breadcrumb */}
        <div className="text-[10px] font-mono text-neutral-600 tracking-wide fade-up">
          <Link href="/" className="hover:text-neutral-400 transition-colors">HOME</Link>
          <span className="mx-2">/</span>
          <Link href="/mixer" className="hover:text-neutral-400 transition-colors">MIXER</Link>
          <span className="mx-2">/</span>
          <span className="text-neutral-400">SESSION</span>
        </div>

        {/* Session header */}
        <div className="fade-up" style={{ animationDelay: "0.05s" }}>
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-3 mb-2">
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Session</span>
                <span className={`text-[10px] px-2.5 py-1 rounded-full border font-mono ${sessionStatusColor}`}>
                  {st.label}
                </span>
              </div>
              <h1 className="text-2xl font-bold tracking-tight truncate">{session.title}</h1>
              {session.description && (
                <p className="text-neutral-500 text-sm mt-2">{session.description}</p>
              )}
              <div className="flex items-center gap-3 mt-3 text-[11px] text-neutral-600 font-mono">
                <span>{session.fragment_count} fragments</span>
                <span className="text-neutral-800">&middot;</span>
                <span>{chunks.length} chunks</span>
                <span className="text-neutral-800">&middot;</span>
                <span>TTL {session.fragment_ttl_hours}h</span>
                <span className="text-neutral-800">&middot;</span>
                <span>{timeAgo(session.created_at)}</span>
              </div>
            </div>
            <Link
              href="/mixer"
              className="text-[11px] font-mono text-neutral-500 hover:text-neutral-300 transition-colors flex-shrink-0 px-3 py-1.5 rounded-lg border border-neutral-800/50 bg-neutral-900/30 hover:border-neutral-700/60"
            >
              &larr; Back
            </Link>
          </div>
        </div>

        {/* Session actions */}
        <div className="flex items-center gap-3 fade-up" style={{ animationDelay: "0.1s" }}>
          {session.status === "draft" && chunks.length > 0 && (
            <button
              onClick={() => sessionAction("start")}
              disabled={actionLoading === "start"}
              className="px-5 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50"
            >
              {actionLoading === "start" ? "Starting..." : "Start Session"}
            </button>
          )}
          {["draft", "running"].includes(session.status) && (
            <button
              onClick={() => sessionAction("cancel")}
              disabled={actionLoading === "cancel"}
              className="px-5 py-2.5 rounded-lg text-sm font-mono border border-neutral-800/50 bg-neutral-900/30 text-neutral-400 hover:text-red-400 hover:border-red-500/30 transition-all disabled:opacity-50"
            >
              Cancel
            </button>
          )}
        </div>

        {/* Fragments */}
        {session.fragments && session.fragments.length > 0 && (
          <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 space-y-3 fade-up" style={{ animationDelay: "0.12s" }}>
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Encrypted Fragments</span>
            <div className="flex flex-wrap gap-2">
              {session.fragments.map((f) => (
                <span
                  key={f.placeholder}
                  className="text-xs px-2.5 py-1 rounded-lg border border-violet-500/30 text-violet-300 bg-violet-500/10 font-mono"
                >
                  {`{{${f.placeholder}}}`}
                  {f.category && <span className="ml-1.5 text-neutral-500">{f.category}</span>}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-neutral-800/50 fade-up" style={{ animationDelay: "0.15s" }}>
          {(["chunks", "audit"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-5 py-2.5 text-sm font-mono transition-colors ${
                tab === t
                  ? "text-violet-300 border-b-2 border-violet-400"
                  : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              {t === "chunks" ? `Chunks (${chunks.length})` : "Audit Log"}
            </button>
          ))}
        </div>

        {tab === "chunks" && (
          <div className="space-y-4">
            {/* Add chunk (draft only) */}
            {session.status === "draft" && (
              <div className="rounded-xl border border-dashed border-neutral-700/60 bg-neutral-900/20 backdrop-blur-sm p-5 space-y-4 fade-up" style={{ animationDelay: "0.18s" }}>
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Add Chunk</span>
                <input
                  value={newChunkTitle}
                  onChange={(e) => setNewChunkTitle(e.target.value)}
                  placeholder="Chunk title"
                  className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-2.5 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors"
                  maxLength={300}
                />
                <select
                  value={newChunkAgentId}
                  onChange={(e) => setNewChunkAgentId(e.target.value)}
                  className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-2.5 text-sm text-white font-mono focus:outline-none focus:border-violet-500/50 transition-colors"
                >
                  <option value="">Select agent...</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>
                      @{a.handle} &mdash; {a.name} ({a.specialization}, {a.model_provider})
                    </option>
                  ))}
                </select>
                <textarea
                  value={newChunkInstructions}
                  onChange={(e) => setNewChunkInstructions(e.target.value)}
                  placeholder="Instructions with {{MIX_xxxxxx}} placeholders"
                  rows={3}
                  className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-2.5 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors resize-none"
                  maxLength={50000}
                />
                <button
                  onClick={addChunk}
                  disabled={!newChunkTitle.trim() || !newChunkAgentId || actionLoading === "add-chunk"}
                  className="px-5 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {actionLoading === "add-chunk" ? "Adding..." : "+ Add Chunk"}
                </button>
              </div>
            )}

            {/* Chunk list */}
            {chunks.map((c: MixerChunk, idx: number) => {
              const cst = CHUNK_STATUS[c.status] || CHUNK_STATUS.pending;
              const chunkStatusColor = CHUNK_STATUS_COLORS[c.status] || CHUNK_STATUS_COLORS.pending;
              const isExpanded = expandedChunk === c.id;

              return (
                <div
                  key={c.id}
                  className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden hover:border-neutral-700/60 transition-all fade-up"
                  style={{ animationDelay: `${0.15 + idx * 0.04}s` }}
                >
                  {/* Chunk header */}
                  <div className="p-5 space-y-3">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="text-white font-medium text-sm">{c.title}</div>
                        <div className="flex items-center gap-2 mt-1.5 text-[11px] text-neutral-600 font-mono">
                          <span className="text-cyan-400/70">@{c.agent_handle || "?"}</span>
                          {c.specialization && (
                            <>
                              <span className="text-neutral-800">&middot;</span>
                              <span>{c.specialization}</span>
                            </>
                          )}
                        </div>
                      </div>
                      <span className={`text-[10px] px-2.5 py-1 rounded-full border font-mono flex-shrink-0 ${chunkStatusColor}`}>
                        {cst.label}
                      </span>
                    </div>

                    {/* Leak warning */}
                    {c.leak_detected && (
                      <div className="rounded-lg border border-red-500/20 bg-red-500/[0.05] p-3 text-xs text-red-400 font-mono flex items-center gap-2">
                        <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
                        </svg>
                        Leak detected: {c.leak_details}
                      </div>
                    )}

                    {/* Output */}
                    {c.output_text && (
                      <div className="rounded-lg bg-neutral-800/30 border border-neutral-800/50 p-4">
                        <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 block mb-2">Output</span>
                        <pre className="text-xs text-neutral-300 font-mono whitespace-pre-wrap break-words max-h-40 overflow-y-auto leading-relaxed">
                          {c.output_text}
                        </pre>
                      </div>
                    )}

                    {/* Actions */}
                    <div className="flex items-center gap-2 pt-1">
                      {c.status === "review" && (
                        <>
                          <button
                            onClick={() => approveChunk(c.id)}
                            disabled={actionLoading === `approve-${c.id}`}
                            className="px-4 py-2 rounded-lg text-xs font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 transition-all disabled:opacity-50"
                          >
                            Approve
                          </button>
                          <button
                            onClick={() => { setRejectChunkId(c.id); setRejectFeedback(""); }}
                            className="px-4 py-2 rounded-lg text-xs font-mono bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 transition-all"
                          >
                            Reject
                          </button>
                        </>
                      )}
                      <button
                        onClick={() => setExpandedChunk(isExpanded ? null : c.id)}
                        className="px-4 py-2 rounded-lg text-xs font-mono text-neutral-500 border border-neutral-800/50 bg-neutral-900/30 hover:text-neutral-300 hover:border-neutral-700/60 transition-all"
                      >
                        {isExpanded ? "Close Chat" : "Open Chat"}
                      </button>
                    </div>
                  </div>

                  {/* Reject dialog */}
                  {rejectChunkId === c.id && (
                    <div className="border-t border-neutral-800/50 p-5 space-y-3 bg-red-500/[0.02]">
                      <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Rejection Feedback</span>
                      <textarea
                        value={rejectFeedback}
                        onChange={(e) => setRejectFeedback(e.target.value)}
                        placeholder="Feedback for the agent..."
                        rows={2}
                        className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-2.5 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-red-500/30 transition-colors resize-none"
                      />
                      <div className="flex gap-2">
                        <button
                          onClick={handleReject}
                          disabled={!rejectFeedback.trim() || actionLoading === `reject-${c.id}`}
                          className="px-4 py-2 rounded-lg text-xs font-mono bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 transition-all disabled:opacity-50"
                        >
                          Confirm Reject
                        </button>
                        <button
                          onClick={() => setRejectChunkId(null)}
                          className="px-4 py-2 rounded-lg text-xs font-mono text-neutral-500 hover:text-neutral-300 transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Messages */}
                  {isExpanded && (
                    <div className="border-t border-neutral-800/50 p-5 space-y-3 bg-neutral-900/20">
                      <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Chunk Messages</span>
                      <div
                        ref={chunkContainerRef}
                        onScroll={handleChunkScroll}
                        className="max-h-64 overflow-y-auto space-y-2 rounded-lg bg-neutral-900/50 border border-neutral-800/50 p-3"
                      >
                        {chunkLoadingMore && (
                          <div className="flex justify-center py-2">
                            <div className="w-3 h-3 border border-violet-500/30 border-t-violet-400 rounded-full animate-spin" />
                          </div>
                        )}
                        {messages.length === 0 && (
                          <p className="text-xs text-neutral-600 font-mono text-center py-4">No messages yet</p>
                        )}
                        {messages.map((m) => (
                          <div key={m.id} className="flex gap-3 py-1">
                            <span className={`text-[10px] font-mono flex-shrink-0 mt-0.5 ${
                              m.sender_type === "agent" ? "text-emerald-400" :
                              m.sender_type === "system" ? "text-neutral-600" : "text-cyan-400"
                            }`}>
                              {m.sender_name}
                            </span>
                            <span className="text-xs text-neutral-300">{m.content}</span>
                          </div>
                        ))}
                        <div ref={chunkBottomRef} />
                      </div>
                      <div className="flex gap-2">
                        <input
                          value={newMessage}
                          onChange={(e) => setNewMessage(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
                          placeholder="Send a message..."
                          className="flex-1 bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-2.5 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors"
                        />
                        <button
                          onClick={sendMessage}
                          className="px-5 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all"
                        >
                          Send
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Assembly section */}
        {(session.status === "assembling" || session.status === "running") && (
          <div className="rounded-xl border border-violet-500/20 bg-violet-500/[0.03] backdrop-blur-sm p-6 space-y-4 fade-up" style={{ animationDelay: "0.2s" }}>
            <div>
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-violet-400/60 block mb-1">Decryption</span>
              <h3 className="text-sm font-semibold text-violet-300">Assemble Output</h3>
            </div>
            <p className="text-xs text-neutral-500 font-mono">
              Enter your passphrase to decrypt fragments and assemble the final output.
            </p>
            <input
              type="password"
              value={assemblePass}
              onChange={(e) => setAssemblePass(e.target.value)}
              placeholder="Enter passphrase"
              className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-2.5 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors"
            />
            {assembleError && (
              <p className="text-xs text-red-400 font-mono">{assembleError}</p>
            )}
            <button
              onClick={handleAssemble}
              disabled={!assemblePass || actionLoading === "assemble"}
              className="px-5 py-2.5 rounded-lg text-sm font-mono bg-violet-500/20 text-violet-300 border border-violet-500/30 hover:bg-violet-500/30 transition-all disabled:opacity-50"
            >
              {actionLoading === "assemble" ? "Decrypting..." : "Decrypt & Assemble"}
            </button>
          </div>
        )}

        {/* Assembled output */}
        {assembledOutput && (
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.03] backdrop-blur-sm p-6 space-y-3 fade-up">
            <div>
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-emerald-400/60 block mb-1">Result</span>
              <h3 className="text-sm font-semibold text-emerald-400">Assembled Output</h3>
            </div>
            <pre className="text-xs text-neutral-300 font-mono whitespace-pre-wrap break-words leading-relaxed">
              {assembledOutput}
            </pre>
          </div>
        )}

        {/* Audit tab */}
        {tab === "audit" && (
          <div className="space-y-1 fade-up" style={{ animationDelay: "0.15s" }}>
            {audit.length === 0 && (
              <p className="text-xs text-neutral-600 font-mono text-center py-8">No audit entries yet.</p>
            )}
            {audit.map((a, idx) => (
              <div
                key={a.id}
                className="flex items-start gap-4 py-3 border-b border-neutral-800/30 hover:bg-neutral-900/20 px-3 rounded-lg transition-colors"
              >
                <span className="text-[10px] text-neutral-600 font-mono flex-shrink-0 w-32">
                  {timeAgo(a.created_at)}
                </span>
                <span className={`text-[10px] font-mono flex-shrink-0 w-14 ${
                  a.actor_type === "user" ? "text-cyan-400" :
                  a.actor_type === "agent" ? "text-emerald-400" : "text-neutral-500"
                }`}>
                  {a.actor_type}
                </span>
                <span className="text-xs text-neutral-400 font-mono">{a.action}</span>
                {a.details && Object.keys(a.details).length > 0 && (
                  <span className="text-[10px] text-neutral-600 font-mono truncate">
                    {JSON.stringify(a.details)}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </main>

      <style jsx global>{`
        .fade-up {
          opacity: 0;
          transform: translateY(12px);
          animation: fadeUpIn 0.5s ease-out forwards;
        }
        @keyframes fadeUpIn {
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
    </div>
  );
}
