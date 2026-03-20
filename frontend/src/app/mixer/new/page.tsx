"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API_URL, Agent, MixerFragmentInfo } from "@/lib/api";
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

const TTL_OPTIONS = [
  { value: 1, label: "1 hour" },
  { value: 6, label: "6 hours" },
  { value: 12, label: "12 hours" },
  { value: 24, label: "24 hours" },
  { value: 48, label: "48 hours" },
  { value: 72, label: "72 hours" },
];

const PRIVATE_RE = /\{\{PRIVATE(?::\w+)?:[^}]+\}\}/g;

interface ChunkDraft {
  key: string;
  agent_id: string;
  title: string;
  instructions: string;
}

let keyCounter = 0;
function nextKey() { return `chunk-${++keyCounter}`; }

export default function NewMixerPage() {
  const router = useRouter();

  // Step 1: session info
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [taskText, setTaskText] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [passphraseConfirm, setPassphraseConfirm] = useState("");
  const [ttl, setTtl] = useState(24);

  // Step 2: after session creation — add chunks
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [fragments, setFragments] = useState<MixerFragmentInfo[]>([]);
  const [sanitizedText, setSanitizedText] = useState("");
  const [chunks, setChunks] = useState<ChunkDraft[]>([
    { key: nextKey(), agent_id: "", title: "", instructions: "" },
  ]);

  const [agents, setAgents] = useState<Agent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/agents/leaderboard`)
      .then((r) => r.json())
      .then((d) => setAgents(Array.isArray(d) ? d : []))
      .catch(() => {});
  }, []);

  // Count private markers in real time
  const markerCount = (taskText.match(PRIVATE_RE) || []).length;

  const wrapSelection = () => {
    const ta = textareaRef.current;
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    if (start === end) return;

    const selected = taskText.substring(start, end);
    const wrapped = `{{PRIVATE:${selected}}}`;
    setTaskText(taskText.substring(0, start) + wrapped + taskText.substring(end));

    setTimeout(() => {
      ta.selectionStart = start;
      ta.selectionEnd = start + wrapped.length;
      ta.focus();
    }, 0);
  };

  // Preview: replace markers with placeholders
  const previewText = taskText.replace(PRIVATE_RE, (match) => {
    const inner = match.slice(2, -2); // remove {{ and }}
    const parts = inner.split(":");
    const placeholder = `MIX_${Math.random().toString(16).slice(2, 8)}`;
    return `{{${placeholder}}}`;
  });

  // Step 1: Create session
  const handleCreateSession = async () => {
    setError("");
    const token = localStorage.getItem("access_token");
    if (!token) { setError("Please sign in first"); return; }
    if (!title.trim()) { setError("Title is required"); return; }
    if (!taskText.trim()) { setError("Task text is required"); return; }
    if (markerCount === 0) { setError("Mark at least one piece of data as private using {{PRIVATE:value}}"); return; }
    if (passphrase.length < 8) { setError("Passphrase must be at least 8 characters"); return; }
    if (passphrase !== passphraseConfirm) { setError("Passphrases do not match"); return; }

    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/mixer`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          title: title.trim(),
          description: description.trim() || null,
          task_text: taskText,
          passphrase,
          fragment_ttl_hours: ttl,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to create session");
      }
      const data = await res.json();
      setSessionId(data.id);
      setFragments(data.placeholders || []);
      setSanitizedText(data.sanitized_text || "");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  };

  // Step 2: Add chunks and start
  const addChunk = () => {
    setChunks((prev) => [...prev, { key: nextKey(), agent_id: "", title: "", instructions: "" }]);
  };

  const removeChunk = (key: string) => {
    setChunks((prev) => prev.filter((c) => c.key !== key));
  };

  const updateChunk = (key: string, field: string, value: string) => {
    setChunks((prev) => prev.map((c) => (c.key === key ? { ...c, [field]: value } : c)));
  };

  const handleStartSession = async () => {
    setError("");
    const token = localStorage.getItem("access_token");
    if (!token || !sessionId) return;
    if (chunks.some((c) => !c.title.trim() || !c.agent_id)) {
      setError("Every chunk needs a title and an agent");
      return;
    }

    setSubmitting(true);
    try {
      // Add chunks
      for (const c of chunks) {
        const res = await fetch(`${API_URL}/api/v1/mixer/${sessionId}/chunks`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({
            agent_id: c.agent_id,
            title: c.title.trim(),
            instructions: c.instructions.trim() || null,
          }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || "Failed to add chunk");
      }

      // Start session
      const startRes = await fetch(`${API_URL}/api/v1/mixer/${sessionId}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      });
      if (!startRes.ok) throw new Error((await startRes.json()).detail || "Failed to start session");

      router.push(`/mixer/${sessionId}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
      setSubmitting(false);
    }
  };

  const saveDraft = () => {
    if (sessionId) {
      router.push(`/mixer/${sessionId}`);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <main className="relative max-w-3xl mx-auto px-6 py-12 space-y-8">
        {/* Breadcrumb */}
        <div className="text-[10px] font-mono text-neutral-600 tracking-wide fade-up">
          <Link href="/" className="hover:text-neutral-400 transition-colors">HOME</Link>
          <span className="mx-2">/</span>
          <Link href="/mixer" className="hover:text-neutral-400 transition-colors">MIXER</Link>
          <span className="mx-2">/</span>
          <span className="text-neutral-400">NEW SESSION</span>
        </div>

        {/* Page header */}
        <div className="flex items-end justify-between gap-4 fade-up" style={{ animationDelay: "0.05s" }}>
          <div>
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 block mb-2">Create</span>
            <h1 className="text-2xl font-bold tracking-tight">New Mixer Session</h1>
          </div>
          <Link
            href="/mixer"
            className="text-[11px] font-mono text-neutral-500 hover:text-neutral-300 transition-colors flex-shrink-0 px-3 py-1.5 rounded-lg border border-neutral-800/50 bg-neutral-900/30 hover:border-neutral-700/60"
          >
            &larr; Back
          </Link>
        </div>

        {!sessionId ? (
          /* ── Step 1: Task with private data ── */
          <>
            {/* Session info */}
            <div className="space-y-4 fade-up" style={{ animationDelay: "0.1s" }}>
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Session Details</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Session title"
                className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-3 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors"
                maxLength={300}
              />
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Description (optional)"
                rows={2}
                className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-3 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors resize-none"
                maxLength={2000}
              />
            </div>

            {/* Task text with private markers */}
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-6 space-y-4 fade-up" style={{ animationDelay: "0.15s" }}>
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 block mb-1">Input</span>
                  <h2 className="text-base font-semibold">Task Text</h2>
                </div>
                <button
                  onClick={wrapSelection}
                  className="text-xs px-4 py-2 rounded-lg border border-violet-500/30 text-violet-300 bg-violet-500/10 hover:bg-violet-500/20 transition-all font-mono"
                >
                  Mark as Private
                </button>
              </div>
              <p className="text-xs text-neutral-600 font-mono">
                Select text and click &quot;Mark as Private&quot; or manually wrap with{" "}
                <code className="text-violet-300 bg-violet-500/10 px-1.5 py-0.5 rounded">{"{{PRIVATE:value}}"}</code>{" "}
                or{" "}
                <code className="text-violet-300 bg-violet-500/10 px-1.5 py-0.5 rounded">{"{{PRIVATE:category:value}}"}</code>
              </p>
              <textarea
                ref={textareaRef}
                value={taskText}
                onChange={(e) => setTaskText(e.target.value)}
                placeholder="Enter your task here. Wrap sensitive data with {{PRIVATE:value}}&#10;&#10;Example: Analyze the report for {{PRIVATE:company:Acme Corp}} — their revenue was {{PRIVATE:financial:$45.2M}}"
                rows={8}
                className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-3 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors resize-none"
                maxLength={50000}
              />
              <div className="flex items-center gap-3 text-[11px] text-neutral-600 font-mono">
                <span>{taskText.length} chars</span>
                <span className="text-neutral-800">&middot;</span>
                <span className={markerCount > 0 ? "text-violet-300" : "text-neutral-600"}>
                  {markerCount} private marker{markerCount !== 1 ? "s" : ""}
                </span>
              </div>
            </div>

            {/* Preview */}
            {markerCount > 0 && (
              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 space-y-3 fade-up" style={{ animationDelay: "0.18s" }}>
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Preview (what agents will see)</span>
                <pre className="text-xs text-neutral-400 font-mono whitespace-pre-wrap break-words leading-relaxed">
                  {previewText}
                </pre>
              </div>
            )}

            {/* Passphrase */}
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-6 space-y-4 fade-up" style={{ animationDelay: "0.2s" }}>
              <div>
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 block mb-1">Security</span>
                <h2 className="text-base font-semibold">Encryption Passphrase</h2>
              </div>
              <p className="text-xs text-neutral-600 font-mono">
                Used to encrypt private data. You&apos;ll need it again to view the assembled result.
                The passphrase is never stored on the server.
              </p>
              <input
                type="password"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                placeholder="Passphrase (min 8 chars)"
                className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-3 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors"
                maxLength={128}
              />
              <input
                type="password"
                value={passphraseConfirm}
                onChange={(e) => setPassphraseConfirm(e.target.value)}
                placeholder="Confirm passphrase"
                className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-3 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors"
                maxLength={128}
              />
              {passphrase && passphraseConfirm && passphrase !== passphraseConfirm && (
                <p className="text-xs text-red-400 font-mono">Passphrases do not match</p>
              )}
            </div>

            {/* TTL */}
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-6 space-y-4 fade-up" style={{ animationDelay: "0.22s" }}>
              <div>
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 block mb-1">Expiry</span>
                <h2 className="text-base font-semibold">Fragment TTL</h2>
              </div>
              <p className="text-xs text-neutral-600 font-mono">
                Private data fragments are automatically deleted after this period.
              </p>
              <select
                value={ttl}
                onChange={(e) => setTtl(Number(e.target.value))}
                className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-3 text-sm text-white font-mono focus:outline-none focus:border-violet-500/50 transition-colors"
              >
                {TTL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {error && (
              <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-4 text-sm text-red-400 font-mono fade-up">
                {error}
              </div>
            )}

            <button
              onClick={handleCreateSession}
              disabled={submitting}
              className="w-full py-3.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed fade-up"
              style={{ animationDelay: "0.25s" }}
            >
              {submitting ? "Encrypting..." : "Create Session & Add Chunks"}
            </button>
          </>
        ) : (
          /* ── Step 2: Add chunks ── */
          <>
            {/* Success message */}
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.03] backdrop-blur-sm p-5 space-y-3 fade-up" style={{ animationDelay: "0.1s" }}>
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center flex-shrink-0">
                  <svg className="w-4 h-4 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm text-emerald-400 font-medium">
                    Session created. {fragments.length} fragment{fragments.length !== 1 ? "s" : ""} encrypted.
                  </p>
                  <p className="text-xs text-neutral-500 font-mono mt-1">
                    Now add chunks -- each chunk is a sub-task assigned to a different agent.
                  </p>
                </div>
              </div>
            </div>

            {/* Sanitized text */}
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 space-y-3 fade-up" style={{ animationDelay: "0.13s" }}>
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Sanitized Text (Placeholders)</span>
              <pre className="text-xs text-neutral-400 font-mono whitespace-pre-wrap break-words leading-relaxed">
                {sanitizedText}
              </pre>
            </div>

            {/* Fragment list */}
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 space-y-3 fade-up" style={{ animationDelay: "0.16s" }}>
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Available Placeholders</span>
              <div className="flex flex-wrap gap-2">
                {fragments.map((f) => (
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

            {/* Chunks */}
            <div className="space-y-4">
              <div className="flex items-center justify-between fade-up" style={{ animationDelay: "0.19s" }}>
                <div>
                  <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 block mb-1">Configuration</span>
                  <h2 className="text-base font-semibold">Chunks</h2>
                </div>
                <span className="text-[11px] text-neutral-600 font-mono">{chunks.length} chunk{chunks.length !== 1 ? "s" : ""}</span>
              </div>

              {chunks.map((c, idx) => (
                <div
                  key={c.key}
                  className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 space-y-4 hover:border-neutral-700/60 transition-all fade-up"
                  style={{ animationDelay: `${0.2 + idx * 0.04}s` }}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Chunk {idx + 1}</span>
                    {chunks.length > 1 && (
                      <button
                        onClick={() => removeChunk(c.key)}
                        className="text-xs font-mono text-neutral-600 hover:text-red-400 transition-colors"
                      >
                        Remove
                      </button>
                    )}
                  </div>

                  <input
                    value={c.title}
                    onChange={(e) => updateChunk(c.key, "title", e.target.value)}
                    placeholder="Chunk title"
                    className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-2.5 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors"
                    maxLength={300}
                  />

                  <select
                    value={c.agent_id}
                    onChange={(e) => updateChunk(c.key, "agent_id", e.target.value)}
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
                    value={c.instructions}
                    onChange={(e) => updateChunk(c.key, "instructions", e.target.value)}
                    placeholder={`Instructions for this chunk. Use placeholders like {{${fragments[0]?.placeholder || "MIX_xxxxxx"}}}`}
                    rows={3}
                    className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-4 py-2.5 text-sm text-white font-mono placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors resize-none"
                    maxLength={50000}
                  />
                </div>
              ))}

              <button
                onClick={addChunk}
                className="w-full py-3 rounded-xl border border-dashed border-neutral-700/60 text-sm font-mono text-neutral-500 hover:text-neutral-300 hover:border-neutral-600 bg-neutral-900/20 transition-all fade-up"
                style={{ animationDelay: `${0.2 + chunks.length * 0.04}s` }}
              >
                + Add Chunk
              </button>
            </div>

            {/* Provider diversity check */}
            {(() => {
              const providerCounts: Record<string, number> = {};
              for (const c of chunks) {
                if (!c.agent_id) continue;
                const agent = agents.find((a) => a.id === c.agent_id);
                if (agent) {
                  providerCounts[agent.model_provider] = (providerCounts[agent.model_provider] || 0) + 1;
                }
              }
              const duplicates = Object.entries(providerCounts).filter(([, count]) => count > 1);
              if (duplicates.length === 0) return null;
              return (
                <div className="rounded-xl border border-orange-500/20 bg-orange-500/[0.05] backdrop-blur-sm p-4 text-sm text-orange-300 font-mono flex items-center gap-3 fade-up">
                  <svg className="w-5 h-5 flex-shrink-0 text-orange-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
                  </svg>
                  <span>
                    Provider overlap: {duplicates.map(([p, n]) => `${p} (${n} chunks)`).join(", ")}.
                    For better privacy, use agents with different LLM providers.
                  </span>
                </div>
              );
            })()}

            {error && (
              <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-4 text-sm text-red-400 font-mono fade-up">
                {error}
              </div>
            )}

            <div className="flex gap-3 fade-up" style={{ animationDelay: "0.28s" }}>
              <button
                onClick={saveDraft}
                className="flex-1 py-3.5 rounded-lg text-sm font-mono border border-neutral-800/50 bg-neutral-900/30 text-neutral-400 hover:text-white hover:border-neutral-700/60 transition-all"
              >
                Save as Draft
              </button>
              <button
                onClick={handleStartSession}
                disabled={submitting}
                className="flex-1 py-3.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {submitting ? "Starting..." : "Start Session"}
              </button>
            </div>
          </>
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
