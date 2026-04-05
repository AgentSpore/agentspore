"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_URL, Rental, RentalMessage, timeAgo } from "@/lib/api";

const STATUS_BADGE: Record<string, string> = {
  active:          "bg-emerald-400/10 text-emerald-400 border border-emerald-400/20",
  awaiting_review: "bg-amber-400/10 text-amber-400 border border-amber-400/20",
  completed:       "bg-neutral-800/50 text-neutral-400 border border-neutral-700/30",
  cancelled:       "bg-red-400/10 text-red-400 border border-red-400/20",
};

const STATUS_DOT: Record<string, string> = {
  active:          "bg-emerald-400",
  awaiting_review: "bg-amber-400",
  completed:       "bg-neutral-500",
  cancelled:       "bg-red-400",
};

const STATUS_LABEL: Record<string, string> = {
  active:          "Active",
  awaiting_review: "Awaiting Review",
  completed:       "Completed",
  cancelled:       "Cancelled",
};

function authHeaders(): Record<string, string> {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  return token ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}

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

/* ─── Complete rental modal ──────────────────────────────────────────────── */

function CompleteModal({
  open,
  onClose,
  onSubmit,
  submitting,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (rating: number, review: string) => void;
  submitting: boolean;
}) {
  const [rating, setRating] = useState(0);
  const [review, setReview] = useState("");

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-md mx-4 bg-[#0a0a0a] border border-neutral-800/50 rounded-xl overflow-hidden">
        {/* Colored top accent */}
        <div className="h-[2px] w-full bg-gradient-to-r from-violet-400 to-transparent" />
        <div className="p-6 space-y-5">
          <div>
            <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">Finalize</p>
            <h3 className="text-lg font-medium text-white font-mono">Complete Rental</h3>
          </div>

          {/* Stars */}
          <div>
            <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600 mb-2">Rating</p>
            <div className="flex gap-1">
              {[1, 2, 3, 4, 5].map((star) => (
                <button
                  key={star}
                  type="button"
                  onClick={() => setRating(star)}
                  className={`text-2xl transition-colors ${
                    star <= rating ? "text-amber-400" : "text-neutral-700 hover:text-neutral-500"
                  }`}
                >
                  {star <= rating ? "\u2605" : "\u2606"}
                </button>
              ))}
            </div>
          </div>

          {/* Review */}
          <div>
            <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600 mb-2">Review (optional)</p>
            <textarea
              value={review}
              onChange={(e) => setReview(e.target.value)}
              placeholder="How was your experience?"
              rows={3}
              maxLength={1000}
              className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-3 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-neutral-700/60 resize-none"
            />
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="bg-neutral-800/30 border border-neutral-800/50 text-neutral-400 hover:text-white text-sm font-mono px-4 py-2 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => { if (rating > 0) onSubmit(rating, review.trim()); }}
              disabled={rating === 0 || submitting}
              className="bg-white text-black font-medium font-mono text-sm px-5 py-2 rounded-lg hover:bg-neutral-200 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
            >
              {submitting ? "Submitting..." : "Submit"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── Main page ──────────────────────────────────────────────────────────── */

export default function RentalChatPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [rental, setRental] = useState<Rental | null>(null);
  const [messages, setMessages] = useState<RentalMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const [completeOpen, setCompleteOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const [hasMore, setHasMore] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ─── Auth guard ───────────────────────────────────────────────────────
  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/login");
    }
  }, [router]);

  // ─── Load rental ──────────────────────────────────────────────────────
  useEffect(() => {
    const headers = authHeaders();
    fetch(`${API_URL}/api/v1/rentals/${id}`, { headers })
      .then((r) => {
        if (r.status === 401) { router.replace("/login"); throw new Error("Unauthorized"); }
        if (!r.ok) throw new Error("Not found");
        return r.json();
      })
      .then((data: Rental) => { setRental(data); setLoading(false); })
      .catch((err) => { if (err.message !== "Unauthorized") { setError(err.message); setLoading(false); } });
  }, [id, router]);

  // ─── Initial load ────────────────────────────────────────────────────
  const loadInitial = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/rentals/${id}/messages?limit=50`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const data: RentalMessage[] = await res.json();
        setMessages(data.reverse());
        setHasMore(data.length === 50);
        setTimeout(() => bottomRef.current?.scrollIntoView(), 100);
      }
    } catch { /* ignore */ }
  }, [id]);

  // Poll for new messages
  const pollNewMessages = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/rentals/${id}/messages?limit=10`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const data: RentalMessage[] = await res.json();
        const newMsgs = data.reverse();
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
      }
    } catch { /* ignore */ }
  }, [id]);

  // Load older messages on scroll up
  const loadOlder = useCallback(async () => {
    if (!hasMore || loadingOlder || messages.length === 0) return;
    setLoadingOlder(true);
    const oldestId = messages[0].id;
    const container = containerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;

    try {
      const res = await fetch(`${API_URL}/api/v1/rentals/${id}/messages?limit=50&before=${oldestId}`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const data: RentalMessage[] = await res.json();
        setMessages(prev => [...data.reverse(), ...prev]);
        setHasMore(data.length === 50);
        requestAnimationFrame(() => {
          if (container) container.scrollTop = container.scrollHeight - prevHeight;
        });
      }
    } catch { /* ignore */ }
    setLoadingOlder(false);
  }, [hasMore, loadingOlder, messages, id]);

  const handleScroll = () => {
    const container = containerRef.current;
    if (container && container.scrollTop < 50) loadOlder();
  };

  useEffect(() => {
    if (!rental) return;
    loadInitial();
    if (rental.status === "active" || rental.status === "awaiting_review") {
      pollRef.current = setInterval(pollNewMessages, 5000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [rental, loadInitial, pollNewMessages]);

  // ─── Send message ─────────────────────────────────────────────────────
  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!content.trim() || sending || rental?.status !== "active") return;
    setSending(true);
    setSendError(null);

    try {
      const res = await fetch(`${API_URL}/api/v1/rentals/${id}/messages`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ content: content.trim() }),
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

  // ─── Upload file ───────────────────────────────────────────────────────
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";

    if (file.size > 10 * 1024 * 1024) {
      setSendError("File too large (max 10 MB)");
      return;
    }

    setUploading(true);
    setSendError(null);

    try {
      const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
      const formData = new FormData();
      formData.append("file", file);

      const uploadRes = await fetch(`${API_URL}/api/v1/rentals/${id}/upload`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });

      if (!uploadRes.ok) {
        const data = await uploadRes.json().catch(() => ({}));
        setSendError(data.detail ?? "Upload failed");
        return;
      }

      const uploaded = await uploadRes.json();

      const msgRes = await fetch(`${API_URL}/api/v1/rentals/${id}/messages`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          content: uploaded.filename || file.name,
          message_type: "file",
          file_url: uploaded.url,
          file_name: uploaded.filename || file.name,
        }),
      });

      if (!msgRes.ok) {
        const data = await msgRes.json().catch(() => ({}));
        setSendError(data.detail ?? "Failed to send file message");
        return;
      }

      await pollNewMessages();
    } catch {
      setSendError("Network error during upload");
    } finally {
      setUploading(false);
    }
  };

  // ─── Complete rental ──────────────────────────────────────────────────
  const handleComplete = async (rating: number, review: string) => {
    setActionLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/rentals/${id}/complete`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ rating, review: review || null }),
      });
      if (res.ok) {
        const updated = await res.json();
        setRental((prev) => prev ? { ...prev, ...updated } : updated);
        setCompleteOpen(false);
        await pollNewMessages();
      }
    } catch {
      /* ignore */
    } finally {
      setActionLoading(false);
    }
  };

  // ─── Resume rental ────────────────────────────────────────────────────
  const handleResume = async () => {
    const reason = prompt("Why does the agent need to continue? (optional)");
    if (reason === null) return; // user pressed cancel
    setActionLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/rentals/${id}/resume`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ reason: reason || null }),
      });
      if (res.ok) {
        const updated = await res.json();
        setRental((prev) => prev ? { ...prev, ...updated } : updated);
        await pollNewMessages();
      }
    } catch {
      /* ignore */
    } finally {
      setActionLoading(false);
    }
  };

  // ─── Cancel rental ────────────────────────────────────────────────────
  const handleCancel = async () => {
    if (!confirm("Are you sure you want to cancel this rental?")) return;
    setActionLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/rentals/${id}/cancel`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (res.ok) {
        const updated = await res.json();
        setRental((prev) => prev ? { ...prev, ...updated } : updated);
        await pollNewMessages();
      }
    } catch {
      /* ignore */
    } finally {
      setActionLoading(false);
    }
  };

  // ─── Loading state ────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] relative flex items-center justify-center">
        <DotGrid />
        <div className="relative z-10 space-y-3 w-full max-w-md px-6">
          <div className="h-4 bg-neutral-800/40 rounded animate-pulse w-3/4" />
          <div className="h-3 bg-neutral-800/30 rounded animate-pulse w-1/2" />
          <div className="h-32 bg-neutral-900/30 border border-neutral-800/50 rounded-xl animate-pulse mt-6" />
          <div className="h-32 bg-neutral-900/30 border border-neutral-800/50 rounded-xl animate-pulse" />
        </div>
      </div>
    );
  }

  // ─── Error state ──────────────────────────────────────────────────────
  if (error || !rental) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] relative flex flex-col items-center justify-center gap-4">
        <DotGrid />
        <div className="relative z-10 flex flex-col items-center gap-4">
          <div className="bg-neutral-900/30 border border-red-400/20 rounded-xl px-6 py-4">
            <p className="text-red-400 text-sm font-mono">{error || "Rental not found"}</p>
          </div>
          <Link href="/" className="text-neutral-500 text-[10px] font-mono uppercase tracking-[0.15em] hover:text-white transition-colors">
            Back to dashboard
          </Link>
        </div>
      </div>
    );
  }

  const isActive = rental.status === "active";
  const isAwaitingReview = rental.status === "awaiting_review";
  const canAct = isActive || isAwaitingReview;

  return (
    <div className="h-screen bg-[#0a0a0a] text-white flex flex-col relative">
      <DotGrid />

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .fade-up { animation: fadeUp 0.4s ease-out both; }
      `}</style>

      {/* ─── Header ────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 bg-[#0a0a0a]/95 border-b border-neutral-800/50 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 py-4">
          {/* Breadcrumbs */}
          <div className="flex items-center gap-2 text-[10px] font-mono mb-3">
            <Link href="/" className="text-neutral-600 hover:text-neutral-400 transition-colors">home</Link>
            <span className="text-neutral-800">/</span>
            <Link href={`/agents/${rental.agent_id}`} className="text-neutral-600 hover:text-neutral-400 transition-colors">@{rental.agent_handle}</Link>
            <span className="text-neutral-800">/</span>
            <span className="text-neutral-400">rental</span>
          </div>

          {/* Title row */}
          <div className="flex items-start justify-between gap-4 min-w-0">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-3 min-w-0">
                <h1 className="text-lg font-medium text-white font-mono truncate min-w-0">{rental.title}</h1>
                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-[10px] font-mono rounded-md ${STATUS_BADGE[rental.status] || STATUS_BADGE.active}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[rental.status] || STATUS_DOT.active}`} />
                  {STATUS_LABEL[rental.status] || rental.status}
                </span>
              </div>

              {/* Terminal-style status panel */}
              <div className="flex items-center gap-2 mt-2 min-w-0 overflow-hidden">
                <span className="text-neutral-500 text-[10px] font-mono tracking-wide truncate">{rental.agent_name}</span>
                <span className="text-neutral-800 text-[10px] shrink-0">/</span>
                <span className="text-neutral-600 text-[10px] font-mono truncate">{rental.specialization}</span>
                <span className="text-neutral-800 text-[10px] shrink-0">/</span>
                <span className="text-neutral-700 text-[10px] font-mono shrink-0">{timeAgo(rental.created_at)}</span>
              </div>
            </div>

            {/* Action buttons */}
            {canAct && (
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={handleCancel}
                  disabled={actionLoading}
                  className="bg-neutral-800/30 border border-neutral-800/50 text-neutral-500 hover:text-red-400 hover:border-red-400/30 font-mono text-sm px-3 py-1.5 rounded-lg transition-all disabled:opacity-30"
                >
                  Cancel
                </button>
                {isAwaitingReview && (
                  <button
                    onClick={handleResume}
                    disabled={actionLoading}
                    className="bg-amber-500/10 border border-amber-500/25 text-amber-400 hover:bg-amber-500/20 font-mono text-sm px-3 py-1.5 rounded-lg transition-all disabled:opacity-30"
                  >
                    Resume
                  </button>
                )}
                <button
                  onClick={() => setCompleteOpen(true)}
                  disabled={actionLoading}
                  className="bg-white text-black font-medium font-mono text-sm px-4 py-1.5 rounded-lg hover:bg-neutral-200 disabled:opacity-30 transition-all"
                >
                  {isAwaitingReview ? "Approve" : "Complete"}
                </button>
              </div>
            )}
          </div>

          {/* Awaiting review info */}
          {isAwaitingReview && rental.agent_completed_at && (
            <div className="mt-3 bg-amber-500/5 border border-amber-500/20 rounded-lg px-4 py-2.5 flex items-center gap-3">
              <span className="text-[10px] font-mono uppercase tracking-[0.15em] text-amber-400/70">Submitted</span>
              <span className="text-amber-400 font-mono text-[11px]">{timeAgo(rental.agent_completed_at)}</span>
            </div>
          )}

          {/* Completed info — terminal-style panel */}
          {rental.status === "completed" && rental.rating !== null && (
            <div className="mt-3 bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-4 py-2.5 flex items-center gap-3">
              <span className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600">Rating</span>
              <span className="text-amber-400 font-mono text-sm">
                {[1, 2, 3, 4, 5].map((s) => (s <= (rental.rating ?? 0) ? "\u2605" : "\u2606")).join("")}
              </span>
              {rental.review && (
                <>
                  <span className="text-neutral-800 text-xs">/</span>
                  <span className="text-neutral-500 text-xs font-mono truncate max-w-[200px]">{rental.review}</span>
                </>
              )}
            </div>
          )}
        </div>
      </header>

      {/* ─── Messages ──────────────────────────────────────────────────── */}
      <main ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto relative z-10">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6 space-y-4">
          {loadingOlder && (
            <div className="flex justify-center py-2">
              <span className="text-[10px] text-neutral-600 font-mono uppercase tracking-[0.15em]">Loading...</span>
            </div>
          )}
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-4 fade-up">
              <div className="w-14 h-14 rounded-xl flex items-center justify-center bg-neutral-900/30 border border-neutral-800/50">
                <span className="text-neutral-600 text-lg font-mono">_</span>
              </div>
              <p className="text-neutral-500 text-sm font-mono">No messages yet</p>
              <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-700">
                Start the conversation by describing your task
              </p>
            </div>
          ) : (
            messages.map((msg, i) => {
              const prev = messages[i - 1];
              const showDate =
                !prev ||
                new Date(msg.created_at).toDateString() !== new Date(prev.created_at).toDateString();

              // System messages
              if (msg.sender_type === "system" || msg.message_type === "system") {
                return (
                  <div key={msg.id}>
                    {showDate && <DateSeparator ts={msg.created_at} />}
                    <div className="flex justify-center py-2">
                      <span className="text-neutral-600 text-[11px] font-mono bg-neutral-900/30 border border-neutral-800/30 rounded-lg px-3 py-1">
                        {msg.content}
                      </span>
                    </div>
                  </div>
                );
              }

              const isUser = msg.sender_type === "user";

              return (
                <div key={msg.id}>
                  {showDate && <DateSeparator ts={msg.created_at} />}
                  <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
                    {/* Bubble */}
                    <div className={`max-w-[75%] ${isUser ? "items-end flex flex-col" : ""}`}>
                      <span className="text-[10px] font-mono uppercase tracking-[0.1em] text-neutral-600 mb-1">
                        {msg.sender_name}
                      </span>

                      {/* File message */}
                      {msg.message_type === "file" && msg.file_url ? (
                        <a
                          href={msg.file_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={`flex items-center gap-2.5 ${
                            isUser
                              ? "bg-neutral-800/60 border border-neutral-700/40 rounded-xl rounded-br-sm"
                              : "bg-neutral-900/30 border border-neutral-800/50 rounded-xl rounded-bl-sm"
                          } px-4 py-3 hover:border-neutral-700/60 transition-all group`}
                        >
                          <svg
                            width="16"
                            height="16"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            className="text-violet-400 group-hover:text-violet-300 shrink-0"
                          >
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                            <polyline points="14 2 14 8 20 8" />
                          </svg>
                          <span className="text-sm text-neutral-300 group-hover:text-white font-mono truncate">
                            {msg.file_name || "Download file"}
                          </span>
                        </a>
                      ) : (
                        /* Text message */
                        <div
                          className={`${
                            isUser
                              ? "bg-neutral-800/60 border border-neutral-700/40 rounded-xl rounded-br-sm"
                              : "bg-neutral-900/30 border border-neutral-800/50 rounded-xl rounded-bl-sm"
                          } px-4 py-3`}
                        >
                          <div className="text-sm text-neutral-200 leading-relaxed">
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
                        </div>
                      )}

                      <span className="text-neutral-700 text-[10px] font-mono mt-1">
                        {timeAgo(msg.created_at)}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })
          )}
          <div ref={bottomRef} />
        </div>
      </main>

      {/* ─── Awaiting review banner ──────────────────────────────────── */}
      {isAwaitingReview && (
        <div className="relative z-10 bg-amber-500/5 border-t border-amber-500/20 px-4 sm:px-6 py-2.5">
          <div className="max-w-3xl mx-auto flex items-center gap-3">
            <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse shrink-0" />
            <p className="text-amber-400 text-[11px] font-mono flex-1">
              Agent has marked this task as completed. Review the work and approve, resume, or cancel.
            </p>
          </div>
        </div>
      )}

      {/* ─── Input area ────────────────────────────────────────────────── */}
      {canAct ? (
        <form
          onSubmit={handleSend}
          className="relative z-10 bg-[#0a0a0a]/95 border-t border-neutral-800/50 backdrop-blur-sm px-4 sm:px-6 py-4"
        >
          <div className="max-w-3xl mx-auto">
            {sendError && (
              <p className="text-[11px] text-red-400 font-mono mb-2">{sendError}</p>
            )}
            <div className="flex items-end gap-3">
              {/* Attach button */}
              <input
                ref={fileInputRef}
                type="file"
                onChange={handleFileUpload}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                title="Attach file (max 10 MB)"
                className="text-neutral-600 hover:text-violet-400 transition-colors disabled:opacity-30 shrink-0 pb-3"
              >
                {uploading ? (
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="animate-spin">
                    <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" opacity="0.25" />
                    <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                ) : (
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                  </svg>
                )}
              </button>

              <textarea
                ref={textareaRef}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
                maxLength={4000}
                rows={2}
                className="flex-1 bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-4 py-3 text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-neutral-700/60 resize-none text-sm"
              />
              <button
                type="submit"
                disabled={!content.trim() || sending}
                className="bg-white text-black font-medium font-mono px-4 py-3 rounded-lg hover:bg-neutral-200 disabled:opacity-30 disabled:cursor-not-allowed transition-all text-sm shrink-0"
              >
                {sending ? "..." : "Send"}
              </button>
            </div>
          </div>
        </form>
      ) : (
        <div className="relative z-10 bg-[#0a0a0a]/95 border-t border-neutral-800/50 backdrop-blur-sm px-4 sm:px-6 py-4">
          <div className="max-w-3xl mx-auto text-center">
            <p className="text-neutral-600 text-[10px] font-mono uppercase tracking-[0.15em]">
              This rental has been {rental.status} -- messaging is disabled
            </p>
          </div>
        </div>
      )}

      {/* ─── Complete modal ────────────────────────────────────────────── */}
      <CompleteModal
        open={completeOpen}
        onClose={() => setCompleteOpen(false)}
        onSubmit={handleComplete}
        submitting={actionLoading}
      />
    </div>
  );
}

/* ─── Helper: date separator ─────────────────────────────────────────────── */

function DateSeparator({ ts }: { ts: string }) {
  return (
    <div className="flex items-center gap-3 my-4">
      <div className="flex-1 h-px bg-neutral-800/30" />
      <span className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-700">
        {new Date(ts).toLocaleDateString("en-US", {
          weekday: "short",
          month: "short",
          day: "numeric",
        })}
      </span>
      <div className="flex-1 h-px bg-neutral-800/30" />
    </div>
  );
}
