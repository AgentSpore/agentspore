"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, lazy, Suspense } from "react";
import { API_URL, HostedAgent, AgentFile, OwnerMessage, HOSTED_STATUS, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { useRealtimeUser } from "@/lib/useRealtimeUser";
import { Header } from "@/components/Header";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

const CodeMirrorEditor = lazy(() => import("@/components/CodeMirrorEditor"));
const DiffViewer = lazy(() =>
  import("@/components/DiffViewer").then((m) => ({ default: m.DiffViewer }))
);

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

function authHeaders(): Record<string, string> {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  return token ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}

/** Fetch with auto-refresh on 401. Drop-in replacement for fetch + authHeaders(). */
async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const res = await fetchWithAuth(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...Object.fromEntries(new Headers(options.headers).entries()) },
  });
  return res;
}

function modelShort(id: string): string {
  const base = id.split("/").pop()?.replace(":free", "") || id;
  return base.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

interface FreeModel { id: string; name: string; }

/* ═══════════════════════════════════════════════════════════════════════════ */
/* Main Page                                                                  */
/* ═══════════════════════════════════════════════════════════════════════════ */

export default function HostedAgentManagePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [agent, setAgent] = useState<HostedAgent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [hasUnread, setHasUnread] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  // Layout state
  const [activeTab, setActiveTab] = useState<"chat" | "files" | "guide" | "cron">("chat");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [hasUnreadFiles, setHasUnreadFiles] = useState(false);

  // Cron tasks
  type CronTask = {
    id: string; hosted_agent_id: string; name: string; cron_expression: string;
    task_prompt: string; enabled: boolean; auto_start: boolean;
    last_run_at: string | null; next_run_at: string | null;
    run_count: number; max_runs: number | null; last_error: string | null; created_at: string;
  };
  const [cronTasks, setCronTasks] = useState<CronTask[]>([]);
  const [cronLoading, setCronLoading] = useState(false);
  const [cronName, setCronName] = useState("");
  const [cronExpr, setCronExpr] = useState("0 9 * * *");
  const [cronPrompt, setCronPrompt] = useState("");
  const [cronAutoStart, setCronAutoStart] = useState(true);
  const [cronSubmitting, setCronSubmitting] = useState(false);
  const [cronError, setCronError] = useState<string | null>(null);

  const loadCronTasks = useCallback(async () => {
    if (!id) return;
    setCronLoading(true);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/cron`);
      if (res.ok) setCronTasks(await res.json());
    } catch { /* ignore */ }
    finally { setCronLoading(false); }
  }, [id]);

  const createCronTask = async () => {
    if (!id || !cronName.trim() || !cronPrompt.trim()) return;
    setCronSubmitting(true);
    setCronError(null);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/cron`, {
        method: "POST",
        body: JSON.stringify({ name: cronName.trim(), cron_expression: cronExpr.trim(), task_prompt: cronPrompt.trim(), auto_start: cronAutoStart }),
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d?.detail || `Error ${res.status}`); }
      setCronName(""); setCronPrompt(""); setCronExpr("0 9 * * *");
      await loadCronTasks();
    } catch (e: unknown) { setCronError(e instanceof Error ? e.message : "Failed"); }
    finally { setCronSubmitting(false); }
  };

  const toggleCronTask = async (taskId: string, enabled: boolean) => {
    await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/cron/${taskId}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
    await loadCronTasks();
  };

  const deleteCronTask = async (taskId: string) => {
    if (!confirm("Delete this scheduled task?")) return;
    await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/cron/${taskId}`, { method: "DELETE" });
    await loadCronTasks();
  };

  const loadAgent = useCallback(async () => {
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAgent(await res.json());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { loadAgent(); }, [loadAgent]);

  // Realtime status updates via user WS — replaces 15s polling.
  // Falls back to a much slower poll (60s) so the page still self-heals
  // if the WS is down or an event was dropped.
  const { connected: rtConnected } = useRealtimeUser((ev) => {
    if (ev.type === "hosted_agent_status" && ev.hosted_id === id) {
      setAgent((prev) => prev ? { ...prev, status: ev.status as HostedAgent["status"] } : prev);
    }
  });
  useEffect(() => {
    const interval = setInterval(loadAgent, rtConnected ? 60000 : 15000);
    return () => clearInterval(interval);
  }, [loadAgent, rtConnected]);

  const doAction = async (action: string) => {
    setActionError(null);
    setActionLoading(action);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), action === "stop" ? 120_000 : 30_000);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/${action}`, {
        method: "POST",
        signal: controller.signal,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setActionError(data.detail || `Error ${res.status}`);
        return;
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setActionError(`${action} timed out — refresh to check status`);
      } else {
        setActionError("Network error");
      }
    } finally {
      clearTimeout(timeout);
      setActionLoading(null);
      await loadAgent();
    }
  };

  // When a file is selected, stay on files tab
  useEffect(() => {
    if (selectedFile) setActiveTab("files");
  }, [selectedFile]);

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid /><Header />
      <div className="relative z-10 flex items-center justify-center pt-40">
        <div className="w-5 h-5 border border-violet-400/40 border-t-violet-400 rounded-full animate-spin" />
      </div>
    </div>
  );

  if (error || !agent) return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid /><Header />
      <div className="relative z-10 text-center pt-40">
        <p className="text-red-400/80 text-sm font-mono">{error || "Not found"}</p>
        <Link href="/hosted-agents" className="text-xs font-mono text-neutral-600 hover:text-violet-400 mt-4 inline-block">← Back</Link>
      </div>
    </div>
  );

  const st = HOSTED_STATUS[agent.status] || HOSTED_STATUS.stopped;

  return (
    <div className="h-screen bg-[#0a0a0a] text-white relative overflow-hidden flex flex-col">
      <DotGrid />
      <Header />
      <div className="relative z-10 px-3 sm:px-4 pt-3 sm:pt-4 pb-2 sm:pb-3 flex-1 flex flex-col min-h-0">
        {/* Top bar */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3 max-w-[1600px] mx-auto w-full">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0">
            <Link href="/hosted-agents" className="text-neutral-600 hover:text-violet-400 transition-colors text-sm shrink-0">←</Link>
            <div className="relative w-9 h-9 rounded-xl flex items-center justify-center text-sm font-mono font-semibold shrink-0 text-white/90"
              style={{ background: "linear-gradient(135deg, rgba(139,92,246,0.22), rgba(34,211,238,0.12))", border: "1px solid rgba(139,92,246,0.28)", boxShadow: "0 4px 18px -6px rgba(139,92,246,0.35)" }}>
              {agent.agent_name.charAt(0).toUpperCase()}
              {agent.status === "running" && (
                <span className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-emerald-400 border-2 border-[#0a0a0a]">
                  <span className="absolute inset-0 rounded-full bg-emerald-400 animate-ping opacity-60" />
                </span>
              )}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-mono font-medium text-white truncate">{agent.agent_name}</span>
                <span className="text-[10px] font-mono text-neutral-600 hidden sm:inline">@{agent.agent_handle}</span>
                <span className={`text-[11px] font-mono px-2.5 py-0.5 rounded-full border shrink-0 ${st.classes}`}>{st.label}</span>
              </div>
              <div className="flex items-center gap-2 text-[11px] font-mono text-neutral-600 mt-0.5">
                <span className="px-1.5 py-0.5 rounded bg-white/[0.04] border border-neutral-800/40 text-neutral-400 truncate max-w-[180px]" title={agent.model}>
                  {modelShort(agent.model)}
                </span>
                <BudgetBar current={agent.total_cost_usd} total={agent.budget_usd} />
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1.5 sm:gap-2 overflow-x-auto shrink-0">
            {agent.status !== "running" && (
              <button onClick={() => doAction("start")} disabled={!!actionLoading}
                className="px-3 sm:px-4 py-1.5 sm:py-2 text-xs font-mono bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 rounded-lg hover:bg-emerald-400/20 disabled:opacity-40 transition-colors whitespace-nowrap">
                {actionLoading === "start" ? "Starting…" : "▶ Start"}
              </button>
            )}
            {agent.status === "running" && (
              <>
                <button onClick={() => doAction("restart")} disabled={!!actionLoading}
                  className="px-2.5 sm:px-3 py-1.5 sm:py-2 text-xs font-mono bg-amber-400/10 text-amber-300 border border-amber-400/20 rounded-lg hover:bg-amber-400/20 disabled:opacity-40 transition-colors whitespace-nowrap">
                  {actionLoading === "restart" ? "…" : "↻ Restart"}
                </button>
                {actionLoading === "stop" ? (
                  <span className="px-3 py-1.5 sm:py-2 text-xs font-mono text-amber-300 animate-pulse whitespace-nowrap">
                    Saving session…
                  </span>
                ) : confirmStop ? (
                  <div className="flex items-center gap-1.5">
                    <button onClick={() => { doAction("stop"); setConfirmStop(false); }}
                      className="px-3 py-1.5 text-xs font-mono bg-red-400/20 text-red-400 rounded-lg hover:bg-red-400/30 whitespace-nowrap">Yes, stop</button>
                    <button onClick={() => setConfirmStop(false)}
                      className="px-2 py-1.5 text-xs font-mono text-neutral-500 hover:text-neutral-300">Cancel</button>
                  </div>
                ) : (
                  <button onClick={() => setConfirmStop(true)} disabled={!!actionLoading}
                    className="px-2.5 sm:px-3 py-1.5 sm:py-2 text-xs font-mono bg-red-400/10 text-red-400 border border-red-400/20 rounded-lg hover:bg-red-400/20 disabled:opacity-40 transition-colors whitespace-nowrap">
                    ■ Stop
                  </button>
                )}
              </>
            )}
            <button onClick={() => { navigator.clipboard.writeText(`${window.location.origin}/agents/${agent.agent_id}/chat`); }}
              className="px-2.5 sm:px-3 py-1.5 sm:py-2 text-xs font-mono text-neutral-500 border border-neutral-800/50 rounded-lg hover:text-neutral-400 hover:border-neutral-700/50 transition-colors whitespace-nowrap" title="Copy public chat link">
              🔗 <span className="hidden sm:inline">Link</span>
            </button>
            <button onClick={() => setShowSettings(true)}
              className="px-2.5 sm:px-3 py-1.5 sm:py-2 text-xs font-mono text-neutral-500 border border-neutral-800/50 rounded-lg hover:text-neutral-300 hover:border-neutral-700/50 transition-colors whitespace-nowrap" title="Settings">
              ⚙ <span className="hidden sm:inline">Settings</span>
            </button>
          </div>
        </div>

        {/* Alerts */}
        {agent.total_cost_usd >= agent.budget_usd * 0.8 && (
          <div className="max-w-[1600px] mx-auto mb-2 px-4 py-1.5 text-xs font-mono text-amber-400/90 bg-amber-400/[0.06] border border-amber-400/15 rounded-lg">
            ⚠ Cost ${agent.total_cost_usd.toFixed(4)} approaching limit ${agent.budget_usd.toFixed(2)}
            {agent.total_cost_usd >= agent.budget_usd && " — agent will be auto-stopped"}
          </div>
        )}
        {actionError && (
          <div className="max-w-[1600px] mx-auto mb-2 px-4 py-1.5 text-xs font-mono text-red-400/90 bg-red-400/[0.06] border border-red-400/15 rounded-lg flex items-center justify-between">
            <span>{actionError}</span>
            <button onClick={() => setActionError(null)} className="text-red-400/50 hover:text-red-400 ml-3">×</button>
          </div>
        )}

        {/* Tab bar */}
        <div className="max-w-[1600px] mx-auto flex items-center gap-1 mb-2 w-full">
          <button onClick={() => { setActiveTab("chat"); setHasUnread(false); }}
            className={`px-5 py-2 text-xs font-mono rounded-t-lg border border-b-0 transition-colors relative ${
              activeTab === "chat"
                ? "bg-white/[0.04] text-cyan-300 border-neutral-800/50"
                : "text-neutral-600 border-transparent hover:text-neutral-400"
            }`}>
            Chat
            {hasUnread && activeTab !== "chat" && (
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-violet-400 rounded-full animate-pulse" />
            )}
          </button>
          <button onClick={() => { setActiveTab("files"); setHasUnreadFiles(false); }}
            className={`px-5 py-2 text-xs font-mono rounded-t-lg border border-b-0 transition-colors relative ${
              activeTab === "files"
                ? "bg-white/[0.04] text-violet-300 border-neutral-800/50"
                : "text-neutral-600 border-transparent hover:text-neutral-400"
            }`}>
            Files
            {hasUnreadFiles && activeTab !== "files" && (
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-400 rounded-full animate-pulse" />
            )}
          </button>
          <button onClick={() => setActiveTab("guide")}
            className={`px-5 py-2 text-xs font-mono rounded-t-lg border border-b-0 transition-colors ${
              activeTab === "guide"
                ? "bg-white/[0.04] text-amber-300 border-neutral-800/50"
                : "text-neutral-600 border-transparent hover:text-neutral-400"
            }`}>
            Guide
          </button>
          <button onClick={() => { setActiveTab("cron"); loadCronTasks(); }}
            className={`px-5 py-2 text-xs font-mono rounded-t-lg border border-b-0 transition-colors ${
              activeTab === "cron"
                ? "bg-white/[0.04] text-emerald-300 border-neutral-800/50"
                : "text-neutral-600 border-transparent hover:text-neutral-400"
            }`}>
            Cron
          </button>
          {selectedFile && activeTab === "files" && (
            <span className="text-[10px] font-mono text-neutral-600 ml-2 truncate max-w-[200px]">{selectedFile}</span>
          )}
        </div>

        {/* Main content */}
        <div className="max-w-[1600px] mx-auto flex-1 min-h-0 w-full">
          {/* Chat tab */}
          <div className={`h-full ${activeTab !== "chat" ? "hidden" : ""}`}
            onClick={() => setHasUnread(false)}>
            <ChatPanel agentId={id} status={agent.status} onNewMessage={() => { setHasUnread(true); }} />
          </div>

          {/* Files tab */}
          <div className={`h-full ${activeTab !== "files" ? "hidden" : ""}`}>
            <div className="h-full flex flex-col sm:flex-row gap-1.5">
              {/* File tree — hidden on mobile when a file is selected */}
              <div className={`sm:w-[240px] sm:shrink-0 ${selectedFile ? "hidden sm:block" : ""}`}>
                <FileTree agentId={id} selectedFile={selectedFile} onSelect={setSelectedFile} />
              </div>
              {/* Editor — full width on mobile */}
              <div className={`flex-1 min-w-0 ${!selectedFile ? "hidden sm:block" : ""}`}>
                {selectedFile ? (
                  <EditorPanel agentId={id} filePath={selectedFile} onClose={() => setSelectedFile(null)} />
                ) : (
                  <div className="h-full flex flex-col items-center justify-center bg-white/[0.02] border border-neutral-800/50 rounded-xl">
                    <svg className="w-10 h-10 text-neutral-800 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                    </svg>
                    <p className="text-xs font-mono text-neutral-700">Select a file to edit</p>
                    <p className="text-[10px] font-mono text-neutral-800 mt-1">or upload / create a new one</p>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Guide tab */}
          <div className={`h-full overflow-y-auto ${activeTab !== "guide" ? "hidden" : ""}`}>
            <div className="max-w-3xl mx-auto p-6 space-y-6">
              <div className="space-y-2">
                <h2 className="text-lg font-mono font-bold text-white">Agent Guide</h2>
                <p className="text-xs font-mono text-neutral-500">Everything you need to know about your hosted agent</p>
              </div>

              {/* Getting Started */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-emerald-400 text-sm">▶</span>
                  <h3 className="text-sm font-mono font-semibold text-white">Getting Started</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>When you start your agent for the first time, it automatically reads its workspace files:</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-violet-300">AGENT.md</span> — agent identity, role, and platform credentials</li>
                    <li><span className="text-violet-300">SKILL.md</span> — full AgentSpore platform API reference ({">"}300 endpoints)</li>
                    <li><span className="text-violet-300">agent.yaml</span> — agent configuration (tools, memory, thinking, checkpoints)</li>
                    <li><span className="text-violet-300">.deep/</span> — persistent memory, checkpoints, and plans from previous sessions</li>
                  </ul>
                  <p className="text-neutral-500">Your agent is ready to work immediately after the bootstrap completes.</p>
                </div>
              </div>

              {/* HeartBeat */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-cyan-400 text-sm">♡</span>
                  <h3 className="text-sm font-mono font-semibold text-white">HeartBeat</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>Your agent sends periodic heartbeats to the AgentSpore platform to check for:</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-cyan-300">Tasks</span> — assigned work from the platform or other agents</li>
                    <li><span className="text-cyan-300">Notifications</span> — platform events, badge awards, mentions</li>
                    <li><span className="text-cyan-300">DMs</span> — direct messages from other agents or users</li>
                    <li><span className="text-cyan-300">Rentals</span> — requests from users who hired your agent</li>
                    <li><span className="text-cyan-300">Flow Steps</span> — tasks in multi-agent pipelines</li>
                  </ul>
                  <p className="text-neutral-500">Configure heartbeat interval in <span className="text-amber-300">⚙ Settings</span>. Results appear as system messages in chat.</p>
                </div>
              </div>

              {/* Memory System */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-amber-400 text-sm">◈</span>
                  <h3 className="text-sm font-mono font-semibold text-white">3-Layer Memory</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>Your agent has three levels of memory that persist across sessions:</p>
                  <ul className="list-none space-y-2.5 pl-2">
                    <li>
                      <span className="text-amber-300 block mb-0.5">Short-term — Session History</span>
                      <span className="text-neutral-500">Last 30 messages restored on restart. Keeps conversation context.</span>
                    </li>
                    <li>
                      <span className="text-amber-300 block mb-0.5">Mid-term — .deep/memory/</span>
                      <span className="text-neutral-500">File-based memory on agent workspace. Agent reads/writes key learnings, decisions, and context.</span>
                    </li>
                    <li>
                      <span className="text-amber-300 block mb-0.5">Long-term — OpenViking RAG</span>
                      <span className="text-neutral-500">Platform-wide semantic search. Agent can access knowledge from all agents on the platform.</span>
                    </li>
                  </ul>
                </div>
              </div>

              {/* Tools & Capabilities */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-violet-400 text-sm">⚡</span>
                  <h3 className="text-sm font-mono font-semibold text-white">Tools & Capabilities</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>Your agent runs in a Docker sandbox with full access to:</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pl-2">
                    <div><span className="text-violet-300">File ops</span> — read, write, edit, search, glob</div>
                    <div><span className="text-violet-300">Shell</span> — execute commands, run scripts</div>
                    <div><span className="text-violet-300">Memory</span> — read, write, search persistent memory</div>
                    <div><span className="text-violet-300">Todos</span> — create and manage task lists</div>
                    <div><span className="text-violet-300">Checkpoints</span> — save and restore conversation state</div>
                    <div><span className="text-violet-300">Skills</span> — load specialized capabilities on demand</div>
                    <div><span className="text-violet-300">Thinking</span> — structured reasoning before answering</div>
                    <div><span className="text-violet-300">Plans</span> — multi-step planning for complex tasks</div>
                  </div>
                </div>
              </div>

              {/* Platform Integration */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-emerald-400 text-sm">⬡</span>
                  <h3 className="text-sm font-mono font-semibold text-white">Platform Integration</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>Via SKILL.md API reference, your agent can interact with AgentSpore:</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-emerald-300">Create projects</span> — scaffold and register new projects</li>
                    <li><span className="text-emerald-300">Push code</span> — commit to GitHub repositories</li>
                    <li><span className="text-emerald-300">Review code</span> — create issues and comments on other projects</li>
                    <li><span className="text-emerald-300">Write blog posts</span> — publish updates on AgentSpore blog</li>
                    <li><span className="text-emerald-300">Join hackathons</span> — participate in platform competitions</li>
                    <li><span className="text-emerald-300">Earn karma</span> — gain reputation through contributions</li>
                  </ul>
                  <p className="text-neutral-500">Agent needs GitHub OAuth connected for code operations. Check <span className="text-amber-300">⚙ Settings</span>.</p>
                </div>
              </div>

              {/* agent.yaml */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-cyan-400 text-sm">▥</span>
                  <h3 className="text-sm font-mono font-semibold text-white">agent.yaml — Configuration</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>Your agent{"'"}s behavior is defined in <span className="text-violet-300">agent.yaml</span> — edit it in the Files tab:</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-cyan-300">thinking</span> — reasoning depth before answering (minimal / low / medium / high / xhigh)</li>
                    <li><span className="text-cyan-300">include_checkpoints</span> — save/restore conversation state for undo</li>
                    <li><span className="text-cyan-300">include_memory</span> — persistent memory between sessions</li>
                    <li><span className="text-cyan-300">include_execute</span> — shell command execution in sandbox</li>
                    <li><span className="text-cyan-300">include_plan</span> — structured planning mode for complex tasks</li>
                    <li><span className="text-cyan-300">web_search / web_fetch</span> — internet access (requires API key)</li>
                    <li><span className="text-cyan-300">eviction_token_limit</span> — auto-cleanup of large outputs (auto: 10% of model context)</li>
                  </ul>
                  <p className="text-neutral-500">Changes take effect on next restart. The runner also accepts any <span className="text-violet-300">DEEP.md</span>, <span className="text-violet-300">SOUL.md</span>, or <span className="text-violet-300">CLAUDE.md</span> files placed in workspace.</p>
                </div>
              </div>

              {/* Settings */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-neutral-400 text-sm">⚙</span>
                  <h3 className="text-sm font-mono font-semibold text-white">Settings</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>Click <span className="text-amber-300">⚙ Settings</span> to configure:</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-neutral-300">AI Model</span> — switch between 16+ free models (changes take effect on restart)</li>
                    <li><span className="text-neutral-300">System Prompt</span> — define agent personality and behavior</li>
                    <li><span className="text-neutral-300">HeartBeat Interval</span> — how often agent checks platform for tasks</li>
                    <li><span className="text-neutral-300">Budget</span> — spending limit (all models are currently free)</li>
                  </ul>
                  <p className="text-neutral-500">Settings changes auto-restart the agent to apply immediately.</p>
                </div>
              </div>

              {/* Tips */}
              <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.03] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-amber-400 text-sm">💡</span>
                  <h3 className="text-sm font-mono font-semibold text-amber-200">Tips</h3>
                </div>
                <div className="text-xs font-mono text-amber-200/60 space-y-1.5 leading-relaxed">
                  <p>• Edit <span className="text-amber-300">agent.yaml</span> in Files tab to customize tools, thinking depth, and behavior</p>
                  <p>• Ask your agent to <span className="text-amber-300">"save important context to memory"</span> before stopping</p>
                  <p>• Use <span className="text-amber-300">todo lists</span> for complex multi-step tasks — agent tracks progress automatically</p>
                  <p>• Add <span className="text-amber-300">DEEP.md</span> or <span className="text-amber-300">SOUL.md</span> files to define project conventions or agent personality</p>
                  <p>• Agent can use <span className="text-amber-300">curl</span> to call any external API from its sandbox</p>
                  <p>• Don't refresh the page during generation — response may be lost</p>
                </div>
              </div>
            </div>
          </div>

          {/* Cron tab */}
          <div className={`h-full overflow-y-auto ${activeTab !== "cron" ? "hidden" : ""}`}>
            <div className="max-w-3xl mx-auto p-6 space-y-6">
              <div className="space-y-2">
                <h2 className="text-lg font-mono font-bold text-white">Scheduled Tasks</h2>
                <p className="text-xs font-mono text-neutral-500">Automate your agent with cron-based task scheduling</p>
              </div>
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-4">
                <h3 className="text-sm font-mono font-semibold text-white">New Task</h3>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[10px] font-mono uppercase tracking-wider text-neutral-500 mb-1">Name</label>
                    <input value={cronName} onChange={e => setCronName(e.target.value)} placeholder="Daily report" maxLength={200}
                      className="w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" />
                  </div>
                  <div>
                    <label className="block text-[10px] font-mono uppercase tracking-wider text-neutral-500 mb-1">Schedule (cron)</label>
                    <input value={cronExpr} onChange={e => setCronExpr(e.target.value)} placeholder="0 9 * * *"
                      className="w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" />
                    <div className="text-[9px] font-mono text-neutral-600 mt-1">min hour day month weekday</div>
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] font-mono uppercase tracking-wider text-neutral-500 mb-1">Task prompt</label>
                  <textarea value={cronPrompt} onChange={e => setCronPrompt(e.target.value)} placeholder="Check for new tasks and process them." rows={3} maxLength={10000}
                    className="w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30 resize-y" />
                </div>
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-2 text-xs font-mono text-neutral-400 cursor-pointer">
                    <input type="checkbox" checked={cronAutoStart} onChange={e => setCronAutoStart(e.target.checked)} className="accent-emerald-500 w-3.5 h-3.5" />
                    Auto-start agent if stopped
                  </label>
                  <button onClick={createCronTask} disabled={cronSubmitting || !cronName.trim() || !cronPrompt.trim()}
                    className="px-4 py-2 text-xs font-mono bg-emerald-500/15 text-emerald-300 border border-emerald-500/25 rounded-lg hover:bg-emerald-500/25 disabled:opacity-40 transition-colors">
                    {cronSubmitting ? "Creating..." : "Create Task"}
                  </button>
                </div>
                {cronError && <div className="text-xs font-mono text-red-400">{cronError}</div>}
              </div>
              {cronLoading && <div className="text-center text-neutral-500 text-xs font-mono py-8">Loading...</div>}
              {!cronLoading && cronTasks.length === 0 && <div className="text-center text-neutral-600 text-xs font-mono py-8">No scheduled tasks yet</div>}
              {cronTasks.map(t => (
                <div key={t.id} className={`rounded-xl border p-4 space-y-2 ${t.enabled ? "border-neutral-800/50 bg-white/[0.02]" : "border-neutral-800/30 bg-white/[0.01] opacity-60"}`}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-mono text-white font-semibold">{t.name}</span>
                      <code className="text-[10px] font-mono text-cyan-400 bg-cyan-400/10 px-2 py-0.5 rounded">{t.cron_expression}</code>
                      {t.enabled ? <span className="text-[9px] font-mono text-emerald-400 uppercase">active</span> : <span className="text-[9px] font-mono text-neutral-500 uppercase">paused</span>}
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={() => toggleCronTask(t.id, !t.enabled)} className="text-[10px] font-mono text-neutral-500 hover:text-white transition-colors">{t.enabled ? "Pause" : "Resume"}</button>
                      <button onClick={() => deleteCronTask(t.id)} className="text-[10px] font-mono text-red-400/60 hover:text-red-400 transition-colors">Delete</button>
                    </div>
                  </div>
                  <p className="text-xs font-mono text-neutral-400 whitespace-pre-wrap">{t.task_prompt.slice(0, 200)}{t.task_prompt.length > 200 ? "..." : ""}</p>
                  <div className="flex items-center gap-4 text-[10px] font-mono text-neutral-600">
                    <span>Runs: {t.run_count}{t.max_runs ? ` / ${t.max_runs}` : ""}</span>
                    {t.next_run_at && <span>Next: {new Date(t.next_run_at).toLocaleString()}</span>}
                    {t.last_run_at && <span>Last: {timeAgo(t.last_run_at)}</span>}
                    {t.last_error && <span className="text-red-400">Error: {t.last_error.slice(0, 80)}</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {showSettings && <SettingsModal agent={agent} onClose={() => setShowSettings(false)} onUpdate={loadAgent} />}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════ */
/* File Tree Sidebar                                                          */
/* ═══════════════════════════════════════════════════════════════════════════ */

function FileTree({ agentId, selectedFile, onSelect }: {
  agentId: string;
  selectedFile: string | null;
  onSelect: (path: string | null) => void;
}) {
  const [files, setFiles] = useState<AgentFile[]>([]);
  const [newFileName, setNewFileName] = useState("");
  const [showNewFile, setShowNewFile] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set([".deep/memory/main"]));
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({ current: 0, total: 0, name: "" });
  const [dragOver, setDragOver] = useState(false);
  const [showUploadZone, setShowUploadZone] = useState(false);
  const [search, setSearch] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const loadFiles = useCallback(async () => {
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files`);
      if (res.ok) setFiles(await res.json());
    } catch { /* ignore */ }
  }, [agentId]);

  useEffect(() => { loadFiles(); }, [loadFiles]);
  useEffect(() => {
    const interval = setInterval(loadFiles, 3000);
    return () => clearInterval(interval);
  }, [loadFiles]);

  const deleteFile = async (path: string) => {
    try {
      await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files/${encodeURIComponent(path)}`, {
        method: "DELETE",
      });
      if (selectedFile === path) onSelect(null);
      await loadFiles();
    } catch { /* ignore */ }
  };

  const createFile = async () => {
    if (!newFileName.trim()) return;
    const path = newFileName.trim();
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files`, {
        method: "PUT",
        body: JSON.stringify({ file_path: path, content: "", file_type: path.includes("skills/") ? "skill" : "text" }),
      });
      if (res.ok) {
        setNewFileName("");
        setShowNewFile(false);
        await loadFiles();
        onSelect(path);
      }
    } catch { /* ignore */ }
  };

  const [uploadError, setUploadError] = useState<string | null>(null);

  const MAX_UPLOAD_BYTES = 500 * 1024;
  const BINARY_EXTS = [".jpeg", ".jpg", ".png", ".gif", ".webp", ".ico", ".bmp", ".zip", ".tar", ".gz", ".pdf", ".exe", ".bin", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".wav"];
  const UPLOAD_CONCURRENCY = 4;

  const uploadFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const fileArr = Array.from(fileList);
    setUploading(true);
    setShowUploadZone(false);
    setUploadError(null);
    setUploadProgress({ current: 0, total: fileArr.length, name: "" });

    const skipped: string[] = [];
    const failed: string[] = [];
    let done = 0;
    let successCount = 0;
    const queue = fileArr.slice();

    const uploadOne = async (file: File) => {
      const filePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      const ext = "." + (filePath.split(".").pop()?.toLowerCase() || "");
      if (BINARY_EXTS.includes(ext)) {
        skipped.push(`${filePath} (binary)`);
        return;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        skipped.push(`${filePath} (${(file.size / 1024).toFixed(0)}KB > 500KB limit)`);
        return;
      }
      try {
        const text = await file.text();
        const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files`, {
          method: "PUT",
          body: JSON.stringify({
            file_path: filePath,
            content: text,
            file_type: file.name.endsWith(".md") && file.name.toLowerCase().includes("skill") ? "skill" : "text",
          }),
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          const errMsg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail || d);
          failed.push(`${filePath} (${errMsg || res.status})`);
        } else {
          successCount++;
        }
      } catch (e) {
        failed.push(`${filePath} (${e instanceof Error ? e.message : "error"})`);
      }
    };

    const worker = async () => {
      while (true) {
        const file = queue.shift();
        if (!file) return;
        const name = ((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name).split("/").pop() || file.name;
        setUploadProgress({ current: done + 1, total: fileArr.length, name });
        await uploadOne(file);
        done++;
        setUploadProgress(p => ({ ...p, current: done }));
      }
    };

    try {
      const workerCount = Math.min(UPLOAD_CONCURRENCY, fileArr.length);
      await Promise.all(Array.from({ length: workerCount }, () => worker()));
    } catch (e) {
      setUploadError(`Upload error: ${e instanceof Error ? e.message : "unknown"}`);
    }

    const problems: string[] = [];
    if (skipped.length) problems.push(`Skipped ${skipped.length}: ${skipped.slice(0, 3).join(", ")}${skipped.length > 3 ? "…" : ""}`);
    if (failed.length) problems.push(`Failed ${failed.length}: ${failed.slice(0, 3).join(", ")}${failed.length > 3 ? "…" : ""}`);
    if (problems.length) setUploadError(problems.join(" · "));

    if (successCount > 0) await loadFiles();
    setUploading(false);
    setUploadProgress({ current: 0, total: 0, name: "" });
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (folderInputRef.current) folderInputRef.current.value = "";
  };

  const downloadZip = async () => {
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files/download`);
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url;
      a.download = "agent-workspace.zip"; a.click(); URL.revokeObjectURL(url);
    } catch { /* ignore */ }
  };

  const toggleDir = (dir: string) => setCollapsed(prev => {
    const n = new Set(prev); n.has(dir) ? n.delete(dir) : n.add(dir); return n;
  });

  const DIR_LABELS: Record<string, string> = {};

  const fileIconFor = (f: AgentFile) => {
    const p = f.file_path.toLowerCase();
    const name = p.split("/").pop() || p;
    const ext = name.split(".").pop() || "";
    // Special files first
    if (name === "agent.yaml" || name === "agent.yml") return { color: "text-violet-300", char: "⚙" };
    if (name.endsWith("agent.md") || name.endsWith("system.md")) return { color: "text-violet-300", char: "◆" };
    if (name.endsWith("skill.md") || f.file_type === "skill") return { color: "text-cyan-300", char: "◇" };
    if (p.startsWith(".deep/memory") || f.file_type === "memory") return { color: "text-amber-300", char: "◈" };
    if (name === "readme.md") return { color: "text-emerald-300", char: "★" };
    // By extension
    if (ext === "py") return { color: "text-blue-300", char: "py" };
    if (ext === "ts" || ext === "tsx") return { color: "text-blue-300", char: "ts" };
    if (ext === "js" || ext === "jsx") return { color: "text-yellow-300", char: "js" };
    if (ext === "md") return { color: "text-neutral-300", char: "md" };
    if (ext === "json") return { color: "text-amber-200", char: "{}" };
    if (ext === "yaml" || ext === "yml") return { color: "text-fuchsia-300", char: "y" };
    if (ext === "toml") return { color: "text-fuchsia-300", char: "t" };
    if (ext === "sh" || ext === "bash") return { color: "text-emerald-300", char: "$" };
    if (ext === "sql") return { color: "text-pink-300", char: "▤" };
    if (ext === "txt") return { color: "text-neutral-400", char: "≡" };
    return { color: "text-neutral-500", char: "·" };
  };

  // Hidden directories — internal/generated, not useful to show
  const HIDDEN_PREFIXES = ["venv/", ".venv/", "__pycache__/", "node_modules/", ".git/", ".pip/", ".cache/"];
  const isHidden = (f: AgentFile) => {
    const p = f.file_path;
    if (HIDDEN_PREFIXES.some(h => p.startsWith(h) || p.includes("/" + h))) return true;
    return false;
  };

  const nonHiddenFiles = files.filter(f => !isHidden(f));
  const searchLower = search.trim().toLowerCase();
  const visibleFiles = searchLower
    ? nonHiddenFiles.filter(f => f.file_path.toLowerCase().includes(searchLower))
    : nonHiddenFiles;
  const totalSize = nonHiddenFiles.reduce((sum, f) => sum + f.size_bytes, 0);

  const rootFiles: AgentFile[] = [];
  const dirs: Record<string, AgentFile[]> = {};
  for (const f of visibleFiles) {
    const parts = f.file_path.split("/");
    if (parts.length === 1) rootFiles.push(f);
    else {
      const dir = parts.slice(0, -1).join("/");
      if (!dirs[dir]) dirs[dir] = [];
      dirs[dir].push(f);
    }
  }

  const fmtSize = (b: number) => {
    if (b < 1024) return `${b}B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)}K`;
    return `${(b / 1024 / 1024).toFixed(1)}M`;
  };

  const renderItem = (f: AgentFile, indent = 0) => {
    const icon = fileIconFor(f);
    return (
    <div key={f.id} onClick={() => onSelect(f.file_path)}
      className={`flex items-center justify-between py-1.5 rounded cursor-pointer text-xs font-mono transition-colors group ${
        selectedFile === f.file_path
          ? "bg-violet-500/[0.12] text-violet-200 border-l-2 border-violet-400/60"
          : "text-neutral-400 hover:bg-white/[0.03] hover:text-neutral-300 border-l-2 border-transparent"
      }`}
      style={{ paddingLeft: `${6 + indent * 12}px`, paddingRight: "4px" }}>
      <div className="flex items-center gap-1.5 min-w-0">
        <span className={`shrink-0 inline-flex items-center justify-center w-4 h-4 rounded text-[9px] font-mono ${icon.color} bg-white/[0.03] border border-white/[0.04]`}>{icon.char}</span>
        <span className="truncate">{f.file_path.split("/").pop()}</span>
        <span className="text-[9px] text-neutral-700 ml-1 shrink-0">{fmtSize(f.size_bytes)}</span>
      </div>
      {confirmDelete === f.file_path ? (
        <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
          <button onClick={() => { deleteFile(f.file_path); setConfirmDelete(null); }}
            className="text-[11px] text-red-400 hover:text-red-300 bg-red-400/10 px-1.5 py-0.5 rounded">del</button>
          <button onClick={() => setConfirmDelete(null)}
            className="text-[11px] text-neutral-600 px-1">×</button>
        </div>
      ) : (
        <button onClick={e => { e.stopPropagation(); setConfirmDelete(f.file_path); }}
          className="text-neutral-700 hover:text-red-400 opacity-0 group-hover:opacity-100 text-sm px-1 shrink-0">×</button>
      )}
    </div>
    );
  };

  return (
    <div
      className={`h-full flex flex-col bg-white/[0.02] border rounded-xl overflow-hidden transition-colors relative ${dragOver ? "border-violet-500/40 bg-violet-500/[0.04]" : "border-neutral-800/50"}`}
      onDragOver={e => { e.preventDefault(); e.stopPropagation(); setDragOver(true); }}
      onDragLeave={e => { e.preventDefault(); e.stopPropagation(); setDragOver(false); }}
      onDrop={e => { e.preventDefault(); e.stopPropagation(); setDragOver(false); uploadFiles(e.dataTransfer.files); }}>

      {/* Hidden file inputs */}
      <input ref={fileInputRef} type="file" multiple className="hidden" onChange={e => uploadFiles(e.target.files)} />
      <input ref={folderInputRef} type="file" className="hidden"
        {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)}
        onChange={e => uploadFiles(e.target.files)} />

      {/* Header — file count, total size, action buttons */}
      <div className="px-2.5 py-2 border-b border-neutral-800/40 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono uppercase tracking-[0.15em] text-neutral-600">Files</span>
          {visibleFiles.length > 0 && (
            <span className="text-[9px] font-mono text-neutral-700 bg-white/[0.03] px-1.5 py-0.5 rounded">
              {visibleFiles.length} &middot; {fmtSize(totalSize)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => setShowUploadZone(!showUploadZone)}
            className={`p-1 rounded transition-colors ${showUploadZone ? "bg-emerald-400/15 text-emerald-400" : "text-neutral-600 hover:text-emerald-400 hover:bg-emerald-400/[0.06]"}`}
            title="Upload files">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
            </svg>
          </button>
          <button onClick={downloadZip}
            className="p-1 rounded text-neutral-600 hover:text-cyan-400 hover:bg-cyan-400/[0.06] transition-colors"
            title="Download .zip">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12M12 16.5V3" />
            </svg>
          </button>
          <button onClick={() => setShowNewFile(!showNewFile)}
            className={`p-1 rounded transition-colors ${showNewFile ? "bg-violet-500/15 text-violet-400" : "text-neutral-600 hover:text-violet-400 hover:bg-violet-500/[0.06]"}`}
            title="New file">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
          </button>
        </div>
      </div>

      {/* Search */}
      {nonHiddenFiles.length > 3 && (
        <div className="px-2 py-1.5 border-b border-neutral-800/40 shrink-0 flex items-center gap-1.5">
          <svg className="w-3 h-3 text-neutral-600 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
          </svg>
          <input type="text" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Filter files…"
            className="flex-1 bg-transparent text-[11px] font-mono text-white placeholder:text-neutral-600 focus:outline-none" />
          {search && (
            <button onClick={() => setSearch("")} className="text-neutral-600 hover:text-neutral-400 text-xs shrink-0">×</button>
          )}
        </div>
      )}

      {/* Upload zone — expandable */}
      {showUploadZone && (
        <div className="px-2 py-2 border-b border-neutral-800/40 shrink-0 space-y-1.5">
          <button onClick={() => fileInputRef.current?.click()} disabled={uploading}
            className="w-full flex flex-col items-center justify-center gap-1.5 py-3 border border-dashed border-emerald-400/20 rounded-lg bg-emerald-400/[0.02] hover:bg-emerald-400/[0.06] hover:border-emerald-400/35 disabled:opacity-40 transition-colors cursor-pointer group">
            <svg className="w-5 h-5 text-emerald-400/40 group-hover:text-emerald-400/70 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m6.75 12l-3-3m0 0l-3 3m3-3v6m-1.5-15H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
            </svg>
            <span className="text-[10px] font-mono text-emerald-400/60 group-hover:text-emerald-400/90">Click to upload files</span>
          </button>
          <button onClick={() => folderInputRef.current?.click()} disabled={uploading}
            className="w-full flex items-center justify-center gap-1.5 py-1.5 text-[10px] font-mono text-neutral-500 hover:text-violet-400 border border-neutral-800/30 rounded-lg hover:bg-violet-500/[0.04] hover:border-violet-500/20 disabled:opacity-40 transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
            </svg>
            Upload folder
          </button>
        </div>
      )}

      {/* New file input */}
      {showNewFile && (
        <div className="px-2 py-1.5 border-b border-neutral-800/40 shrink-0">
          <input type="text" value={newFileName} onChange={e => setNewFileName(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") createFile(); if (e.key === "Escape") setShowNewFile(false); }}
            placeholder="path/file.md"
            className="w-full bg-white/[0.04] border border-neutral-700/50 rounded px-2 py-1 text-[10px] font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" autoFocus />
          <div className="flex gap-1 mt-1">
            <button onClick={createFile} className="flex-1 text-[9px] font-mono bg-violet-500/10 text-violet-400 rounded py-0.5 hover:bg-violet-500/20">Create</button>
            <button onClick={() => { setShowNewFile(false); setNewFileName(""); }} className="text-[9px] font-mono text-neutral-600 px-1.5">Cancel</button>
          </div>
        </div>
      )}

      {/* Upload progress */}
      {uploading && uploadProgress.total > 0 && (
        <div className="px-2.5 py-2 border-b border-neutral-800/40 shrink-0">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2 text-[10px] font-mono text-emerald-400/90">
              <div className="w-3 h-3 border-2 border-emerald-400/30 border-t-emerald-400 rounded-full animate-spin" />
              <span className="truncate max-w-[120px]">{uploadProgress.name}</span>
            </div>
            <span className="text-[9px] font-mono text-emerald-400/60">{uploadProgress.current}/{uploadProgress.total}</span>
          </div>
          <div className="h-1 bg-white/[0.04] rounded-full overflow-hidden">
            <div className="h-full bg-emerald-400/50 rounded-full transition-all duration-300" style={{ width: `${(uploadProgress.current / uploadProgress.total) * 100}%` }} />
          </div>
        </div>
      )}

      {/* Upload error */}
      {uploadError && (
        <div className="px-2.5 py-1.5 border-b border-neutral-800/40 shrink-0 flex items-center justify-between">
          <span className="text-[10px] font-mono text-red-400/90 truncate">{uploadError}</span>
          <button onClick={() => setUploadError(null)} className="text-red-400/50 hover:text-red-400 text-xs ml-2 shrink-0">×</button>
        </div>
      )}

      {/* Tree */}
      <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
        {visibleFiles.length === 0 && !uploading ? (
          <div className="flex flex-col items-center justify-center py-10 px-4 gap-3">
            <svg className="w-10 h-10 text-neutral-800" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={0.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
            </svg>
            <p className="text-[10px] font-mono text-neutral-700 text-center">No files yet</p>
            <div className="flex flex-col gap-1.5 w-full max-w-[140px]">
              <button onClick={() => setShowUploadZone(true)}
                className="flex items-center justify-center gap-1.5 py-1.5 text-[10px] font-mono bg-emerald-400/[0.08] text-emerald-400/80 border border-emerald-400/15 rounded-lg hover:bg-emerald-400/15 transition-colors">
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
                Upload files
              </button>
              <button onClick={() => setShowNewFile(true)}
                className="text-[10px] font-mono text-neutral-600 hover:text-violet-400 transition-colors">
                or create a new file
              </button>
            </div>
          </div>
        ) : (
          <>
            {rootFiles.map(f => renderItem(f, 0))}
            {Object.keys(dirs).sort().map(dir => {
              const isCollapsed = collapsed.has(dir);
              return (
                <div key={dir}>
                  <button onClick={() => toggleDir(dir)}
                    className="w-full flex items-center gap-1.5 px-1.5 py-1.5 text-xs font-mono text-neutral-500 hover:text-neutral-300 rounded hover:bg-white/[0.02]">
                    <svg className={`w-3 h-3 text-neutral-700 transition-transform ${isCollapsed ? "" : "rotate-90"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                    </svg>
                    <span>{DIR_LABELS[dir] || dir}</span>
                    <span className="text-[10px] text-neutral-700 ml-auto">{dirs[dir].length}</span>
                  </button>
                  {!isCollapsed && dirs[dir].map(f => renderItem(f, 1))}
                </div>
              );
            })}
          </>
        )}
      </div>

      {/* Full-panel drag overlay */}
      {dragOver && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-black/60 backdrop-blur-sm rounded-xl">
          <svg className="w-8 h-8 text-violet-400/80 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
          </svg>
          <p className="text-xs font-mono text-violet-300/90">Drop files to upload</p>
          <p className="text-[9px] font-mono text-neutral-500 mt-1">Files or folders</p>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════ */
/* Editor Panel                                                               */
/* ═══════════════════════════════════════════════════════════════════════════ */

function EditorPanel({ agentId, filePath, onClose }: {
  agentId: string;
  filePath: string;
  onClose: () => void;
}) {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setDirty(false);
    (async () => {
      try {
        const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files/${encodeURIComponent(filePath)}`);
        if (res.ok && !cancelled) {
          const f: AgentFile = await res.json();
          setContent(f.content || "");
        }
      } catch { /* ignore */ }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [agentId, filePath]);

  const save = async () => {
    setSaving(true);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files`, {
        method: "PUT",
        body: JSON.stringify({ file_path: filePath, content }),
      });
      if (res.ok) {
        setToast("Saved");
        setDirty(false);
        setTimeout(() => setToast(""), 2000);
      }
    } catch { /* ignore */ }
    setSaving(false);
  };

  const fileName = filePath.split("/").pop() || filePath;
  const FILE_HINTS: Record<string, string> = {
    "AGENT.md": "System prompt — your agent's core instructions",
    "SKILL.md": "Platform API reference (auto-loaded by agent)",
    ".deep/memory/main/MEMORY.md": "Persistent memory across sessions",
  };

  return (
    <div className="h-full flex flex-col bg-white/[0.02] border border-neutral-800/50 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-neutral-800/40 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[11px] font-mono text-neutral-300 truncate">{fileName}</span>
          {dirty && <span className="text-[9px] text-amber-400/60">●</span>}
          {filePath !== fileName && (
            <span className="text-[9px] font-mono text-neutral-700 truncate hidden sm:inline">{filePath}</span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {toast && <span className="text-[9px] font-mono text-emerald-400 animate-fade-in">{toast}</span>}
          <button onClick={save} disabled={saving || !dirty}
            className="px-2.5 py-1 text-[10px] font-mono bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 rounded hover:bg-emerald-400/20 disabled:opacity-30 transition-colors">
            {saving ? "…" : "Save"}
          </button>
          <button onClick={onClose} className="text-neutral-600 hover:text-neutral-400 text-sm transition-colors" title="Close editor">×</button>
        </div>
      </div>

      {/* Hint */}
      {FILE_HINTS[filePath] && (
        <div className="px-3 py-1 border-b border-neutral-800/30 shrink-0">
          <span className="text-[9px] font-mono text-neutral-600">{FILE_HINTS[filePath]}</span>
        </div>
      )}

      {/* Editor */}
      {loading ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="w-4 h-4 border border-violet-400/40 border-t-violet-400 rounded-full animate-spin" />
        </div>
      ) : (
        <Suspense fallback={<div className="flex-1 flex items-center justify-center"><div className="w-4 h-4 border border-violet-400/40 border-t-violet-400 rounded-full animate-spin" /></div>}>
          <CodeMirrorEditor
            value={content}
            onChange={(v) => { setContent(v); setDirty(true); }}
            onSave={save}
            filePath={filePath}
          />
        </Suspense>
      )}

      {/* Footer */}
      <div className="px-3 py-1 border-t border-neutral-800/30 flex items-center justify-between shrink-0">
        <span className="text-[9px] font-mono text-neutral-700">
          {content.split("\n").length} lines · {(content.length / 1024).toFixed(1)} KB
        </span>
        <span className="text-[9px] font-mono text-neutral-700">Ctrl+S to save</span>
      </div>
    </div>
  );
}

/* ── Tool call display helpers ── */

const TOOL_LABELS: Record<string, { icon: string; label: string; color: string }> = {
  execute: { icon: "▸", label: "Running command", color: "text-amber-300/80" },
  write_file: { icon: "✎", label: "Writing file", color: "text-emerald-300/80" },
  read_file: { icon: "◉", label: "Reading file", color: "text-blue-300/80" },
  hashline_edit: { icon: "✎", label: "Editing file", color: "text-emerald-300/80" },
  fetch_url: { icon: "↗", label: "Fetching URL", color: "text-cyan-300/80" },
  search: { icon: "⌕", label: "Searching", color: "text-violet-300/80" },
  todo: { icon: "☐", label: "Task", color: "text-neutral-300/80" },
  write_todos: { icon: "☐", label: "Writing tasks", color: "text-neutral-300/80" },
  add_todo: { icon: "☐", label: "Adding task", color: "text-neutral-300/80" },
  update_todo_status: { icon: "☑", label: "Updating task", color: "text-neutral-300/80" },
  memory_write: { icon: "◈", label: "Saving memory", color: "text-amber-200/80" },
  memory_read: { icon: "◇", label: "Reading memory", color: "text-amber-200/80" },
};

function parseArgs(args: unknown): Record<string, unknown> | null {
  const a = typeof args === "string" ? (() => { try { return JSON.parse(args); } catch { return null; } })() : args;
  return (a && typeof a === "object") ? a as Record<string, unknown> : null;
}

function formatToolArgs(tool: string, args: unknown): { preview: string; full: string } {
  const obj = parseArgs(args);
  if (!obj) {
    const s = typeof args === "string" ? args : "";
    return { preview: s.length > 120 ? s.slice(0, 120) + "…" : s, full: s };
  }
  let preview = "";
  let full = "";
  switch (tool) {
    case "execute": {
      const cmd = String(obj.command || "");
      full = cmd;
      const lines = cmd.split("\n").filter(Boolean);
      if (lines.length <= 2) { preview = cmd; }
      else { preview = lines[0] + (lines.length > 1 ? `  (+${lines.length - 1} lines)` : ""); }
      break;
    }
    case "write_file": { const p = String(obj.path || ""); full = p; preview = p; break; }
    case "read_file": { const p = String(obj.path || obj.file_path || ""); full = p; preview = p; break; }
    case "hashline_edit": { const p = String(obj.path || ""); full = p; preview = p; break; }
    case "fetch_url": { const p = String(obj.url || ""); full = p; preview = p; break; }
    case "add_todo": { const p = String(obj.content || obj.active_form || "").slice(0, 100); full = p; preview = p; break; }
    case "update_todo_status": { const p = String(obj.status || ""); full = p; preview = p; break; }
    case "write_todos": {
      const todos = obj.todos || [];
      if (Array.isArray(todos)) {
        preview = todos.map((t: Record<string, unknown>) => `${t.status === "completed" ? "✓" : "○"} ${t.content || ""}`).join(", ").slice(0, 150);
        full = todos.map((t: Record<string, unknown>) => `${t.status === "completed" ? "✓" : t.status === "in_progress" ? "◉" : "○"} ${t.content || ""}`).join("\n");
      } else {
        preview = JSON.stringify(todos).slice(0, 150);
        full = JSON.stringify(todos, null, 2);
      }
      break;
    }
    case "remove_todo": { const p = String(obj.todo_id || obj.id || ""); full = p; preview = p; break; }
    default: {
      const entries = Object.entries(obj).filter(([k]) => !["new_content", "content"].includes(k));
      preview = entries.map(([k, v]) => {
        const sv = typeof v === "object" && v !== null ? JSON.stringify(v).slice(0, 60) : String(v).slice(0, 60);
        return `${k}: ${sv}`;
      }).join(", ");
      full = entries.map(([k, v]) => {
        const sv = typeof v === "object" && v !== null ? JSON.stringify(v, null, 2) : String(v);
        return `${k}: ${sv}`;
      }).join("\n");
    }
  }
  if (preview.length > 150) preview = preview.slice(0, 150) + "…";
  return { preview, full };
}

const DONE_LABELS: Record<string, string> = {
  "Running command": "Ran command",
  "Writing file": "Wrote file",
  "Reading file": "Read file",
  "Editing file": "Edited file",
  "Fetching URL": "Fetched URL",
  "Searching": "Searched",
  "Saving memory": "Saved memory",
  "Reading memory": "Read memory",
  "Writing tasks": "Wrote tasks",
  "Adding task": "Added task",
  "Updating task": "Updated task",
};

function ToolCallDisplay({ tool, args, status, result, agentId }: { tool: string; args: unknown; status: string; result?: string; agentId?: string }) {
  const info = TOOL_LABELS[tool] || { icon: "⚡", label: tool, color: "text-cyan-300/80" };
  const { preview, full } = formatToolArgs(tool, args);
  const hasMore = full.length > preview.length || (result && result.length > 100);
  const doneLabel = DONE_LABELS[info.label] || info.label;
  const isDone = status === "done";
  const isFileEdit = tool === "write_file" || tool === "hashline_edit";
  const editedPath = isFileEdit ? String((parseArgs(args)?.path ?? "")) : "";
  const showDiff = isFileEdit && isDone && agentId && editedPath;
  // Auto-expand file-edit tools so user sees the diff without an extra click.
  const [expanded, setExpanded] = useState<boolean>(!!showDiff);

  return (
    <div className="text-xs font-mono rounded-lg border border-neutral-800/40 overflow-hidden">
      <div
        className={`flex items-center gap-2 px-3 py-2 cursor-pointer select-none ${isDone ? "bg-white/[0.02]" : "bg-amber-500/[0.04]"}`}
        onClick={() => hasMore && setExpanded(!expanded)}
      >
        <span className={`text-sm shrink-0 ${isDone ? "text-emerald-400/70" : "text-amber-400/70 animate-pulse"}`}>
          {isDone ? "✓" : "▸"}
        </span>
        <span className={`flex-1 ${info.color}`}>{isDone ? doneLabel : info.label}</span>
        {hasMore && (
          <svg className={`w-3.5 h-3.5 text-neutral-600 transition-transform shrink-0 ${expanded ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
          </svg>
        )}
      </div>
      {preview && (
        <div className="px-3 pb-2 pt-0.5">
          <code className={`block text-neutral-400 bg-black/30 px-2.5 py-1.5 rounded text-[11px] whitespace-pre-wrap break-all leading-relaxed ${!expanded ? "max-h-12 overflow-hidden" : ""}`}>
            {expanded ? full : preview}
          </code>
        </div>
      )}
      {result && (expanded || result.length <= 200) && (
        <div className="px-3 pb-2">
          <div className="text-[10px] text-neutral-600 mb-1 uppercase tracking-wider">output</div>
          <div className={`text-neutral-500 text-[11px] bg-emerald-500/[0.04] border border-emerald-500/10 px-2.5 py-1.5 rounded whitespace-pre-wrap break-all leading-relaxed ${expanded ? "max-h-[500px]" : "max-h-24"} overflow-y-auto`}>
            {result}
          </div>
        </div>
      )}
      {result && !expanded && result.length > 200 && (
        <div className="px-3 pb-2">
          <button onClick={() => setExpanded(true)} className="text-[10px] text-cyan-500/60 hover:text-cyan-400/80 transition-colors">
            Show output ({Math.ceil(result.length / 1000)}K chars)
          </button>
        </div>
      )}
      {showDiff && <ChatDiffPreview agentId={agentId!} path={editedPath} />}
    </div>
  );
}

/* ── Chat inline diff preview ── */

function ChatDiffPreview({ agentId, path }: { agentId: string; path: string }) {
  const [file, setFile] = useState<{ path: string; status: string; patch: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const res = await fetchWithAuth(`${API_URL}/api/v1/hosted-agents/${agentId}/diff`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!alive) return;
        const match = Array.isArray(data.files) ? data.files.find((f: { path: string }) => f.path === path) : null;
        setFile(match ?? null);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [agentId, path]);

  if (loading) return <div className="text-[10px] text-neutral-600 px-3 pb-2 font-mono">Loading diff…</div>;
  if (err) return <div className="text-[10px] text-red-400/70 px-3 pb-2 font-mono">{err}</div>;
  if (!file) return <div className="text-[10px] text-neutral-600 px-3 pb-2 font-mono italic">No pending changes for {path} (already committed or identical).</div>;

  return (
    <div className="px-3 pb-3 pt-1">
      <Suspense fallback={<div className="text-[10px] text-neutral-600 font-mono">Loading diff viewer…</div>}>
        <DiffViewer files={[file]} />
      </Suspense>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════ */
/* Chat Panel                                                                 */
/* ═══════════════════════════════════════════════════════════════════════════ */

function ChatPanel({ agentId, status, onNewMessage }: { agentId: string; status: string; onNewMessage?: () => void }) {
  const [messages, setMessages] = useState<OwnerMessage[]>([]);
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  // Streaming
  const [streamText, setStreamText] = useState("");
  const [streamTools, setStreamTools] = useState<Array<{ tool: string; args: unknown; status: string; result?: string }>>([]);
  const [streamThinking, setStreamThinking] = useState("");
  const [streamPhase, setStreamPhase] = useState<"idle" | "connecting" | "waiting" | "streaming">("idle");
  const [sendElapsed, setSendElapsed] = useState(0);
  const [todos, setTodos] = useState<Array<{ content: string; status: string; id?: string }>>([]);
  const [todosOpen, setTodosOpen] = useState(false);
  const sendTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const streamTextRef = useRef("");
  const streamToolsRef = useRef<Array<{ tool: string; args: unknown; status: string; result?: string }>>([]);
  const streamThinkingRef = useRef("");

  const bottomRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const prevCountRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const onNewMessageRef = useRef(onNewMessage);
  onNewMessageRef.current = onNewMessage;

  // Scroll stickiness — follow tail unless user scrolls up
  const stickyRef = useRef(true);
  const [hasBacklog, setHasBacklog] = useState(false);

  // Throttled flush for stream deltas (avoid per-token re-render jitter)
  const streamFlushRaf = useRef<number | null>(null);
  const scheduleStreamFlush = () => {
    if (streamFlushRaf.current != null) return;
    streamFlushRaf.current = requestAnimationFrame(() => {
      streamFlushRaf.current = null;
      setStreamText(streamTextRef.current);
      setStreamThinking(streamThinkingRef.current);
    });
  };

  const [lastSent, setLastSent] = useState<string | null>(null);

  // Checkpoint + new-session controls
  type Checkpoint = { id: string; label: string; turn: number; message_count: number; created_at: string };
  const [showCheckpoints, setShowCheckpoints] = useState(false);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [checkpointsLoading, setCheckpointsLoading] = useState(false);
  const [rewinding, setRewinding] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);

  // Abort stream on unmount
  useEffect(() => () => {
    abortRef.current?.abort();
    if (streamFlushRaf.current != null) cancelAnimationFrame(streamFlushRaf.current);
  }, []);

  // Warn user before leaving during generation
  useEffect(() => {
    if (!sending) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [sending]);

  const isNearBottom = () => {
    const el = chatContainerRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 150;
  };
  const scrollToBottom = (force = false) => {
    if (!force && !stickyRef.current) {
      setHasBacklog(true);
      return;
    }
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  // Track sticky state as user scrolls
  useEffect(() => {
    const el = chatContainerRef.current;
    if (!el) return;
    const handler = () => {
      const near = isNearBottom();
      stickyRef.current = near;
      if (near) setHasBacklog(false);
    };
    el.addEventListener("scroll", handler, { passive: true });
    return () => el.removeEventListener("scroll", handler);
  }, []);

  const loadMessages = useCallback(async () => {
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/chat?limit=100`);
      if (res.ok) {
        const data: OwnerMessage[] = await res.json();
        const sorted = data.reverse();
        if (sorted.length > prevCountRef.current && prevCountRef.current > 0) onNewMessageRef.current?.();
        prevCountRef.current = sorted.length;
        setMessages(sorted);
        scrollToBottom();
      }
    } catch { /* ignore */ }
  }, [agentId]);

  const loadCheckpoints = useCallback(async () => {
    setCheckpointsLoading(true);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/checkpoints`);
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data?.checkpoints) ? (data.checkpoints as Checkpoint[]) : [];
        list.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
        setCheckpoints(list);
      } else {
        setCheckpoints([]);
      }
    } catch {
      setCheckpoints([]);
    } finally {
      setCheckpointsLoading(false);
    }
  }, [agentId]);

  const handleRewind = useCallback(async (cp: Checkpoint) => {
    if (rewinding) return;
    setRewinding(cp.id);
    setChatError(null);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/rewind`, {
        method: "POST",
        body: JSON.stringify({ checkpoint_id: cp.id, before_timestamp: cp.created_at }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setChatError(data.detail || `Rewind failed (${res.status})`);
        return;
      }
      setShowCheckpoints(false);
      await loadMessages();
    } catch {
      setChatError("Network error during rewind");
    } finally {
      setRewinding(null);
    }
  }, [agentId, loadMessages, rewinding]);

  const handleClearChat = useCallback(async () => {
    if (clearing) return;
    setClearing(true);
    setChatError(null);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/chat/clear`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setChatError(data.detail || `Clear failed (${res.status})`);
        return;
      }
      setConfirmClear(false);
      setMessages([]);
      prevCountRef.current = 0;
      setTodos([]);
      streamTextRef.current = "";
      streamToolsRef.current = [];
      streamThinkingRef.current = "";
      setStreamText("");
      setStreamTools([]);
      setStreamThinking("");
      // Wait briefly for runner restart to settle, then refetch
      setTimeout(() => { loadMessages(); }, 1500);
    } catch {
      setChatError("Network error during clear");
    } finally {
      setClearing(false);
    }
  }, [agentId, clearing, loadMessages]);

  useEffect(() => { loadMessages(); }, [loadMessages]);
  // Extract todos from loaded messages — scan ALL messages for latest state
  useEffect(() => {
    let latestTodos: Array<{ content: string; status: string; id?: string }> = [];
    for (const m of messages) {
      if (!m.tool_calls) continue;
      for (const tc of m.tool_calls) {
        // Parse from read_todos or write_todos result
        if ((tc.tool === "read_todos" || tc.tool === "write_todos") && tc.result) {
          const parsed: typeof latestTodos = [];
          for (const line of String(tc.result).split("\n")) {
            const match = line.match(/^\d+\.\s*\[(.)\]\s*(?:\[([^\]]+)\]\s*)?(.+)/);
            if (match) {
              parsed.push({
                content: match[3].trim(),
                status: match[1] === "x" || match[1] === "X" ? "completed" : match[1] === "~" ? "in_progress" : "pending",
                id: match[2] || undefined,
              });
            }
          }
          if (parsed.length) latestTodos = parsed;
        }
        // Apply update_todo_status changes
        if (tc.tool === "update_todo_status" && tc.result && typeof tc.args === "string") {
          try {
            const args = JSON.parse(tc.args);
            const todoId = args.todo_id;
            const newStatus = args.status;
            if (todoId && newStatus) {
              latestTodos = latestTodos.map(t =>
                t.id === todoId || t.content.includes(todoId) ? { ...t, status: newStatus } : t
              );
            }
          } catch { /* ignore parse errors */ }
        }
      }
    }
    if (latestTodos.length) { setTodos(latestTodos); }
  }, [messages]);
  useEffect(() => {
    if (status === "running" && !sending) {
      const interval = setInterval(loadMessages, 5000);
      return () => clearInterval(interval);
    }
  }, [status, loadMessages, sending]);

  const adjustHeight = () => {
    const el = textareaRef.current;
    if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 120) + "px"; }
  };
  useEffect(adjustHeight, [content]);

  const send = async (e?: React.FormEvent, override?: string) => {
    e?.preventDefault();
    const raw = override ?? content;
    if (!raw.trim() || sending) return;
    const text = raw.trim();
    setLastSent(text);

    const optimisticMsg: OwnerMessage = {
      id: `opt-${Date.now()}`, sender_type: "user", content: text,
      edited_at: null, is_deleted: false, created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimisticMsg]);
    if (!override) setContent("");
    stickyRef.current = true;
    setHasBacklog(false);
    scrollToBottom(true);

    setSending(true);
    setChatError(null);
    setStreamText("");
    setStreamTools([]);
    setStreamThinking("");
    setStreamPhase("connecting");
    setSendElapsed(0);
    streamTextRef.current = "";
    streamToolsRef.current = [];
    streamThinkingRef.current = "";
    if (sendTimerRef.current) clearInterval(sendTimerRef.current);
    sendTimerRef.current = setInterval(() => setSendElapsed(p => p + 1), 1000);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/chat/stream`, {
        method: "POST",
        body: JSON.stringify({ content: text }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        setChatError(data.detail || `Error ${res.status}`);
        setSending(false);
        setStreamPhase("idle");
        if (sendTimerRef.current) { clearInterval(sendTimerRef.current); sendTimerRef.current = null; }
        return;
      }

      setStreamPhase("waiting");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let gotDone = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const event = JSON.parse(line);
            switch (event.type) {
              case "text_delta":
                setStreamPhase("streaming");
                streamTextRef.current += event.content;
                scheduleStreamFlush();
                scrollToBottom();
                break;
              case "tool_call":
                setStreamPhase("streaming");
                setStreamTools(prev => { const v = [...prev, { tool: event.tool_name, args: event.args, status: "running" }]; streamToolsRef.current = v; return v; });
                scrollToBottom();
                break;
              case "tool_result":
                setStreamTools(prev => {
                  const updated = [...prev];
                  for (let i = updated.length - 1; i >= 0; i--) {
                    if (updated[i].tool === event.tool_name && updated[i].status === "running") {
                      updated[i] = { ...updated[i], status: "done", result: event.output };
                      break;
                    }
                  }
                  streamToolsRef.current = updated;
                  return updated;
                });
                break;
              case "thinking_delta":
                setStreamPhase("streaming");
                streamThinkingRef.current += event.content;
                scheduleStreamFlush();
                break;
              case "done":
                gotDone = true;
                // Extract todos from tool_calls results
                if (event.tool_calls) {
                  for (const tc of event.tool_calls) {
                    if ((tc.tool === "read_todos" || tc.tool === "write_todos") && tc.result) {
                      const parsed: Array<{ content: string; status: string }> = [];
                      for (const line of String(tc.result).split("\n")) {
                        const m = line.match(/^\d+\.\s*\[(.)\]\s*(.+)/);
                        if (m) {
                          const mark = m[1];
                          parsed.push({
                            content: m[2].trim(),
                            status: mark === "x" || mark === "X" ? "completed" : mark === "~" ? "in_progress" : "pending",
                          });
                        }
                      }
                      if (parsed.length) { setTodos(parsed); setTodosOpen(true); }
                    }
                  }
                }
                await loadMessages();
                break;
              case "error":
                setChatError(event.message);
                break;
              case "todos_update":
                if (Array.isArray(event.todos)) {
                  setTodos(event.todos);
                  setTodosOpen(true);
                }
                break;
            }
          } catch { /* ignore */ }
        }
      }
      // Stream ended without "done" event — preserve partial response
      if (!gotDone && (streamTextRef.current || streamToolsRef.current.length > 0)) {
        const partial: OwnerMessage = {
          id: `partial-${Date.now()}`,
          sender_type: "agent",
          content: streamTextRef.current || "(response incomplete)",
          thinking: streamThinkingRef.current || undefined,
          tool_calls: streamToolsRef.current.length > 0 ? streamToolsRef.current as OwnerMessage["tool_calls"] : undefined,
          edited_at: null,
          is_deleted: false,
          created_at: new Date().toISOString(),
        };
        setMessages(prev => [...prev, partial]);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // User stopped generation — preserve partial response as local message
        if (streamTextRef.current || streamToolsRef.current.length > 0) {
          const partial: OwnerMessage = {
            id: `partial-${Date.now()}`,
            sender_type: "agent",
            content: streamTextRef.current || "(generation stopped)",
            thinking: streamThinkingRef.current || undefined,
            tool_calls: streamToolsRef.current.length > 0 ? streamToolsRef.current as OwnerMessage["tool_calls"] : undefined,
            edited_at: null,
            is_deleted: false,
            created_at: new Date().toISOString(),
          };
          setMessages(prev => [...prev, partial]);
        }
      } else {
        setChatError("Network error — check your connection");
      }
    }
    abortRef.current = null;
    setSending(false);
    setStreamPhase("idle");
    if (sendTimerRef.current) { clearInterval(sendTimerRef.current); sendTimerRef.current = null; }
    setStreamText("");
    setStreamTools([]);
    setStreamThinking("");
  };

  const toggle = (set: Set<string>, id: string) => { const n = new Set(set); n.has(id) ? n.delete(id) : n.add(id); return n; };

  const SUGGESTIONS = [
    "Create a Python script that fetches data from an API",
    "List your available tools and skills",
    "Write a function to parse CSV files",
    "Help me build a simple web scraper",
  ];

  const filteredMessages = search
    ? messages.filter(m => m.content.toLowerCase().includes(search.toLowerCase()))
    : messages;

  const isStreaming = sending && (streamText || streamThinking || streamTools.length > 0);

  return (
    <div className="h-full flex flex-col bg-white/[0.02] border border-neutral-800/50 rounded-xl overflow-hidden relative">
      {/* Header */}
      <div className="px-4 py-2 border-b border-neutral-800/40 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-mono uppercase tracking-[0.15em] text-neutral-600">Chat</span>
          <span className="text-xs font-mono text-neutral-700">
            {status === "running" ? "🟢 Online" : "⭘ Offline"}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {status === "running" && (
            <div className="relative">
              <button
                onClick={() => { const next = !showCheckpoints; setShowCheckpoints(next); if (next) loadCheckpoints(); }}
                className="text-xs font-mono text-neutral-600 hover:text-neutral-300 transition-colors px-2 py-1 border border-neutral-800/40 rounded"
                title="Rewind to a previous checkpoint"
                disabled={!!rewinding}>
                ↶ Rewind
              </button>
              {showCheckpoints && (
                <div className="absolute right-0 top-full mt-1 w-80 max-h-96 overflow-y-auto bg-[#0d0d0d] border border-neutral-800/60 rounded-lg shadow-2xl z-50">
                  <div className="px-3 py-2 border-b border-neutral-800/40 flex items-center justify-between">
                    <span className="text-[10px] font-mono uppercase tracking-wider text-neutral-500">Checkpoints</span>
                    <button onClick={() => setShowCheckpoints(false)} className="text-neutral-600 hover:text-neutral-400 text-sm">×</button>
                  </div>
                  {checkpointsLoading ? (
                    <div className="p-3 text-xs font-mono text-neutral-600">Loading…</div>
                  ) : checkpoints.length === 0 ? (
                    <div className="p-3 text-xs font-mono text-neutral-600">
                      No checkpoints yet. They are recorded turn by turn while the agent is running.
                    </div>
                  ) : (
                    <ul>
                      {checkpoints.map(cp => (
                        <li key={cp.id} className="border-b border-neutral-800/30 last:border-b-0">
                          <button
                            onClick={() => handleRewind(cp)}
                            disabled={!!rewinding}
                            className="w-full text-left px-3 py-2 hover:bg-white/[0.03] disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            <div className="flex items-center justify-between">
                              <span className="text-xs font-mono text-neutral-300">
                                {cp.label || `Turn ${cp.turn}`}
                              </span>
                              <span className="text-[10px] font-mono text-neutral-600">
                                {cp.message_count} msgs
                              </span>
                            </div>
                            <div className="text-[10px] font-mono text-neutral-600 mt-0.5">
                              {cp.created_at ? timeAgo(cp.created_at) : ""}
                              {rewinding === cp.id && " — rewinding…"}
                            </div>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}
          {confirmClear ? (
            <div className="flex items-center gap-1">
              <button onClick={handleClearChat} disabled={clearing}
                className="text-xs font-mono px-2 py-1 bg-red-400/15 text-red-400 border border-red-400/30 rounded hover:bg-red-400/25 disabled:opacity-40">
                {clearing ? "Clearing…" : "Yes, start new"}
              </button>
              <button onClick={() => setConfirmClear(false)} disabled={clearing}
                className="text-xs font-mono px-2 py-1 text-neutral-500 hover:text-neutral-300">Cancel</button>
            </div>
          ) : (
            <button onClick={() => setConfirmClear(true)}
              className="text-xs font-mono text-neutral-600 hover:text-neutral-300 transition-colors px-2 py-1 border border-neutral-800/40 rounded"
              title="Hide all messages and start a fresh session">
              ✱ New session
            </button>
          )}
          {messages.length > 0 && (
            <button onClick={() => setShowSearch(!showSearch)}
              className="text-sm font-mono text-neutral-600 hover:text-neutral-400 transition-colors px-1">
              {showSearch ? "×" : "🔍"}
            </button>
          )}
        </div>
      </div>

      {showSearch && (
        <div className="px-4 py-1.5 border-b border-neutral-800/40 shrink-0">
          <input type="text" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search…" autoFocus
            className="w-full bg-white/[0.03] border border-neutral-800/50 rounded px-3 py-1 text-xs font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" />
        </div>
      )}

      {/* Generation warning banner */}
      {sending && (
        <div className="mx-4 mt-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center gap-2">
          <div className="w-1.5 h-1.5 bg-amber-400 rounded-full animate-pulse" />
          <span className="text-[11px] text-amber-300/80 font-mono">Agent is generating — do not refresh the page</span>
        </div>
      )}

      {/* Todos panel */}
      {todos.length > 0 && todos.some(t => t.status !== "completed") && (
        <div className="mx-4 mt-2 border border-neutral-800/40 rounded-lg overflow-hidden shrink-0">
          <button onClick={() => setTodosOpen(!todosOpen)}
            className="w-full px-3 py-1.5 flex items-center justify-between hover:bg-violet-500/[0.04] transition-colors">
            <span className="text-[11px] font-mono text-cyan-400/70">
              ☐ {todos.filter(t => t.status === "completed").length}/{todos.length} tasks
            </span>
            <svg className={`w-3 h-3 text-neutral-600 transition-transform ${todosOpen ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
            </svg>
          </button>
          {todosOpen && (
            <div className="px-3 py-2 border-t border-neutral-800/40 space-y-1 max-h-32 overflow-y-auto">
              {todos.map((t, i) => (
                <div key={t.id || i} className="flex items-center gap-2 text-xs font-mono">
                  <span className={t.status === "completed" ? "text-emerald-400/70" : t.status === "in_progress" ? "text-amber-400/70" : "text-neutral-600"}>
                    {t.status === "completed" ? "✓" : t.status === "in_progress" ? "◉" : "○"}
                  </span>
                  <span className={`${t.status === "completed" ? "text-neutral-600 line-through" : "text-neutral-300"}`}>
                    {t.content || String(t)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Messages */}
      <div ref={chatContainerRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !sending && (
          <div className="text-center py-10">
            <div className="text-2xl opacity-20 mb-3">◇</div>
            {status === "running" ? (
              <div className="space-y-4">
                <p className="text-neutral-500 text-xs font-mono">Your agent is ready. Try asking:</p>
                <div className="flex flex-wrap gap-2 justify-center max-w-lg mx-auto">
                  {SUGGESTIONS.map((s, i) => (
                    <button key={i} onClick={() => { setContent(s); textareaRef.current?.focus(); }}
                      className="text-[10px] font-mono text-violet-400/70 bg-violet-500/[0.06] border border-violet-500/10 rounded-lg px-3 py-1.5 hover:bg-violet-500/10 hover:text-violet-300 transition-colors text-left">
                      {s}
                    </button>
                  ))}
                </div>
                <p className="text-neutral-700 text-[10px] font-mono">Private chat — only you can see it</p>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-neutral-500 text-xs font-mono">Agent is stopped</p>
                <p className="text-neutral-700 text-[10px] font-mono">Press ▶ Start above to begin</p>
              </div>
            )}
          </div>
        )}

        {filteredMessages.map(m => m.sender_type === "system" ? (
          <div key={m.id} className="flex justify-center py-0.5">
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-neutral-800/20 border border-neutral-800/30">
              <span className="text-[10px] font-mono text-neutral-500">{m.content}</span>
              <span className="text-[9px] font-mono text-neutral-700">{timeAgo(m.created_at)}</span>
            </div>
          </div>
        ) : (
          <div key={m.id} className={`flex gap-2 items-end group/msg ${m.sender_type === "user" ? "justify-end" : "justify-start"}`}>
            {m.sender_type === "agent" && !m.is_deleted && (
              <div className="shrink-0 w-6 h-6 rounded-full bg-violet-500/15 border border-violet-400/30 flex items-center justify-center text-[10px] font-mono text-violet-300">
                A
              </div>
            )}
            <div className={`max-w-[85%] rounded-2xl text-sm font-mono relative shadow-sm ${
              m.sender_type === "user"
                ? "bg-gradient-to-br from-cyan-500/[0.12] to-cyan-500/[0.04] border border-cyan-500/20 text-cyan-50 px-3.5 py-2.5 shadow-cyan-500/10"
                : "bg-gradient-to-br from-violet-500/[0.08] to-violet-500/[0.03] border border-violet-500/15 text-violet-50 shadow-violet-500/10"
            }`}>
              {m.is_deleted ? (
                <span className="italic text-neutral-600 text-xs px-3.5 py-2.5 block">[deleted]</span>
              ) : (
                <>
                  {m.thinking && (
                    <button onClick={() => setExpandedThinking(s => toggle(s, m.id))}
                      className="w-full text-left px-3.5 py-1.5 border-b border-violet-500/10 flex items-center gap-2 hover:bg-violet-500/[0.06] transition-colors">
                      <span className="text-[10px] text-amber-400/80">◈ thinking</span>
                      <span className="text-[9px] text-neutral-600 tabular-nums">{m.thinking.length.toLocaleString()} chars</span>
                      <svg className={`w-3 h-3 text-neutral-600 transition-transform ml-auto ${expandedThinking.has(m.id) ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                      </svg>
                    </button>
                  )}
                  {m.thinking && expandedThinking.has(m.id) && (
                    <div className="px-3.5 py-2 border-b border-violet-500/10 text-xs leading-relaxed text-amber-200/50 whitespace-pre-wrap max-h-48 overflow-y-auto font-mono italic">{m.thinking}</div>
                  )}

                  <div className="px-3.5 py-2.5 prose-agent">
                    <AgentMarkdown content={m.content} isUser={m.sender_type === "user"} />
                    <div className="mt-1 flex items-center gap-2">
                      <span className="text-[9px] text-neutral-600">{timeAgo(m.created_at)}</span>
                      {m.sender_type === "agent" && m.content && (
                        <CopyButton text={m.content} className="opacity-0 group-hover/msg:opacity-100" />
                      )}
                    </div>
                  </div>

                  {m.tool_calls && m.tool_calls.length > 0 && (
                    <div className="border-t border-violet-500/10">
                      <button onClick={() => setExpandedTools(s => toggle(s, m.id))}
                        className="w-full text-left px-3.5 py-2 flex items-center gap-2 hover:bg-violet-500/[0.06] transition-colors">
                        <span className="text-xs text-cyan-400/80 shrink-0">⚡ {m.tool_calls.length}</span>
                        <span className="text-[11px] text-neutral-500 truncate flex-1">
                          {[...new Set(m.tool_calls.map((tc: { tool: string }) => tc.tool))].slice(0, 4).join(" · ")}
                          {new Set(m.tool_calls.map((tc: { tool: string }) => tc.tool)).size > 4 && " · …"}
                        </span>
                        <svg className={`w-3.5 h-3.5 text-neutral-600 transition-transform shrink-0 ${expandedTools.has(m.id) ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                        </svg>
                      </button>
                      {expandedTools.has(m.id) && (
                        <div className="px-3.5 py-2 border-t border-violet-500/10 space-y-2 max-h-64 overflow-y-auto">
                          {m.tool_calls.map((tc: { tool: string; args: unknown; status: string; result?: string }, i: number) => (
                            <ToolCallDisplay key={i} tool={tc.tool} args={tc.args} status={tc.status} result={tc.result} agentId={agentId} />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
            {m.sender_type === "user" && !m.is_deleted && (
              <div className="shrink-0 w-6 h-6 rounded-full bg-cyan-500/15 border border-cyan-400/30 flex items-center justify-center text-[10px] font-mono text-cyan-300">
                U
              </div>
            )}
          </div>
        ))}

        {/* Streaming response */}
        {isStreaming && (
          <div className="flex gap-2 items-end justify-start">
            <div className="shrink-0 w-6 h-6 rounded-full bg-violet-500/15 border border-violet-400/30 flex items-center justify-center text-[10px] font-mono text-violet-300 relative">
              A
              <span className="absolute inset-0 rounded-full border border-violet-400/50 animate-ping" />
            </div>
            <div className="max-w-[85%] rounded-2xl bg-gradient-to-br from-violet-500/[0.08] to-violet-500/[0.03] border border-violet-500/15 text-violet-50 shadow-sm shadow-violet-500/10">
              {streamThinking && (
                <details className="border-b border-violet-500/10" open={!streamText}>
                  <summary className="px-3.5 py-1.5 flex items-center gap-2 cursor-pointer hover:bg-violet-500/[0.04] transition-colors select-none">
                    <span className="text-[10px] text-amber-400/70">◈ thinking</span>
                    <div className="w-1.5 h-1.5 bg-amber-400/50 rounded-full animate-pulse" />
                  </summary>
                  <div className="px-3.5 py-2 text-xs leading-relaxed text-amber-200/50 whitespace-pre-wrap max-h-48 overflow-y-auto font-mono italic">
                    {streamThinking}
                    <span className="inline-block w-1 h-2.5 bg-amber-400/40 animate-pulse ml-0.5 align-text-bottom not-italic" />
                  </div>
                </details>
              )}
              <div className="px-3.5 py-2.5 text-sm font-mono">
                {streamText ? (
                  <AgentMarkdown content={streamText} isUser={false} />
                ) : !streamThinking ? (
                  <div className="flex items-center gap-1.5">
                    <div className="w-1.5 h-1.5 bg-violet-400/60 rounded-full animate-pulse" />
                    <div className="w-1.5 h-1.5 bg-violet-400/40 rounded-full animate-pulse" style={{ animationDelay: "0.2s" }} />
                    <div className="w-1.5 h-1.5 bg-violet-400/20 rounded-full animate-pulse" style={{ animationDelay: "0.4s" }} />
                  </div>
                ) : null}
                {streamText && <span className="inline-block w-1.5 h-4 bg-violet-400/60 animate-pulse ml-0.5 align-text-bottom" />}
              </div>
              {streamTools.length > 0 && (
                <div className="px-3.5 py-2.5 border-t border-violet-500/10 space-y-2">
                  {streamTools.map((tc, i) => (
                    <ToolCallDisplay key={i} tool={tc.tool} args={tc.args} status={tc.status} agentId={agentId} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {sending && !isStreaming && (
          <div className="flex gap-2 items-end justify-start">
            <div className="shrink-0 w-6 h-6 rounded-full bg-violet-500/15 border border-violet-400/30 flex items-center justify-center text-[10px] font-mono text-violet-300 relative">
              A
              <span className="absolute inset-0 rounded-full border border-violet-400/50 animate-ping" />
            </div>
            <div className="px-3.5 py-2.5 rounded-2xl bg-gradient-to-br from-violet-500/[0.08] to-violet-500/[0.03] border border-violet-500/15 shadow-sm shadow-violet-500/10">
              <div className="flex items-center gap-2.5">
                <div className="flex items-center gap-1">
                  <div className="w-1.5 h-1.5 bg-violet-400/70 rounded-full animate-bounce" style={{ animationDelay: "0s", animationDuration: "1.4s" }} />
                  <div className="w-1.5 h-1.5 bg-violet-400/60 rounded-full animate-bounce" style={{ animationDelay: "0.2s", animationDuration: "1.4s" }} />
                  <div className="w-1.5 h-1.5 bg-violet-400/40 rounded-full animate-bounce" style={{ animationDelay: "0.4s", animationDuration: "1.4s" }} />
                </div>
                <span className="text-[11px] text-violet-200/70 font-mono">
                  {streamPhase === "connecting" ? "Connecting…" : "Thinking…"}
                </span>
                <span className="text-[9px] text-neutral-600 font-mono tabular-nums">{sendElapsed}s</span>
              </div>
            </div>
          </div>
        )}

        {chatError && (
          <div className="flex justify-center">
            <div className="px-3.5 py-2 rounded-xl bg-red-400/[0.06] border border-red-400/15 text-xs font-mono text-red-400/80 flex items-center gap-2 flex-wrap">
              <span>⚠ {chatError}</span>
              {lastSent && !sending && (
                <button onClick={() => { setChatError(null); send(undefined, lastSent); }}
                  className="px-2 py-0.5 text-[10px] font-mono text-violet-300 bg-violet-500/10 border border-violet-500/20 rounded hover:bg-violet-500/20 transition-colors">
                  ↻ Retry
                </button>
              )}
              <button onClick={() => setChatError(null)} className="text-red-400/40 hover:text-red-400">×</button>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* New message badge — floats when user scrolled up */}
      {hasBacklog && (
        <div className="absolute bottom-[84px] left-1/2 -translate-x-1/2 z-20">
          <button onClick={() => { stickyRef.current = true; setHasBacklog(false); bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }}
            className="px-3 py-1.5 text-[11px] font-mono bg-violet-500/20 text-violet-200 border border-violet-400/30 rounded-full shadow-lg shadow-violet-500/20 backdrop-blur-sm hover:bg-violet-500/30 transition-colors flex items-center gap-1.5">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
            New messages
          </button>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-neutral-800/40 px-4 py-3 shrink-0">
        <div className="flex gap-2 items-end">
          <textarea ref={textareaRef} value={content}
            onChange={e => setContent(e.target.value)}
            onKeyDown={e => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              if (e.key === "Escape" && sending) { abortRef.current?.abort(); }
            }}
            placeholder={status === "running" ? (sending ? "Generating…" : "Message your agent…") : "Agent is stopped"}
            disabled={status !== "running"}
            rows={1}
            className="flex-1 bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3.5 py-2.5 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30 disabled:opacity-40 transition-colors resize-none overflow-hidden" />
          {sending ? (
            <button onClick={() => { abortRef.current?.abort(); }}
              className="px-4 py-2.5 text-xs font-mono bg-red-400/15 text-red-400 border border-red-400/25 rounded-lg hover:bg-red-400/25 transition-colors shrink-0">
              ■ Stop
            </button>
          ) : (
            <button onClick={() => send()} disabled={!content.trim() || status !== "running"}
              className="px-4 py-2.5 text-xs font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25 rounded-lg hover:bg-violet-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors shrink-0">
              Send
            </button>
          )}
        </div>
        <p className="text-[9px] font-mono text-neutral-700 mt-1.5">{sending ? "Agent is generating — do not refresh the page · Click Stop or press Esc to cancel" : "Enter to send · Shift+Enter for new line"}</p>
      </div>
    </div>
  );
}

/* ── Shared UI helpers ── */

function BudgetBar({ current, total }: { current: number; total: number }) {
  const pct = total > 0 ? Math.min(100, (current / total) * 100) : 0;
  const color = pct >= 100 ? "bg-red-400" : pct >= 80 ? "bg-amber-400" : pct >= 50 ? "bg-cyan-400" : "bg-emerald-400/80";
  return (
    <div className="flex items-center gap-1.5 shrink-0" title={`Spent $${current.toFixed(4)} of $${total.toFixed(2)} budget`}>
      <div className="w-16 h-[3px] rounded-full bg-white/[0.05] overflow-hidden">
        <div className={`h-full ${color} transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-neutral-500">
        ${current.toFixed(current < 0.1 ? 4 : 2)}
        <span className="text-neutral-700"> / ${total.toFixed(2)}</span>
      </span>
    </div>
  );
}

function CopyButton({ text, label = "copy", className = "" }: { text: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async e => {
        e.stopPropagation();
        try { await navigator.clipboard.writeText(text); } catch { return; }
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
      className={`text-[10px] font-mono transition-all ${copied ? "text-emerald-400" : "text-neutral-600 hover:text-violet-300"} ${className}`}
      title="Copy to clipboard">
      {copied ? "✓ copied" : label}
    </button>
  );
}

function CodeBlock({ lang, text }: { lang: string; text: string }) {
  const pretty = lang && lang !== "text" ? lang : "";
  return (
    <div className="relative group my-2 rounded-lg overflow-hidden border border-neutral-800/40 bg-black/40">
      <div className="flex items-center justify-between px-3 py-1 border-b border-neutral-800/40 bg-black/30">
        <span className="text-[9px] font-mono uppercase tracking-[0.15em] text-neutral-500">{pretty || "code"}</span>
        <CopyButton text={text} className="opacity-0 group-hover:opacity-100" />
      </div>
      <SyntaxHighlighter
        language={pretty || "text"}
        style={oneDark as unknown as Record<string, React.CSSProperties>}
        customStyle={{ margin: 0, padding: "10px 12px", background: "transparent", fontSize: "12px", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
        codeTagProps={{ style: { fontSize: "12px", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" } }}>
        {text}
      </SyntaxHighlighter>
    </div>
  );
}

/* ── Markdown renderer ── */

function AgentMarkdown({ content, isUser }: { content: string; isUser: boolean }) {
  if (isUser) {
    return <p className="whitespace-pre-wrap break-words">{content}</p>;
  }

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h1 className="text-lg font-bold text-white mt-3 mb-1">{children}</h1>,
        h2: ({ children }) => <h2 className="text-base font-bold text-white mt-3 mb-1">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-semibold text-neutral-200 mt-2 mb-1">{children}</h3>,
        p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
        ul: ({ children }) => <ul className="list-disc list-inside space-y-0.5 mb-2 ml-1">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal list-inside space-y-0.5 mb-2 ml-1">{children}</ol>,
        li: ({ children }) => <li className="text-neutral-300">{children}</li>,
        strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
        em: ({ children }) => <em className="italic text-neutral-400">{children}</em>,
        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-cyan-400 hover:text-cyan-300 underline underline-offset-2">{children}</a>,
        code: ({ className, children }) => {
          const isBlock = className?.includes("language-");
          if (isBlock) {
            const lang = className?.replace("language-", "") || "text";
            const text = String(children).replace(/\n$/, "");
            return <CodeBlock lang={lang} text={text} />;
          }
          return <code className="bg-white/[0.06] px-1.5 py-0.5 rounded text-[12px] text-cyan-300/80">{children}</code>;
        },
        pre: ({ children }) => <>{children}</>,
        blockquote: ({ children }) => <blockquote className="border-l-2 border-violet-500/30 pl-3 text-neutral-400 italic my-2">{children}</blockquote>,
        hr: () => <hr className="border-neutral-800/50 my-3" />,
        table: ({ children }) => <div className="overflow-x-auto my-2"><table className="text-xs border-collapse">{children}</table></div>,
        th: ({ children }) => <th className="border border-neutral-800/50 px-2 py-1 text-left text-neutral-300 bg-neutral-900/50">{children}</th>,
        td: ({ children }) => <td className="border border-neutral-800/50 px-2 py-1 text-neutral-400">{children}</td>,
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════ */
/* Settings Modal                                                             */
/* ═══════════════════════════════════════════════════════════════════════════ */

function SettingsModal({ agent, onClose, onUpdate }: { agent: HostedAgent; onClose: () => void; onUpdate: () => void }) {
  const router = useRouter();
  const [prompt, setPrompt] = useState(agent.system_prompt);
  const [model, setModel] = useState(agent.model);
  const [budget, setBudget] = useState(String(agent.budget_usd));
  const [hbEnabled, setHbEnabled] = useState(agent.heartbeat_enabled);
  const [hbSeconds, setHbSeconds] = useState(String(agent.heartbeat_seconds));
  const [stuckLoop, setStuckLoop] = useState(agent.stuck_loop_detection);
  const [models, setModels] = useState<FreeModel[]>([]);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    authFetch(`${API_URL}/api/v1/hosted-agents/models`)
      .then(r => r.json())
      .then(d => setModels(d.models || []))
      .catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true); setError("");
    try {
      const body: Record<string, unknown> = {};
      if (prompt !== agent.system_prompt) body.system_prompt = prompt;
      if (model !== agent.model) body.model = model;
      const b = parseFloat(budget);
      if (!isNaN(b) && b !== agent.budget_usd) body.budget_usd = b;
      if (hbEnabled !== agent.heartbeat_enabled) body.heartbeat_enabled = hbEnabled;
      const hb = parseInt(hbSeconds);
      if (!isNaN(hb) && hb !== agent.heartbeat_seconds) body.heartbeat_seconds = hb;
      if (stuckLoop !== agent.stuck_loop_detection) body.stuck_loop_detection = stuckLoop;
      if (Object.keys(body).length === 0) { setSaving(false); return; }

      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agent.id}`, {
        method: "PATCH", body: JSON.stringify(body),
      });
      if (res.ok) { setToast("Saved"); setTimeout(() => setToast(""), 2000); onUpdate(); }
      else { const d = await res.json().catch(() => ({})); setError(d.detail || "Error"); }
    } catch { setError("Network error"); }
    setSaving(false);
  };

  const handleDelete = async () => {
    if (!confirm("Delete this agent? This cannot be undone.")) return;
    setDeleting(true);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agent.id}`, { method: "DELETE" });
      if (res.ok) router.push("/hosted-agents");
      else { const d = await res.json().catch(() => ({})); setError(d.detail || "Failed"); }
    } catch { setError("Network error"); }
    setDeleting(false);
  };

  const inputCls = "w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3.5 py-2.5 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30 transition-colors";
  const labelCls = "block text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-500 mb-2";

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-lg mx-4 bg-[#0a0a0a] border border-neutral-800/50 rounded-xl overflow-hidden max-h-[85vh] flex flex-col">
        <div className="h-[2px] w-full bg-gradient-to-r from-violet-400 to-transparent" />
        <div className="px-6 py-4 border-b border-neutral-800/40 flex items-center justify-between shrink-0">
          <h3 className="text-sm font-mono text-white">Settings — {agent.agent_name}</h3>
          <button onClick={onClose} className="text-neutral-600 hover:text-neutral-400 text-sm">×</button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          <div>
            <label className={labelCls}>System Prompt (AGENT.md)</label>
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)} className={inputCls + " min-h-[120px] resize-y"} />
          </div>
          <div>
            <label className={labelCls}>Model</label>
            <select value={model} onChange={e => setModel(e.target.value)} className={inputCls + " cursor-pointer"}>
              {models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </div>
          <div>
            <label className={labelCls}>Budget (USD)</label>
            <input type="number" step="0.1" min="0.1" max="100" value={budget}
              onChange={e => setBudget(e.target.value)} className={inputCls + " max-w-[200px]"} />
            <p className="text-[10px] font-mono text-neutral-700 mt-1">Current spend: ${agent.total_cost_usd.toFixed(4)}</p>
          </div>
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className={labelCls + " mb-0"}>Heartbeat</label>
              <button onClick={() => setHbEnabled(!hbEnabled)}
                className={`relative w-9 h-5 rounded-full transition-colors ${hbEnabled ? "bg-emerald-400/30" : "bg-neutral-800"}`}>
                <div className={`absolute top-0.5 w-4 h-4 rounded-full transition-all ${hbEnabled ? "left-[18px] bg-emerald-400" : "left-0.5 bg-neutral-600"}`} />
              </button>
            </div>
            {hbEnabled && (
              <select value={hbSeconds} onChange={e => setHbSeconds(e.target.value)}
                className={inputCls + " max-w-[200px] cursor-pointer text-xs"}>
                <option value="300">Every 5 min</option>
                <option value="900">Every 15 min</option>
                <option value="1800">Every 30 min</option>
                <option value="3600">Every 1 hour</option>
                <option value="7200">Every 2 hours</option>
              </select>
            )}
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className={labelCls + " mb-0"}>Stuck Loop Detection</label>
              <button onClick={() => setStuckLoop(!stuckLoop)}
                className={`relative w-9 h-5 rounded-full transition-colors ${stuckLoop ? "bg-amber-400/30" : "bg-neutral-800"}`}>
                <div className={`absolute top-0.5 w-4 h-4 rounded-full transition-all ${stuckLoop ? "left-[18px] bg-amber-400" : "left-0.5 bg-neutral-600"}`} />
              </button>
            </div>
            <p className="text-[10px] font-mono text-neutral-600 leading-relaxed">
              Injects ModelRetry when the agent repeats the same tool call, A-B-A-B alternates, or makes
              no-op calls. Saves tokens on runaway loops, may interrupt legitimate polling.
              Takes effect on next start.
            </p>
          </div>

          <div className="bg-white/[0.02] border border-neutral-800/50 rounded-lg p-4">
            <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600 mb-2">Info</p>
            <div className="grid grid-cols-2 gap-1.5 text-xs font-mono">
              <span className="text-neutral-600">Agent ID</span><span className="text-neutral-400 truncate">{agent.agent_id}</span>
              <span className="text-neutral-600">Handle</span><span className="text-neutral-400">@{agent.agent_handle}</span>
              <span className="text-neutral-600">.deep/</span><span className="text-neutral-400">{agent.memory_limit_mb} MB</span>
              <span className="text-neutral-600">Created</span><span className="text-neutral-400">{timeAgo(agent.created_at)}</span>
            </div>
          </div>
          {error && (
            <div className="px-3 py-2 text-xs font-mono text-red-400/90 bg-red-400/[0.06] border border-red-400/15 rounded-lg">{error}</div>
          )}
        </div>
        <div className="px-6 py-4 border-t border-neutral-800/40 flex items-center justify-between shrink-0">
          <button onClick={handleDelete} disabled={deleting}
            className="px-3 py-1.5 text-xs font-mono text-red-400/70 border border-red-400/15 rounded-lg hover:bg-red-400/[0.06] hover:text-red-400 disabled:opacity-40 transition-colors">
            {deleting ? "Deleting…" : "Delete Agent"}
          </button>
          <div className="flex items-center gap-3">
            {toast && <span className="text-[10px] font-mono text-emerald-400">{toast}</span>}
            <button onClick={onClose} className="px-3 py-1.5 text-xs font-mono text-neutral-500 hover:text-neutral-300 transition-colors">Cancel</button>
            <button onClick={save} disabled={saving}
              className="px-4 py-1.5 text-xs font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25 rounded-lg hover:bg-violet-500/25 disabled:opacity-40 transition-colors">
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
