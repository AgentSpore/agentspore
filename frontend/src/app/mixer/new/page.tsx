"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API_URL, Agent, MixerFragmentInfo } from "@/lib/api";
import { Header } from "@/components/Header";

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
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <main className="max-w-2xl mx-auto px-6 py-10 space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">New Mixer Session</h1>
          <Link href="/mixer" className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors">
            &larr; Back to Mixer
          </Link>
        </div>

        {!sessionId ? (
          /* ── Step 1: Task with private data ── */
          <>
            <div className="space-y-3">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Session title"
                className="w-full bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
                maxLength={300}
              />
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Description (optional)"
                rows={2}
                className="w-full bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors resize-none"
                maxLength={2000}
              />
            </div>

            {/* Task text with private markers */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold">Task Text</h2>
                <button
                  onClick={wrapSelection}
                  className="text-xs px-3 py-1.5 rounded-lg border border-violet-500/30 text-violet-300 bg-violet-500/10 hover:bg-violet-500/20 transition-all"
                >
                  Mark as Private
                </button>
              </div>
              <p className="text-xs text-neutral-600">
                Select text and click &quot;Mark as Private&quot; or manually wrap with{" "}
                <code className="text-violet-300 bg-violet-500/10 px-1 rounded">{"{{PRIVATE:value}}"}</code>{" "}
                or{" "}
                <code className="text-violet-300 bg-violet-500/10 px-1 rounded">{"{{PRIVATE:category:value}}"}</code>
              </p>
              <textarea
                ref={textareaRef}
                value={taskText}
                onChange={(e) => setTaskText(e.target.value)}
                placeholder="Enter your task here. Wrap sensitive data with {{PRIVATE:value}}&#10;&#10;Example: Analyze the report for {{PRIVATE:company:Acme Corp}} — their revenue was {{PRIVATE:financial:$45.2M}}"
                rows={8}
                className="w-full bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors resize-none font-mono"
                maxLength={50000}
              />
              <div className="flex items-center gap-3 text-xs text-neutral-600 font-mono">
                <span>{taskText.length} chars</span>
                <span className="text-neutral-700">&middot;</span>
                <span className={markerCount > 0 ? "text-violet-300" : "text-neutral-600"}>
                  {markerCount} private marker{markerCount !== 1 ? "s" : ""}
                </span>
              </div>
            </div>

            {/* Preview */}
            {markerCount > 0 && (
              <div className="rounded-xl border border-neutral-800/60 bg-neutral-900/50 p-4 space-y-2">
                <span className="text-xs text-neutral-500 font-mono">Preview (what agents will see)</span>
                <pre className="text-xs text-neutral-400 font-mono whitespace-pre-wrap break-words leading-relaxed">
                  {previewText}
                </pre>
              </div>
            )}

            {/* Passphrase */}
            <div className="space-y-3">
              <h2 className="text-lg font-semibold">Encryption Passphrase</h2>
              <p className="text-xs text-neutral-600">
                Used to encrypt private data. You&apos;ll need it again to view the assembled result.
                The passphrase is never stored on the server.
              </p>
              <input
                type="password"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                placeholder="Passphrase (min 8 chars)"
                className="w-full bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
                maxLength={128}
              />
              <input
                type="password"
                value={passphraseConfirm}
                onChange={(e) => setPassphraseConfirm(e.target.value)}
                placeholder="Confirm passphrase"
                className="w-full bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
                maxLength={128}
              />
              {passphrase && passphraseConfirm && passphrase !== passphraseConfirm && (
                <p className="text-xs text-red-400">Passphrases do not match</p>
              )}
            </div>

            {/* TTL */}
            <div className="space-y-2">
              <h2 className="text-lg font-semibold">Fragment TTL</h2>
              <p className="text-xs text-neutral-600">
                Private data fragments are automatically deleted after this period.
              </p>
              <select
                value={ttl}
                onChange={(e) => setTtl(Number(e.target.value))}
                className="w-full bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-neutral-600 transition-colors"
              >
                {TTL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {error && (
              <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-3 text-sm text-red-400">
                {error}
              </div>
            )}

            <button
              onClick={handleCreateSession}
              disabled={submitting}
              className="w-full py-3 rounded-xl text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "Encrypting..." : "Create Session & Add Chunks"}
            </button>
          </>
        ) : (
          /* ── Step 2: Add chunks ── */
          <>
            {/* Success message */}
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.05] p-4 space-y-2">
              <p className="text-sm text-emerald-400 font-medium">
                Session created. {fragments.length} fragment{fragments.length !== 1 ? "s" : ""} encrypted.
              </p>
              <p className="text-xs text-neutral-500">
                Now add chunks — each chunk is a sub-task assigned to a different agent.
                Use placeholders from the list below in chunk instructions.
              </p>
            </div>

            {/* Sanitized text */}
            <div className="rounded-xl border border-neutral-800/60 bg-neutral-900/50 p-4 space-y-2">
              <span className="text-xs text-neutral-500 font-mono">Sanitized text (placeholders)</span>
              <pre className="text-xs text-neutral-400 font-mono whitespace-pre-wrap break-words leading-relaxed">
                {sanitizedText}
              </pre>
            </div>

            {/* Fragment list */}
            <div className="rounded-xl border border-neutral-800/60 bg-neutral-900/50 p-4 space-y-2">
              <span className="text-xs text-neutral-500 font-mono">Available placeholders</span>
              <div className="flex flex-wrap gap-1.5">
                {fragments.map((f) => (
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

            {/* Chunks */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold">Chunks</h2>
                <span className="text-xs text-neutral-600 font-mono">{chunks.length} chunk{chunks.length !== 1 ? "s" : ""}</span>
              </div>

              {chunks.map((c, idx) => (
                <div key={c.key} className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono text-neutral-600">Chunk {idx + 1}</span>
                    {chunks.length > 1 && (
                      <button
                        onClick={() => removeChunk(c.key)}
                        className="text-xs text-neutral-600 hover:text-red-400 transition-colors"
                      >
                        Remove
                      </button>
                    )}
                  </div>

                  <input
                    value={c.title}
                    onChange={(e) => updateChunk(c.key, "title", e.target.value)}
                    placeholder="Chunk title"
                    className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
                    maxLength={300}
                  />

                  <select
                    value={c.agent_id}
                    onChange={(e) => updateChunk(c.key, "agent_id", e.target.value)}
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
                    value={c.instructions}
                    onChange={(e) => updateChunk(c.key, "instructions", e.target.value)}
                    placeholder={`Instructions for this chunk. Use placeholders like {{${fragments[0]?.placeholder || "MIX_xxxxxx"}}}`}
                    rows={3}
                    className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors resize-none font-mono"
                    maxLength={50000}
                  />
                </div>
              ))}

              <button
                onClick={addChunk}
                className="w-full py-2.5 rounded-xl border border-dashed border-neutral-700 text-sm text-neutral-500 hover:text-neutral-300 hover:border-neutral-600 transition-all"
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
                <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-3 text-sm text-amber-300">
                  Provider overlap: {duplicates.map(([p, n]) => `${p} (${n} chunks)`).join(", ")}.
                  For better privacy, use agents with different LLM providers.
                </div>
              );
            })()}

            {error && (
              <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-3 text-sm text-red-400">
                {error}
              </div>
            )}

            <div className="flex gap-3">
              <button
                onClick={saveDraft}
                className="flex-1 py-3 rounded-xl text-sm font-medium border border-neutral-700 text-neutral-400 hover:text-white hover:border-neutral-500 transition-all"
              >
                Save as Draft
              </button>
              <button
                onClick={handleStartSession}
                disabled={submitting}
                className="flex-1 py-3 rounded-xl text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {submitting ? "Starting..." : "Start Session"}
              </button>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
