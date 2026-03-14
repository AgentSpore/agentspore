"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, Rental, RentalMessage, timeAgo } from "@/lib/api";

const STATUS_BADGE: Record<string, string> = {
  active:    "bg-emerald-400/10 text-emerald-400 border border-emerald-400/20",
  completed: "bg-neutral-700/50 text-neutral-400 border border-neutral-600/30",
  cancelled: "bg-red-400/10 text-red-400 border border-red-400/20",
};

function authHeaders(): Record<string, string> {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  return token ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
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
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div className="relative z-10 w-full max-w-md mx-4 bg-[#0a0a0a] border border-neutral-800 rounded-xl p-6 space-y-5">
        <h3 className="text-lg font-medium text-white font-mono">Complete Rental</h3>

        {/* Stars */}
        <div>
          <p className="text-sm text-neutral-400 mb-2">Rate the agent</p>
          <div className="flex gap-1">
            {[1, 2, 3, 4, 5].map((star) => (
              <button
                key={star}
                type="button"
                onClick={() => setRating(star)}
                className={`text-2xl transition-colors ${
                  star <= rating ? "text-amber-400" : "text-neutral-600 hover:text-neutral-400"
                }`}
              >
                {star <= rating ? "\u2605" : "\u2606"}
              </button>
            ))}
          </div>
        </div>

        {/* Review */}
        <div>
          <p className="text-sm text-neutral-400 mb-2">Review (optional)</p>
          <textarea
            value={review}
            onChange={(e) => setReview(e.target.value)}
            placeholder="How was your experience?"
            rows={3}
            maxLength={1000}
            className="w-full bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3 text-sm text-white placeholder-neutral-500 focus:outline-none focus:border-neutral-600 resize-none"
          />
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-neutral-500 hover:text-white text-sm font-mono transition-colors"
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

  // ─── Load messages ────────────────────────────────────────────────────
  const loadMessages = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/rentals/${id}/messages`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const data: RentalMessage[] = await res.json();
        setMessages(data);
      }
    } catch {
      /* ignore polling errors */
    }
  }, [id]);

  useEffect(() => {
    if (!rental) return;
    loadMessages();
    if (rental.status === "active") {
      pollRef.current = setInterval(loadMessages, 5000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [rental, loadMessages]);

  // ─── Auto-scroll ──────────────────────────────────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

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
      await loadMessages();
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
    // Reset input so the same file can be re-selected
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

      // Send file message
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

      await loadMessages();
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
        await loadMessages();
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
        await loadMessages();
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
      <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
        <div className="space-y-3 w-full max-w-md px-6">
          <div className="h-4 bg-neutral-800/60 rounded animate-pulse w-3/4" />
          <div className="h-3 bg-neutral-800/40 rounded animate-pulse w-1/2" />
          <div className="h-32 bg-neutral-800/30 rounded-xl animate-pulse mt-6" />
          <div className="h-32 bg-neutral-800/20 rounded-xl animate-pulse" />
        </div>
      </div>
    );
  }

  // ─── Error state ──────────────────────────────────────────────────────
  if (error || !rental) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
        <div className="text-red-400 text-sm">{error || "Rental not found"}</div>
        <Link href="/" className="text-neutral-400 text-sm hover:text-white transition-colors">
          ← Back to dashboard
        </Link>
      </div>
    );
  }

  const isActive = rental.status === "active";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex flex-col">
      {/* ─── Header ────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 bg-neutral-900/50 border-b border-neutral-800/80 backdrop-blur-sm overflow-hidden">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 py-4">
          {/* Back link */}
          <Link
            href={`/agents/${rental.agent_id}`}
            className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm flex items-center gap-1.5 mb-3"
          >
            <span>←</span> Back to @{rental.agent_handle}
          </Link>

          {/* Title row */}
          <div className="flex items-start justify-between gap-4 min-w-0">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-3 min-w-0">
                <h1 className="text-lg font-medium text-white font-mono truncate min-w-0">{rental.title}</h1>
                <span className={`inline-flex items-center px-2 py-0.5 text-[11px] font-mono rounded-md ${STATUS_BADGE[rental.status] || STATUS_BADGE.active}`}>
                  {rental.status}
                </span>
              </div>
              <div className="flex items-center gap-2 mt-1.5 min-w-0 overflow-hidden">
                <span className="text-neutral-400 text-xs font-mono truncate">{rental.agent_name}</span>
                <span className="text-neutral-700 text-xs shrink-0">·</span>
                <span className="text-neutral-500 text-xs font-mono truncate">{rental.specialization}</span>
                <span className="text-neutral-700 text-xs shrink-0">·</span>
                <span className="text-neutral-600 text-xs font-mono shrink-0">{timeAgo(rental.created_at)}</span>
              </div>
            </div>

            {/* Action buttons */}
            {isActive && (
              <div className="flex items-center gap-3 shrink-0">
                <button
                  onClick={handleCancel}
                  disabled={actionLoading}
                  className="text-neutral-500 hover:text-red-400 font-mono text-sm transition-colors disabled:opacity-30"
                >
                  Cancel
                </button>
                <button
                  onClick={() => setCompleteOpen(true)}
                  disabled={actionLoading}
                  className="bg-white text-black font-medium font-mono text-sm px-4 py-1.5 rounded-lg hover:bg-neutral-200 disabled:opacity-30 transition-all"
                >
                  Complete
                </button>
              </div>
            )}
          </div>

          {/* Completed info */}
          {rental.status === "completed" && rental.rating !== null && (
            <div className="mt-3 flex items-center gap-2 text-sm">
              <span className="text-neutral-500 font-mono text-xs">Rating:</span>
              <span className="text-amber-400 font-mono">
                {[1, 2, 3, 4, 5].map((s) => (s <= (rental.rating ?? 0) ? "\u2605" : "\u2606")).join("")}
              </span>
              {rental.review && (
                <>
                  <span className="text-neutral-700 text-xs">·</span>
                  <span className="text-neutral-400 text-xs truncate max-w-[200px]">{rental.review}</span>
                </>
              )}
            </div>
          )}
        </div>
      </header>

      {/* ─── Messages ──────────────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6 space-y-4">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <div className="w-12 h-12 rounded-xl flex items-center justify-center bg-neutral-800/60 border border-neutral-700/50">
                <span className="text-neutral-500 text-lg">...</span>
              </div>
              <p className="text-neutral-500 text-sm">No messages yet</p>
              <p className="text-neutral-600 text-xs font-mono">
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
                      <span className="text-neutral-500 text-sm font-mono italic px-4">
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
                      <span className="text-neutral-400 text-xs font-mono mb-1">
                        {msg.sender_name}
                      </span>

                      {/* File message */}
                      {msg.message_type === "file" && msg.file_url ? (
                        <a
                          href={msg.file_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={`flex items-center gap-2 ${
                            isUser
                              ? "bg-neutral-800 rounded-xl rounded-br-sm"
                              : "bg-neutral-900/80 border border-neutral-800/60 rounded-xl rounded-bl-sm"
                          } px-4 py-3 hover:bg-neutral-700/60 transition-colors group`}
                        >
                          <svg
                            width="16"
                            height="16"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            className="text-neutral-400 group-hover:text-white shrink-0"
                          >
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                            <polyline points="14 2 14 8 20 8" />
                          </svg>
                          <span className="text-sm text-neutral-300 group-hover:text-white truncate">
                            {msg.file_name || "Download file"}
                          </span>
                        </a>
                      ) : (
                        /* Text message */
                        <div
                          className={`${
                            isUser
                              ? "bg-neutral-800 rounded-xl rounded-br-sm"
                              : "bg-neutral-900/80 border border-neutral-800/60 rounded-xl rounded-bl-sm"
                          } px-4 py-3`}
                        >
                          <p className="text-sm text-neutral-200 leading-relaxed whitespace-pre-wrap">
                            {msg.content}
                          </p>
                        </div>
                      )}

                      <span className="text-neutral-600 text-xs font-mono mt-1">
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

      {/* ─── Input area ────────────────────────────────────────────────── */}
      {isActive ? (
        <form
          onSubmit={handleSend}
          className="bg-neutral-900/50 border-t border-neutral-800/80 px-4 sm:px-6 py-4"
        >
          <div className="max-w-3xl mx-auto">
            {sendError && (
              <p className="text-[11px] text-red-400 mb-2">{sendError}</p>
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
                className="text-neutral-500 hover:text-white transition-colors disabled:opacity-30 shrink-0 pb-3"
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
                className="flex-1 bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3 text-white placeholder-neutral-500 focus:outline-none focus:border-neutral-600 resize-none text-sm"
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
        <div className="bg-neutral-900/50 border-t border-neutral-800/80 px-4 sm:px-6 py-4">
          <div className="max-w-3xl mx-auto text-center">
            <p className="text-neutral-500 text-sm font-mono">
              This rental has been {rental.status}. Messaging is disabled.
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
      <div className="flex-1 h-px bg-neutral-800/60" />
      <span className="text-[10px] text-neutral-700 font-mono">
        {new Date(ts).toLocaleDateString("en-US", {
          weekday: "short",
          month: "short",
          day: "numeric",
        })}
      </span>
      <div className="flex-1 h-px bg-neutral-800/60" />
    </div>
  );
}
