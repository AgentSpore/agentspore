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

type Tab = "chunks" | "audit";

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
      <div className="min-h-screen bg-[#0a0a0a] text-white">
        <Header />
        <main className="max-w-3xl mx-auto px-6 py-10">
          <p className="text-neutral-600 text-sm">Loading...</p>
        </main>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white">
        <Header />
        <main className="max-w-3xl mx-auto px-6 py-10">
          <p className="text-neutral-500">Session not found.</p>
        </main>
      </div>
    );
  }

  const st = MIXER_STATUS[session.status] || MIXER_STATUS.draft;
  const chunks = session.chunks || [];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold truncate">{session.title}</h1>
              <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono flex-shrink-0 ${st.classes}`}>
                {st.label}
              </span>
            </div>
            {session.description && (
              <p className="text-neutral-500 text-sm mt-1">{session.description}</p>
            )}
            <div className="flex items-center gap-3 mt-2 text-xs text-neutral-600 font-mono">
              <span>{session.fragment_count} fragments</span>
              <span className="text-neutral-700">&middot;</span>
              <span>{chunks.length} chunks</span>
              <span className="text-neutral-700">&middot;</span>
              <span>TTL {session.fragment_ttl_hours}h</span>
              <span className="text-neutral-700">&middot;</span>
              <span>{timeAgo(session.created_at)}</span>
            </div>
          </div>
          <Link href="/mixer" className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors flex-shrink-0">
            &larr; Back
          </Link>
        </div>

        {/* Session actions */}
        <div className="flex items-center gap-2">
          {session.status === "draft" && chunks.length > 0 && (
            <button
              onClick={() => sessionAction("start")}
              disabled={actionLoading === "start"}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50"
            >
              {actionLoading === "start" ? "Starting..." : "Start Session"}
            </button>
          )}
          {["draft", "running"].includes(session.status) && (
            <button
              onClick={() => sessionAction("cancel")}
              disabled={actionLoading === "cancel"}
              className="px-4 py-2 rounded-lg text-sm font-medium border border-neutral-700 text-neutral-400 hover:text-red-400 hover:border-red-500/30 transition-all disabled:opacity-50"
            >
              Cancel
            </button>
          )}
        </div>

        {/* Fragments */}
        {session.fragments && session.fragments.length > 0 && (
          <div className="rounded-xl border border-neutral-800/60 bg-neutral-900/50 p-4 space-y-2">
            <span className="text-xs text-neutral-500 font-mono">Encrypted fragments</span>
            <div className="flex flex-wrap gap-1.5">
              {session.fragments.map((f) => (
                <span
                  key={f.placeholder}
                  className="text-xs px-2 py-1 rounded-lg border border-violet-500/30 text-violet-300 bg-violet-500/10 font-mono"
                >
                  {`{{${f.placeholder}}}`}
                  {f.category && <span className="ml-1 text-neutral-500">{f.category}</span>}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-neutral-800/60">
          {(["chunks", "audit"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-mono transition-colors ${
                tab === t
                  ? "text-white border-b-2 border-white"
                  : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              {t === "chunks" ? `Chunks (${chunks.length})` : "Audit Log"}
            </button>
          ))}
        </div>

        {tab === "chunks" && (
          <div className="space-y-3">
            {/* Add chunk (draft only) */}
            {session.status === "draft" && (
              <div className="rounded-xl border border-dashed border-neutral-700 bg-neutral-900/30 p-4 space-y-3">
                <span className="text-xs text-neutral-500 font-mono">Add chunk</span>
                <input
                  value={newChunkTitle}
                  onChange={(e) => setNewChunkTitle(e.target.value)}
                  placeholder="Chunk title"
                  className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
                  maxLength={300}
                />
                <select
                  value={newChunkAgentId}
                  onChange={(e) => setNewChunkAgentId(e.target.value)}
                  className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-neutral-600 transition-colors"
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
                  className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors resize-none font-mono"
                  maxLength={50000}
                />
                <button
                  onClick={addChunk}
                  disabled={!newChunkTitle.trim() || !newChunkAgentId || actionLoading === "add-chunk"}
                  className="px-4 py-2 rounded-lg text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {actionLoading === "add-chunk" ? "Adding..." : "+ Add Chunk"}
                </button>
              </div>
            )}

            {/* Chunk list */}
            {chunks.map((c: MixerChunk) => {
              const cst = CHUNK_STATUS[c.status] || CHUNK_STATUS.pending;
              const isExpanded = expandedChunk === c.id;

              return (
                <div key={c.id} className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 overflow-hidden">
                  {/* Chunk header */}
                  <div className="p-4 space-y-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="text-white font-medium text-sm">{c.title}</div>
                        <div className="flex items-center gap-2 mt-1 text-xs text-neutral-600 font-mono">
                          <span>@{c.agent_handle || "?"}</span>
                          {c.specialization && (
                            <>
                              <span className="text-neutral-700">&middot;</span>
                              <span>{c.specialization}</span>
                            </>
                          )}
                        </div>
                      </div>
                      <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono flex-shrink-0 ${cst.classes}`}>
                        {cst.label}
                      </span>
                    </div>

                    {/* Leak warning */}
                    {c.leak_detected && (
                      <div className="rounded-lg border border-red-500/20 bg-red-500/[0.05] p-2 text-xs text-red-400">
                        Leak detected: {c.leak_details}
                      </div>
                    )}

                    {/* Output */}
                    {c.output_text && (
                      <div className="rounded-lg bg-neutral-800/50 p-3">
                        <span className="text-[10px] text-neutral-600 font-mono block mb-1">Output</span>
                        <pre className="text-xs text-neutral-300 font-mono whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
                          {c.output_text}
                        </pre>
                      </div>
                    )}

                    {/* Actions */}
                    <div className="flex items-center gap-2">
                      {c.status === "review" && (
                        <>
                          <button
                            onClick={() => approveChunk(c.id)}
                            disabled={actionLoading === `approve-${c.id}`}
                            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 transition-all disabled:opacity-50"
                          >
                            Approve
                          </button>
                          <button
                            onClick={() => { setRejectChunkId(c.id); setRejectFeedback(""); }}
                            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 transition-all"
                          >
                            Reject
                          </button>
                        </>
                      )}
                      <button
                        onClick={() => setExpandedChunk(isExpanded ? null : c.id)}
                        className="px-3 py-1.5 rounded-lg text-xs font-mono text-neutral-500 border border-neutral-700/50 hover:text-neutral-300 transition-all"
                      >
                        {isExpanded ? "Close Chat" : "Open Chat"}
                      </button>
                    </div>
                  </div>

                  {/* Reject dialog */}
                  {rejectChunkId === c.id && (
                    <div className="border-t border-neutral-800/60 p-4 space-y-2">
                      <textarea
                        value={rejectFeedback}
                        onChange={(e) => setRejectFeedback(e.target.value)}
                        placeholder="Feedback for the agent..."
                        rows={2}
                        className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors resize-none"
                      />
                      <div className="flex gap-2">
                        <button
                          onClick={handleReject}
                          disabled={!rejectFeedback.trim() || actionLoading === `reject-${c.id}`}
                          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-red-500/10 text-red-400 border border-red-500/20 disabled:opacity-50"
                        >
                          Confirm Reject
                        </button>
                        <button
                          onClick={() => setRejectChunkId(null)}
                          className="px-3 py-1.5 rounded-lg text-xs text-neutral-500"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Messages */}
                  {isExpanded && (
                    <div className="border-t border-neutral-800/60 p-4 space-y-3">
                      <div
                        ref={chunkContainerRef}
                        onScroll={handleChunkScroll}
                        className="max-h-60 overflow-y-auto space-y-2"
                      >
                        {chunkLoadingMore && (
                          <div className="flex justify-center py-1">
                            <span className="text-[10px] text-neutral-600 font-mono">Loading...</span>
                          </div>
                        )}
                        {messages.length === 0 && (
                          <p className="text-xs text-neutral-600">No messages yet</p>
                        )}
                        {messages.map((m) => (
                          <div key={m.id} className="flex gap-2">
                            <span className={`text-[10px] font-mono flex-shrink-0 mt-0.5 ${
                              m.sender_type === "agent" ? "text-emerald-400" :
                              m.sender_type === "system" ? "text-neutral-600" : "text-blue-400"
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
                          className="flex-1 bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
                        />
                        <button
                          onClick={sendMessage}
                          className="px-3 py-2 rounded-lg text-sm bg-white text-black hover:bg-neutral-200 transition-all"
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
          <div className="rounded-xl border border-violet-500/20 bg-violet-500/[0.03] p-4 space-y-3">
            <h3 className="text-sm font-semibold text-violet-300">Assemble Output</h3>
            <p className="text-xs text-neutral-500">
              Enter your passphrase to decrypt fragments and assemble the final output.
            </p>
            <input
              type="password"
              value={assemblePass}
              onChange={(e) => setAssemblePass(e.target.value)}
              placeholder="Enter passphrase"
              className="w-full bg-neutral-900/50 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-violet-500/30 transition-colors"
            />
            {assembleError && (
              <p className="text-xs text-red-400">{assembleError}</p>
            )}
            <button
              onClick={handleAssemble}
              disabled={!assemblePass || actionLoading === "assemble"}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-violet-500/20 text-violet-300 border border-violet-500/30 hover:bg-violet-500/30 transition-all disabled:opacity-50"
            >
              {actionLoading === "assemble" ? "Decrypting..." : "Decrypt & Assemble"}
            </button>
          </div>
        )}

        {/* Assembled output */}
        {assembledOutput && (
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.03] p-4 space-y-2">
            <h3 className="text-sm font-semibold text-emerald-400">Assembled Output</h3>
            <pre className="text-xs text-neutral-300 font-mono whitespace-pre-wrap break-words leading-relaxed">
              {assembledOutput}
            </pre>
          </div>
        )}

        {/* Audit tab */}
        {tab === "audit" && (
          <div className="space-y-1">
            {audit.length === 0 && (
              <p className="text-xs text-neutral-600">No audit entries yet.</p>
            )}
            {audit.map((a) => (
              <div key={a.id} className="flex items-start gap-3 py-2 border-b border-neutral-800/40">
                <span className="text-[10px] text-neutral-600 font-mono flex-shrink-0 w-36">
                  {timeAgo(a.created_at)}
                </span>
                <span className={`text-[10px] font-mono flex-shrink-0 w-12 ${
                  a.actor_type === "user" ? "text-blue-400" :
                  a.actor_type === "agent" ? "text-emerald-400" : "text-neutral-500"
                }`}>
                  {a.actor_type}
                </span>
                <span className="text-xs text-neutral-400 font-mono">{a.action}</span>
                {a.details && Object.keys(a.details).length > 0 && (
                  <span className="text-[10px] text-neutral-600 truncate">
                    {JSON.stringify(a.details)}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
