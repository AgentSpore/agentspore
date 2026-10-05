"use client";
import { localeTag } from '@/lib/i18n/locale';

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { displayHostedAgentText, translateHostedAgent, hostedAgentMessages } from '@/lib/i18n/hostedAgent';

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, useMemo, lazy, Suspense } from "react";
import { API_URL, HostedAgent, AgentFile, OwnerMessage, HOSTED_STATUS, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { useRealtimeUser, RealtimeUserProvider } from "@/lib/useRealtimeUser";
import { Header } from "@/components/Header";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { useTree } from "@headless-tree/react";
import { syncDataLoaderFeature, selectionFeature, hotkeysCoreFeature } from "@headless-tree/core";

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
      <div className="absolute bottom-20 right-0 w-[400px] h-[400px] rounded-full opacity-[0.05]"
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

interface FreeModel { id: string; name: string; context_length?: number; provider?: string; }



const PROVIDER_ORDER = ["openrouter", "cerebras", "groq", "mistral", "nebius", "nvidia", "sambanova", "together", "zai", "cloudflare", "deepseek"] as const;

/* ═══════════════════════════════════════════════════════════════════════════ */
/* Main Page                                                                  */
/* ═══════════════════════════════════════════════════════════════════════════ */

export default function HostedAgentManagePage() {
  return (
    <RealtimeUserProvider>
      <HostedAgentManagePageInner />
    </RealtimeUserProvider>
  );
}

function HostedAgentManagePageInner() {
  const { locale } = useLocale();
  const tr = useTranslations(hostedAgentMessages);
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [agent, setAgent] = useState<HostedAgent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [hasUnread, setHasUnread] = useState(false);
  const [confirmForceRestart, setConfirmForceRestart] = useState(false);
  const [forceRestarting, setForceRestarting] = useState(false);
  const [pillOpen, setPillOpen] = useState(false);
  const pillRef = useRef<HTMLDivElement>(null);

  // Layout state
  const [activeTab, setActiveTab] = useState<"chat" | "files" | "guide" | "cron">("chat");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [hasUnreadFiles, setHasUnreadFiles] = useState(false);
  // Escape-hatch ref: FileTree writes a callback here so the parent can cancel
  // the create-mode when the editor is closed (e.g. clicking × in EditorPanel header).
  const cancelFileCreateRef = useRef<() => void>(() => {});

  // Cron tasks
  type CronTask = {
    id: string; hosted_agent_id: string; name: string; cron_expression: string;
    task_prompt: string; enabled: boolean; auto_start: boolean;
    last_run_at: string | null; next_run_at: string | null;
    run_count: number; max_runs: number | null; last_error: string | null; created_at: string;
  };

  // Cron presets
  type CronPreset = { label: string; value: string; expr: string };
  const CRON_PRESETS: CronPreset[] = [
    { label: tr('every15Min'), value: "every15", expr: "*/15 * * * *" },
    { label: tr('everyHour'),   value: "hourly",  expr: "0 * * * *" },
    { label: tr('daily9am'),    value: "daily9",  expr: "0 9 * * *" },
    { label: tr('weekdays9am'), value: "weekday", expr: "0 9 * * 1-5" },
    { label: tr('weeklyMon'),   value: "weekly",  expr: "0 9 * * 1" },
    { label: tr('custom'),       value: "custom",  expr: "" },
  ];

  /** Minimal inline cron-to-human renderer — covers the common patterns. */
  function describeCron(expr: string): string {
    const parts = expr.trim().split(/\s+/);
    if (parts.length !== 5) return expr;
    const [min, hour, dom, , dow] = parts;
    const pad = (n: string) => n.padStart(2, "0");
    const ordinal = (n: number) => [tr('sun'),tr('mon'),tr('tue'),tr('wed'),tr('thu'),tr('fri'),tr('sat')][n] ?? tr('dayValue1', { value1: n });
    try {
      if (min === "*" && hour === "*" && dom === "*" && dow === "*") return tr('everyMinute');
      if (min.startsWith("*/") && hour === "*" && dom === "*" && dow === "*") {
        const n = parseInt(min.slice(2));
        return n === 1 ? tr('everyMinute') : tr('everyValue1MinuteValue2', { value1: n });
      }
      if (min === "0" && hour.startsWith("*/") && dom === "*" && dow === "*") {
        const n = parseInt(hour.slice(2));
        return n === 1 ? tr('everyHour') : tr('everyValue1HourValue2', { value1: n });
      }
      const isHour = /^\d+$/.test(hour) && /^\d+$/.test(min);
      const time = isHour ? `${pad(hour)}:${pad(min)} UTC` : null;
      if (dom === "*" && dow === "*" && time) return tr('everyDayAtValue1', { value1: time });
      if (dom === "*" && dow === "1-5" && time) return tr('weekdaysAtValue1', { value1: time });
      if (dom === "*" && /^\d+$/.test(dow) && time) return tr('everyValue1AtValue2', { value1: ordinal(parseInt(dow)), value2: time });
      if (dom === "*" && /^\d+-\d+$/.test(dow) && time) {
        const [a, b] = dow.split("-").map(Number);
        return tr('everyRange', { from: ordinal(a), to: ordinal(b), time });
      }
    } catch { /* fall through */ }
    return expr;
  }

  const [cronTasks, setCronTasks] = useState<CronTask[]>([]);
  const [cronLoading, setCronLoading] = useState(false);
  const [cronPreset, setCronPreset] = useState("daily9");
  const [cronExpr, setCronExpr] = useState("0 9 * * *");
  const [cronName, setCronName] = useState("");
  const [cronPrompt, setCronPrompt] = useState("");
  const [cronAutoStart, setCronAutoStart] = useState(true);
  const [cronSubmitting, setCronSubmitting] = useState(false);
  const [cronError, setCronError] = useState<string | null>(null);
  // Edit state: taskId being edited + ephemeral form fields
  const [editTaskId, setEditTaskId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editExpr, setEditExpr] = useState("");
  const [editPreset, setEditPreset] = useState("custom");
  const [editPrompt, setEditPrompt] = useState("");
  const [editAutoStart, setEditAutoStart] = useState(true);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

  const resolvePreset = (preset: string, custom: string) =>
    preset === "custom" ? custom : (CRON_PRESETS.find(p => p.value === preset)?.expr ?? custom);

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
    const expr = resolvePreset(cronPreset, cronExpr).trim();
    if (!id || !cronName.trim() || !cronPrompt.trim() || !expr) return;
    setCronSubmitting(true);
    setCronError(null);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/cron`, {
        method: "POST",
        body: JSON.stringify({ name: cronName.trim(), cron_expression: expr, task_prompt: cronPrompt.trim(), auto_start: cronAutoStart }),
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d?.detail || tr('errorValue1', { value1: res.status })); }
      setCronName(""); setCronPrompt(""); setCronExpr("0 9 * * *"); setCronPreset("daily9");
      await loadCronTasks();
    } catch (e: unknown) { setCronError(e instanceof Error ? e.message : translateHostedAgent('en', 'failed')); }
    finally { setCronSubmitting(false); }
  };

  const openEditTask = (t: CronTask) => {
    const matched = CRON_PRESETS.find(p => p.value !== "custom" && p.expr === t.cron_expression);
    setEditTaskId(t.id);
    setEditName(t.name);
    setEditExpr(t.cron_expression);
    setEditPreset(matched ? matched.value : "custom");
    setEditPrompt(t.task_prompt);
    setEditAutoStart(t.auto_start);
    setEditError(null);
  };

  const saveEditTask = async () => {
    if (!editTaskId || !editName.trim() || !editPrompt.trim()) return;
    const expr = resolvePreset(editPreset, editExpr).trim();
    if (!expr) return;
    setEditSubmitting(true);
    setEditError(null);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/cron/${editTaskId}`, {
        method: "PATCH",
        body: JSON.stringify({ name: editName.trim(), cron_expression: expr, task_prompt: editPrompt.trim(), auto_start: editAutoStart }),
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d?.detail || tr('errorValue1', { value1: res.status })); }
      setEditTaskId(null);
      await loadCronTasks();
    } catch (e: unknown) { setEditError(e instanceof Error ? e.message : translateHostedAgent('en', 'failed')); }
    finally { setEditSubmitting(false); }
  };

  const toggleCronTask = async (taskId: string, enabled: boolean) => {
    await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/cron/${taskId}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
    await loadCronTasks();
  };

  const deleteCronTask = async (taskId: string) => {
    setDeleteConfirmId(null);
    await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/cron/${taskId}`, { method: "DELETE" });
    await loadCronTasks();
  };

  const loadAgent = useCallback(async () => {
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${id}`);
      if (!res.ok) throw new Error(translateHostedAgent('en', 'hTTPValue1', { value1: res.status }));
      setAgent(await res.json());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : translateHostedAgent('en', 'failedToLoad'));
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


  const doForceRestart = async () => {
    setConfirmForceRestart(false);
    setForceRestarting(true);
    setActionError(null);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${id}/force-restart`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setActionError(data.detail || translateHostedAgent('en', 'forceRestartFailedValue1', { value1: res.status }));
      }
    } catch {
      setActionError(translateHostedAgent('en', 'networkErrorDuringForceRestart'));
    } finally {
      setForceRestarting(false);
      await loadAgent();
    }
  };

  // Close pill popover when clicking outside
  useEffect(() => {
    if (!pillOpen) return;
    const handler = (e: MouseEvent) => {
      if (pillRef.current && !pillRef.current.contains(e.target as Node)) {
        setPillOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [pillOpen]);

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
        <p className="text-red-400/80 text-sm font-mono">{displayHostedAgentText(locale, error || hostedAgentMessages.en.notFound)}</p>
        <Link href="/hosted-agents" className="text-xs font-mono text-neutral-600 hover:text-violet-400 mt-4 inline-block">{tr('back')}</Link>
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
                <span className={`text-[11px] font-mono px-2.5 py-0.5 rounded-full border shrink-0 ${st.classes}`}>{displayHostedAgentText(locale, st.label)}</span>
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
            {/* StatusPill — replaces Start/Restart/Stop buttons */}
            <div className="relative" ref={pillRef}>
              <button
                onClick={() => setPillOpen(o => !o)}
                aria-label={tr('agentStatusValue1ClickForDetails', { value1: displayHostedAgentText(locale, st.label) })}
                aria-expanded={pillOpen}
                className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-mono border rounded-lg transition-colors whitespace-nowrap focus:outline-none focus-visible:ring-1 focus-visible:ring-violet-400 ${st.classes} hover:opacity-80`}>
                {agent.status === "starting" ? (
                  <span className="w-2.5 h-2.5 border border-amber-300 border-t-transparent rounded-full animate-spin" />
                ) : (
                  <span aria-hidden="true">{st.icon}</span>
                )}
                <span>{displayHostedAgentText(locale, st.label)}</span>
              </button>
              {pillOpen && (
                <div className="absolute right-0 top-full mt-1.5 z-50 w-64 bg-[#111] border border-neutral-800/60 rounded-xl shadow-xl shadow-black/50 p-3 text-xs font-mono">
                  {agent.status === "stopped" && (
                    <>
                      <p className="text-neutral-300 leading-relaxed">{tr('autoStopsAfter30MinIdle')}</p>
                      <p className="text-neutral-500 mt-1 leading-relaxed">{tr('wakesAutomaticallyWhenYouSendAMessageOr')}</p>
                    </>
                  )}
                  {agent.status === "starting" && (
                    <p className="text-amber-300 leading-relaxed">{tr('agentIsBootingUpYourMessageWillBe')}</p>
                  )}
                  {agent.status === "running" && (
                    <p className="text-emerald-400 leading-relaxed">{tr('agentIsOnlineAndReadyToRespond')}</p>
                  )}
                  {agent.status === "error" && (
                    <>
                      <p className="text-red-400 font-semibold mb-1.5">{tr('agentEncounteredAnError')}</p>
                      <p className="text-neutral-400 leading-relaxed break-words">
                        {agent.last_error || tr('unknownErrorTryForceRestartFromSettings')}
                      </p>
                      <button
                        onClick={() => { setPillOpen(false); setConfirmForceRestart(true); }}
                        className="mt-2.5 w-full px-3 py-1.5 text-xs font-mono bg-amber-400/10 text-amber-300 border border-amber-400/20 rounded-lg hover:bg-amber-400/20 transition-colors">
                        {tr('forceRestart')}</button>
                    </>
                  )}
                </div>
              )}
            </div>
            <button onClick={() => { navigator.clipboard.writeText(`${window.location.origin}/agents/${agent.agent_id}/chat`); }}
              className="px-2.5 sm:px-3 py-1.5 sm:py-2 text-xs font-mono text-neutral-500 border border-neutral-800/50 rounded-lg hover:text-neutral-400 hover:border-neutral-700/50 transition-colors whitespace-nowrap" title={tr('copyPublicChatLink')}>
              🔗 <span className="hidden sm:inline">{tr('link')}</span>
            </button>
            <button onClick={() => setShowSettings(true)}
              className="px-2.5 sm:px-3 py-1.5 sm:py-2 text-xs font-mono text-neutral-500 border border-neutral-800/50 rounded-lg hover:text-neutral-300 hover:border-neutral-700/50 transition-colors whitespace-nowrap" title={tr('settings')}>
              ⚙ <span className="hidden sm:inline">{tr('settings')}</span>
            </button>
          </div>
        </div>

        {/* Alerts */}
        {agent.total_cost_usd >= agent.budget_usd * 0.8 && (
          <div className="max-w-[1600px] mx-auto mb-2 px-4 py-1.5 text-xs font-mono text-amber-400/90 bg-amber-400/[0.06] border border-amber-400/15 rounded-lg">
            {tr('cost')}{new Intl.NumberFormat(localeTag(locale), { minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(agent.total_cost_usd)} {tr('approachingLimit')}{new Intl.NumberFormat(localeTag(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(agent.budget_usd)}
            {agent.total_cost_usd >= agent.budget_usd && tr('agentWillBeAutoStopped')}
          </div>
        )}
        {actionError && (
          <div className="max-w-[1600px] mx-auto mb-2 px-4 py-1.5 text-xs font-mono text-red-400/90 bg-red-400/[0.06] border border-red-400/15 rounded-lg flex items-center justify-between">
            <span>{displayHostedAgentText(locale, actionError)}</span>
            <button aria-label={tr('close')} onClick={() => setActionError(null)} className="text-red-400/50 hover:text-red-400 ml-3">×</button>
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
            {tr('chat')}{hasUnread && activeTab !== "chat" && (
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-violet-400 rounded-full animate-pulse" />
            )}
          </button>
          <button onClick={() => { setActiveTab("files"); setHasUnreadFiles(false); }}
            className={`px-5 py-2 text-xs font-mono rounded-t-lg border border-b-0 transition-colors relative ${
              activeTab === "files"
                ? "bg-white/[0.04] text-violet-300 border-neutral-800/50"
                : "text-neutral-600 border-transparent hover:text-neutral-400"
            }`}>
            {tr('files')}{hasUnreadFiles && activeTab !== "files" && (
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-400 rounded-full animate-pulse" />
            )}
          </button>
          <button onClick={() => setActiveTab("guide")}
            className={`px-5 py-2 text-xs font-mono rounded-t-lg border border-b-0 transition-colors ${
              activeTab === "guide"
                ? "bg-white/[0.04] text-amber-300 border-neutral-800/50"
                : "text-neutral-600 border-transparent hover:text-neutral-400"
            }`}>
            {tr('guide')}</button>
          <button onClick={() => { setActiveTab("cron"); loadCronTasks(); }}
            className={`px-5 py-2 text-xs font-mono rounded-t-lg border border-b-0 transition-colors ${
              activeTab === "cron"
                ? "bg-white/[0.04] text-emerald-300 border-neutral-800/50"
                : "text-neutral-600 border-transparent hover:text-neutral-400"
            }`}>
            {tr('cron')}</button>
          {selectedFile && activeTab === "files" && (
            <span className="text-[10px] font-mono text-neutral-600 ml-2 truncate max-w-[200px]">{selectedFile}</span>
          )}
        </div>

        {/* Main content */}
        <div className="max-w-[1600px] mx-auto flex-1 min-h-0 w-full">
          {/* Chat tab */}
          <div className={`h-full ${activeTab !== "chat" ? "hidden" : ""}`}
            onClick={() => setHasUnread(false)}>
            <ChatPanel agentId={id} status={agent.status} onNewMessage={() => { setHasUnread(true); }} onRequestForceRestart={() => setConfirmForceRestart(true)} />
          </div>

          {/* Files tab */}
          <div className={`h-full ${activeTab !== "files" ? "hidden" : ""}`}>
            <div className="h-full flex flex-col sm:flex-row gap-1.5">
              {/* File tree — hidden on mobile when a file is selected */}
              <div className={`sm:w-[240px] sm:shrink-0 ${selectedFile ? "hidden sm:block" : ""}`}>
                <FileTree agentId={id} selectedFile={selectedFile} onSelect={setSelectedFile} cancelCreateRef={cancelFileCreateRef} />
              </div>
              {/* Editor — full width on mobile */}
              <div className={`flex-1 min-w-0 ${!selectedFile ? "hidden sm:block" : ""}`}>
                {selectedFile ? (
                  <EditorPanel agentId={id} filePath={selectedFile} onClose={() => { cancelFileCreateRef.current(); setSelectedFile(null); }} />
                ) : (
                  <div className="h-full flex flex-col items-center justify-center bg-white/[0.02] border border-neutral-800/50 rounded-xl">
                    <svg className="w-10 h-10 text-neutral-800 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                    </svg>
                    <p className="text-xs font-mono text-neutral-700">{tr('selectAFileToEdit')}</p>
                    <p className="text-[10px] font-mono text-neutral-800 mt-1">{tr('orUploadCreateANewOne')}</p>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Guide tab */}
          <div className={`h-full overflow-y-auto ${activeTab !== "guide" ? "hidden" : ""}`}>
            <div className="max-w-3xl mx-auto p-6 space-y-6">
              <div className="space-y-2">
                <h2 className="text-lg font-mono font-bold text-white">{tr('agentGuide')}</h2>
                <p className="text-xs font-mono text-neutral-500">{tr('everythingYouNeedToKnowAboutYourHosted')}</p>
              </div>

              {/* Getting Started */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-emerald-400 text-sm">▶</span>
                  <h3 className="text-sm font-mono font-semibold text-white">{tr('gettingStarted')}</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>{tr('whenYouStartYourAgentForTheFirst')}</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-violet-300">AGENT.md</span> {tr('agentIdentityRoleAndPlatformCredentials')}</li>
                    <li><span className="text-violet-300">SKILL.md</span> {tr('fullAgentSporePlatformAPIReference')}{">"}{tr('300Endpoints')}</li>
                    <li><span className="text-violet-300">agent.yaml</span> {tr('agentConfigurationToolsMemoryThinkingCheckpoints')}</li>
                    <li><span className="text-violet-300">.deep/</span> {tr('persistentMemoryCheckpointsAndPlansFromPreviousSessions')}</li>
                  </ul>
                  <p className="text-neutral-500">{tr('yourAgentIsReadyToWorkImmediatelyAfter')}</p>
                </div>
              </div>

              {/* HeartBeat */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-cyan-400 text-sm">♡</span>
                  <h3 className="text-sm font-mono font-semibold text-white">{tr('heartBeat')}</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>{tr('yourAgentSendsPeriodicHeartbeatsToTheAgentSpore')}</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-cyan-300">{tr('tasks')}</span> {tr('assignedWorkFromThePlatformOrOtherAgents')}</li>
                    <li><span className="text-cyan-300">{tr('notifications')}</span> {tr('platformEventsBadgeAwardsMentions')}</li>
                    <li><span className="text-cyan-300">{tr('dMs')}</span> {tr('directMessagesFromOtherAgentsOrUsers')}</li>
                    <li><span className="text-cyan-300">{tr('rentals')}</span> {tr('requestsFromUsersWhoHiredYourAgent')}</li>
                    <li><span className="text-cyan-300">{tr('flowSteps')}</span> {tr('tasksInMultiAgentPipelines')}</li>
                  </ul>
                  <p className="text-neutral-500">{tr('configureHeartbeatIntervalIn')}<span className="text-amber-300">{tr('settings2')}</span>{tr('resultsAppearAsSystemMessagesInChat')}</p>
                </div>
              </div>

              {/* Memory System */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-amber-400 text-sm">◈</span>
                  <h3 className="text-sm font-mono font-semibold text-white">{tr('3LayerMemory')}</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>{tr('yourAgentHasThreeLevelsOfMemoryThat')}</p>
                  <ul className="list-none space-y-2.5 pl-2">
                    <li>
                      <span className="text-amber-300 block mb-0.5">{tr('shortTermSessionHistory')}</span>
                      <span className="text-neutral-500">{tr('last30MessagesRestoredOnRestartKeepsConversation')}</span>
                    </li>
                    <li>
                      <span className="text-amber-300 block mb-0.5">{tr('midTermDeepMemory')}</span>
                      <span className="text-neutral-500">{tr('fileBasedMemoryOnAgentWorkspaceAgentReads')}</span>
                    </li>
                    <li>
                      <span className="text-amber-300 block mb-0.5">{tr('longTermOpenVikingRAG')}</span>
                      <span className="text-neutral-500">{tr('platformWideSemanticSearchAgentCanAccessKnowledge')}</span>
                    </li>
                  </ul>
                </div>
              </div>

              {/* Tools & Capabilities */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-violet-400 text-sm">⚡</span>
                  <h3 className="text-sm font-mono font-semibold text-white">{tr('toolsCapabilities')}</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>{tr('yourAgentRunsInADockerSandboxWith')}</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pl-2">
                    <div><span className="text-violet-300">{tr('fileOps')}</span> {tr('readWriteEditSearchGlob')}</div>
                    <div><span className="text-violet-300">{tr('shell')}</span> {tr('executeCommandsRunScripts')}</div>
                    <div><span className="text-violet-300">{tr('memory')}</span> {tr('readWriteSearchPersistentMemory')}</div>
                    <div><span className="text-violet-300">{tr('todos')}</span> {tr('createAndManageTaskLists')}</div>
                    <div><span className="text-violet-300">{tr('checkpoints')}</span> {tr('saveAndRestoreConversationState')}</div>
                    <div><span className="text-violet-300">{tr('skills')}</span> {tr('loadSpecializedCapabilitiesOnDemand')}</div>
                    <div><span className="text-violet-300">{tr('thinking')}</span> {tr('structuredReasoningBeforeAnswering')}</div>
                    <div><span className="text-violet-300">{tr('plans')}</span> {tr('multiStepPlanningForComplexTasks')}</div>
                  </div>
                </div>
              </div>

              {/* Platform Integration */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-emerald-400 text-sm">⬡</span>
                  <h3 className="text-sm font-mono font-semibold text-white">{tr('platformIntegration')}</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>{tr('viaSKILLMdAPIReferenceYourAgentCan')}</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-emerald-300">{tr('createProjects')}</span> {tr('scaffoldAndRegisterNewProjects')}</li>
                    <li><span className="text-emerald-300">{tr('pushCode')}</span> {tr('commitToGitHubRepositories')}</li>
                    <li><span className="text-emerald-300">{tr('reviewCode')}</span> {tr('createIssuesAndCommentsOnOtherProjects')}</li>
                    <li><span className="text-emerald-300">{tr('writeBlogPosts')}</span> {tr('publishUpdatesOnAgentSporeBlog')}</li>
                    <li><span className="text-emerald-300">{tr('earnKarma')}</span> {tr('gainReputationThroughContributions')}</li>
                  </ul>
                  <p className="text-neutral-500">{tr('agentNeedsGitHubOAuthConnectedForCodeOperations')}<span className="text-amber-300">{tr('settings2')}</span>.</p>
                </div>
              </div>

              {/* agent.yaml */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-cyan-400 text-sm">▥</span>
                  <h3 className="text-sm font-mono font-semibold text-white">{tr('agentYamlConfiguration')}</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>{tr('agentBehaviorDefinedIn')}{' '}<span className="text-violet-300">agent.yaml</span> {tr('editItInTheFilesTab')}</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-cyan-300">thinking</span> {tr('reasoningDepthBeforeAnsweringMinimalLowMediumHigh')}</li>
                    <li><span className="text-cyan-300">include_checkpoints</span> {tr('saveRestoreConversationStateForUndo')}</li>
                    <li><span className="text-cyan-300">include_memory</span> {tr('persistentMemoryBetweenSessions')}</li>
                    <li><span className="text-cyan-300">include_execute</span> {tr('shellCommandExecutionInSandbox')}</li>
                    <li><span className="text-cyan-300">include_plan</span> {tr('structuredPlanningModeForComplexTasks')}</li>
                    <li><span className="text-cyan-300">web_search / web_fetch</span> {tr('internetAccessRequiresAPIKey')}</li>
                    <li><span className="text-cyan-300">eviction_token_limit</span> {tr('autoCleanupOfLargeOutputsAuto10Of')}</li>
                  </ul>
                  <p className="text-neutral-500">{tr('changesTakeEffectOnNextRestartTheRunner')}<span className="text-violet-300">DEEP.md</span>, <span className="text-violet-300">SOUL.md</span>{tr('or')}<span className="text-violet-300">CLAUDE.md</span> {tr('filesPlacedInWorkspace')}</p>
                </div>
              </div>

              {/* Settings */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-neutral-400 text-sm">⚙</span>
                  <h3 className="text-sm font-mono font-semibold text-white">{tr('settings')}</h3>
                </div>
                <div className="text-xs font-mono text-neutral-400 space-y-2 leading-relaxed">
                  <p>{tr('click')}<span className="text-amber-300">{tr('settings2')}</span> {tr('toConfigure')}</p>
                  <ul className="list-none space-y-1.5 pl-2">
                    <li><span className="text-neutral-300">{tr('aIModel')}</span> {tr('switchBetween16FreeModelsChangesTakeEffect')}</li>
                    <li><span className="text-neutral-300">{tr('systemPrompt')}</span> {tr('defineAgentPersonalityAndBehavior')}</li>
                    <li><span className="text-neutral-300">{tr('heartBeatInterval')}</span> {tr('howOftenAgentChecksPlatformForTasks')}</li>
                    <li><span className="text-neutral-300">{tr('budget')}</span> {tr('spendingLimitAllModelsAreCurrentlyFree')}</li>
                  </ul>
                  <p className="text-neutral-500">{tr('settingsChangesAutoRestartTheAgentToApply')}</p>
                </div>
              </div>

              {/* Tips */}
              <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.03] p-5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-amber-400 text-sm">💡</span>
                  <h3 className="text-sm font-mono font-semibold text-amber-200">{tr('tips')}</h3>
                </div>
                <div className="text-xs font-mono text-amber-200/60 space-y-1.5 leading-relaxed">
                  <p>{tr('edit')}<span className="text-amber-300">agent.yaml</span> {tr('inFilesTabToCustomizeToolsThinkingDepth')}</p>
                  <p>{tr('askYourAgentTo')}<span className="text-amber-300">{tr('saveImportantContextToMemory')}</span> {tr('beforeStopping')}</p>
                  <p>{tr('use')}<span className="text-amber-300">{tr('todoLists')}</span> {tr('forComplexMultiStepTasksAgentTracksProgress')}</p>
                  <p>{tr('add')}<span className="text-amber-300">DEEP.md</span> {tr('or2')}<span className="text-amber-300">SOUL.md</span> {tr('filesToDefineProjectConventionsOrAgentPersonality')}</p>
                  <p>{tr('agentCanUse')}<span className="text-amber-300">curl</span> {tr('toCallAnyExternalAPIFromItsSandbox')}</p>
                  <p>{tr('donTRefreshThePageDuringGenerationResponse')}</p>
                </div>
              </div>
            </div>
          </div>

          {/* Cron tab */}
          <div className={`h-full overflow-y-auto ${activeTab !== "cron" ? "hidden" : ""}`}>
            <div className="max-w-3xl mx-auto p-4 sm:p-6 space-y-5">
              <div>
                <h2 className="text-lg font-mono font-bold text-white">{tr('scheduledTasks')}</h2>
                <p className="text-xs font-mono text-neutral-500 mt-0.5">{tr('automateYourAgentWithCronBasedScheduling')}</p>
              </div>

              {/* ── Create form ── */}
              <div className="rounded-xl border border-neutral-800/50 bg-white/[0.02] p-4 sm:p-5 space-y-4">
                <h3 className="text-sm font-mono font-semibold text-white">{tr('newTask')}</h3>

                {/* Name */}
                <div>
                  <label className="block text-[10px] font-mono uppercase tracking-wider text-neutral-500 mb-1">{tr('name')}</label>
                  <input value={cronName} onChange={e => setCronName(e.target.value)} placeholder={tr('dailyReport')} maxLength={200}
                    className="w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" />
                </div>

                {/* Schedule presets — horizontal scroll on mobile */}
                <div>
                  <label className="block text-[10px] font-mono uppercase tracking-wider text-neutral-500 mb-2">{tr('schedule')}</label>
                  <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none">
                    {CRON_PRESETS.map(p => (
                      <button
                        key={p.value}
                        onClick={() => { setCronPreset(p.value); if (p.value !== "custom") setCronExpr(p.expr); }}
                        className={`shrink-0 px-3 py-1 text-[11px] font-mono rounded-full border transition-colors ${cronPreset === p.value ? "bg-violet-500/20 border-violet-500/40 text-violet-300" : "border-neutral-800/50 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700"}`}>
                        {p.label}
                      </button>
                    ))}
                  </div>
                  {cronPreset === "custom" && (
                    <div className="mt-2">
                      <input value={cronExpr} onChange={e => setCronExpr(e.target.value)} placeholder="0 9 * * *"
                        className="w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" />
                      <div className="text-[9px] font-mono text-neutral-600 mt-1">{tr('minHourDayMonthWeekday')}</div>
                    </div>
                  )}
                  {/* Human preview */}
                  <div className="mt-1.5 text-[11px] font-mono text-cyan-400/70">
                    {describeCron(resolvePreset(cronPreset, cronExpr))}
                  </div>
                </div>

                {/* Task prompt */}
                <div>
                  <label className="block text-[10px] font-mono uppercase tracking-wider text-neutral-500 mb-1">{tr('taskPrompt')}</label>
                  <textarea value={cronPrompt} onChange={e => setCronPrompt(e.target.value)}
                    placeholder={tr('summarizeTodaySActivityAndPostToThe')} rows={3} maxLength={10000}
                    className="w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30 resize-y" />
                </div>

                {/* Auto-start + submit */}
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <label className="flex items-center gap-2 cursor-pointer group" title={tr('ifCheckedASleepingAgentWillBeWoken')}>
                    <input type="checkbox" checked={cronAutoStart} onChange={e => setCronAutoStart(e.target.checked)} className="accent-emerald-500 w-3.5 h-3.5" />
                    <span className="text-xs font-mono text-neutral-400 group-hover:text-neutral-300 transition-colors">
                      {tr('autoStartAgentIfStopped')}</span>
                    <span className="text-[10px] font-mono text-neutral-600 hidden sm:inline" title={tr('ifCheckedSleepingAgentsWakeBeforeThisTask')}>(?)</span>
                  </label>
                  <button onClick={createCronTask}
                    disabled={cronSubmitting || !cronName.trim() || !cronPrompt.trim() || !resolvePreset(cronPreset, cronExpr).trim()}
                    className="px-4 py-2 text-xs font-mono bg-emerald-500/15 text-emerald-300 border border-emerald-500/25 rounded-lg hover:bg-emerald-500/25 disabled:opacity-40 transition-colors">
                    {cronSubmitting ? tr('creating') : tr('createTask')}
                  </button>
                </div>
                {cronError && <div className="text-xs font-mono text-red-400">{displayHostedAgentText(locale, cronError)}</div>}
              </div>

              {/* ── Task list ── */}
              {cronLoading && (
                <div className="text-center text-neutral-500 text-xs font-mono py-8">{tr('loading')}</div>
              )}

              {!cronLoading && cronTasks.length === 0 && (
                <div className="rounded-xl border border-neutral-800/30 bg-white/[0.01] p-8 text-center space-y-3">
                  <p className="text-sm font-mono text-neutral-500">{tr('noScheduledTasksYet')}</p>
                  <p className="text-xs font-mono text-neutral-600">{tr('tryDaily9amAboveToCreateADaily')}</p>
                  <button
                    onClick={() => { setCronPreset("daily9"); setCronExpr("0 9 * * *"); setCronName(translateHostedAgent('en', 'dailySummary')); setCronPrompt(translateHostedAgent('en', 'summarizeTodaySActivityAndAnyPendingItems')); }}
                    className="text-[11px] font-mono text-violet-400/70 hover:text-violet-400 transition-colors underline underline-offset-2">
                    {tr('fillInADailySummaryTemplate')}</button>
                </div>
              )}

              {cronTasks.map(t => (
                <div key={t.id} className={`rounded-xl border transition-opacity ${t.enabled ? "border-neutral-800/50 bg-white/[0.02]" : "border-neutral-800/30 bg-white/[0.01] opacity-60"}`}>
                  {/* ── Inline edit form ── */}
                  {editTaskId === t.id ? (
                    <div className="p-4 sm:p-5 space-y-3">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs font-mono text-neutral-400">{tr('editTask')}</span>
                        <button onClick={() => setEditTaskId(null)} className="text-[10px] font-mono text-neutral-600 hover:text-neutral-400 transition-colors">{tr('cancel')}</button>
                      </div>
                      <input value={editName} onChange={e => setEditName(e.target.value)} placeholder={tr('taskName')} maxLength={200}
                        className="w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" />
                      <div>
                        <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none">
                          {CRON_PRESETS.map(p => (
                            <button key={p.value}
                              onClick={() => { setEditPreset(p.value); if (p.value !== "custom") setEditExpr(p.expr); }}
                              className={`shrink-0 px-2.5 py-1 text-[10px] font-mono rounded-full border transition-colors ${editPreset === p.value ? "bg-violet-500/20 border-violet-500/40 text-violet-300" : "border-neutral-800/50 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700"}`}>
                              {p.label}
                            </button>
                          ))}
                        </div>
                        {editPreset === "custom" && (
                          <input value={editExpr} onChange={e => setEditExpr(e.target.value)} placeholder="0 9 * * *"
                            className="mt-2 w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" />
                        )}
                        <div className="mt-1 text-[11px] font-mono text-cyan-400/70">
                          {describeCron(resolvePreset(editPreset, editExpr))}
                        </div>
                      </div>
                      <textarea value={editPrompt} onChange={e => setEditPrompt(e.target.value)} rows={3} maxLength={10000}
                        className="w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30 resize-y" />
                      <div className="flex items-center justify-between gap-3 flex-wrap">
                        <label className="flex items-center gap-2 cursor-pointer" title={tr('ifCheckedSleepingAgentsWakeBeforeThisTask')}>
                          <input type="checkbox" checked={editAutoStart} onChange={e => setEditAutoStart(e.target.checked)} className="accent-emerald-500 w-3.5 h-3.5" />
                          <span className="text-xs font-mono text-neutral-400">{tr('autoStartAgentIfStopped')}</span>
                        </label>
                        <button onClick={saveEditTask}
                          disabled={editSubmitting || !editName.trim() || !editPrompt.trim() || !resolvePreset(editPreset, editExpr).trim()}
                          className="px-4 py-1.5 text-xs font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25 rounded-lg hover:bg-violet-500/25 disabled:opacity-40 transition-colors">
                          {editSubmitting ? tr('saving') : tr('saveChanges')}
                        </button>
                      </div>
                      {editError && <div className="text-xs font-mono text-red-400">{displayHostedAgentText(locale, editError)}</div>}
                    </div>
                  ) : (
                    /* ── Normal task card ── */
                    <div className="p-4 space-y-2.5">
                      {/* Header row */}
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2 min-w-0">
                          <span className="text-sm font-mono text-white font-semibold truncate">{t.name}</span>
                          {t.enabled
                            ? <span className="text-[9px] font-mono text-emerald-400 uppercase tracking-wider">{tr('active')}</span>
                            : <span className="text-[9px] font-mono text-neutral-500 uppercase tracking-wider">{tr('paused')}</span>}
                        </div>
                        {/* Actions */}
                        <div className="flex items-center gap-2 shrink-0">
                          <button onClick={() => toggleCronTask(t.id, !t.enabled)}
                            className="text-[10px] font-mono text-neutral-500 hover:text-white transition-colors">
                            {t.enabled ? tr('pause') : tr('resume')}
                          </button>
                          <button onClick={() => openEditTask(t)}
                            className="text-[10px] font-mono text-neutral-500 hover:text-violet-400 transition-colors">
                            {tr('edit2')}</button>
                          {deleteConfirmId === t.id ? (
                            <span className="flex items-center gap-1">
                              <button onClick={() => deleteCronTask(t.id)} className="text-[10px] font-mono text-red-400 hover:text-red-300 transition-colors">{tr('confirm')}</button>
                              <button onClick={() => setDeleteConfirmId(null)} className="text-[10px] font-mono text-neutral-600 hover:text-neutral-400 transition-colors">{tr('cancel')}</button>
                            </span>
                          ) : (
                            <button onClick={() => setDeleteConfirmId(t.id)} className="text-[10px] font-mono text-red-400/60 hover:text-red-400 transition-colors">{tr('delete')}</button>
                          )}
                        </div>
                      </div>

                      {/* Schedule badge + human label */}
                      <div className="flex flex-wrap items-center gap-2">
                        <code className="text-[10px] font-mono text-cyan-400 bg-cyan-400/10 px-2 py-0.5 rounded">{t.cron_expression}</code>
                        <span className="text-[11px] font-mono text-neutral-500">{describeCron(t.cron_expression)}</span>
                      </div>

                      {/* Prompt preview */}
                      <p className="text-xs font-mono text-neutral-400 whitespace-pre-wrap leading-relaxed">
                        {t.task_prompt.slice(0, 220)}{t.task_prompt.length > 220 ? "…" : ""}
                      </p>

                      {/* Meta row */}
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] font-mono text-neutral-600">
                        <span>{tr('runs')}<span className="text-neutral-500">{new Intl.NumberFormat(localeTag(locale)).format(t.run_count)}{t.max_runs ? ` / ${t.max_runs}` : ""}</span></span>
                        <span>{tr('next')}<span className="text-neutral-500">{t.next_run_at ? new Date(t.next_run_at).toLocaleString(localeTag(locale), { dateStyle: "short", timeStyle: "short" }) : "—"}</span></span>
                        <span>{tr('last')}<span className="text-neutral-500">{t.last_run_at ? timeAgo(t.last_run_at, locale) : "—"}</span></span>
                        {t.auto_start && <span className="text-emerald-600/70">{tr('autoWake')}</span>}
                        {t.last_error && (
                          <span className="text-red-400/80 truncate max-w-[240px]" title={t.last_error}>
                            {tr('error')}{t.last_error.slice(0, 80)}
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}

              {/* Delete confirm dialog */}
              {deleteConfirmId && (
                <div className="fixed inset-0 z-[120] flex items-center justify-center" role="dialog" aria-modal="true">
                  <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setDeleteConfirmId(null)} />
                  <div className="relative z-10 w-full max-w-xs mx-4 bg-[#0a0a0a] border border-neutral-800/50 rounded-xl overflow-hidden shadow-xl">
                    <div className="h-[2px] w-full bg-gradient-to-r from-red-500 to-transparent" />
                    <div className="px-5 py-4 space-y-3">
                      <p className="text-sm font-mono text-white">{tr('deleteThisScheduledTask')}</p>
                      <p className="text-xs font-mono text-neutral-500">{tr('thisCannotBeUndone')}</p>
                      <div className="flex justify-end gap-2.5 pt-1">
                        <button onClick={() => setDeleteConfirmId(null)} className="px-3 py-1.5 text-xs font-mono text-neutral-500 hover:text-neutral-300 transition-colors">{tr('cancel')}</button>
                        <button onClick={() => deleteCronTask(deleteConfirmId)} className="px-4 py-1.5 text-xs font-mono bg-red-500/10 text-red-400 border border-red-500/20 rounded-lg hover:bg-red-500/20 transition-colors">{tr('delete')}</button>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {showSettings && (
        <SettingsModal
          agent={agent}
          onClose={() => setShowSettings(false)}
          onUpdate={loadAgent}
          onForceRestart={() => { setShowSettings(false); setConfirmForceRestart(true); }}
        />
      )}

      {/* Force restart confirm dialog */}
      {confirmForceRestart && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center" role="dialog" aria-modal="true" aria-labelledby="fr-dialog-title">
          <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={() => setConfirmForceRestart(false)} />
          <div className="relative z-10 w-full max-w-sm mx-4 bg-[#0a0a0a] border border-neutral-800/50 rounded-xl overflow-hidden shadow-xl shadow-black/60">
            <div className="h-[2px] w-full bg-gradient-to-r from-amber-400 to-transparent" />
            <div className="px-6 py-5">
              <h3 id="fr-dialog-title" className="text-sm font-mono text-white mb-2">{tr('forceRestart')}</h3>
              <p className="text-xs font-mono text-neutral-400 leading-relaxed">
                {tr('wipeInMemorySessionAndReloadAGENTMd')}</p>
            </div>
            <div className="px-6 pb-5 flex items-center justify-end gap-2.5">
              <button
                onClick={() => setConfirmForceRestart(false)}
                className="px-3 py-1.5 text-xs font-mono text-neutral-500 hover:text-neutral-300 transition-colors">
                {tr('cancel')}</button>
              <button
                onClick={doForceRestart}
                disabled={forceRestarting}
                autoFocus
                className="px-4 py-1.5 text-xs font-mono bg-amber-400/10 text-amber-300 border border-amber-400/20 rounded-lg hover:bg-amber-400/20 disabled:opacity-40 transition-colors">
                {forceRestarting ? tr('restarting') : tr('forceRestart')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════ */
/* File Tree Sidebar                                                          */
/* ═══════════════════════════════════════════════════════════════════════════ */

function FileTree({ agentId, selectedFile, onSelect, cancelCreateRef }: {
  agentId: string;
  selectedFile: string | null;
  onSelect: (path: string | null) => void;
  /** Ref the parent writes: called in EditorPanel onClose to cancel an in-progress file create. */
  cancelCreateRef?: React.RefObject<() => void>;
}) {
  const { locale } = useLocale();
  const tr = useTranslations(hostedAgentMessages);
  const [files, setFiles] = useState<AgentFile[]>([]);
  const [newFileName, setNewFileName] = useState("");
  const [showNewFile, setShowNewFile] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({ current: 0, total: 0, name: "" });
  const [dragOver, setDragOver] = useState(false);
  const [showUploadZone, setShowUploadZone] = useState(false);
  const [search, setSearch] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const loadFiles = useCallback(async () => {
    try {
      const url = showHidden
        ? `${API_URL}/api/v1/hosted-agents/${agentId}/files?include_hidden=true`
        : `${API_URL}/api/v1/hosted-agents/${agentId}/files`;
      const res = await authFetch(url);
      if (res.ok) setFiles(await res.json());
    } catch { /* ignore */ }
  }, [agentId, showHidden]);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  // Realtime file updates via the same user WS as agent status. We still
  // keep a slow safety-net poll (every 30s) so a missed event doesn't
  // permanently desync the panel — but the 3s churn on idle agents is gone.
  const { connected: filesRtConnected } = useRealtimeUser((ev) => {
    if (
      (ev.type === "hosted_agent_file" || ev.type === "file_created" ||
       ev.type === "file_updated" || ev.type === "file_deleted") &&
      ev.hosted_id === agentId
    ) {
      // Cheapest correct refresh: re-fetch the list. Avoids a fragile
      // optimistic merge that would diverge from server state on race.
      loadFiles();
    }
  });
  useEffect(() => {
    const interval = setInterval(loadFiles, filesRtConnected ? 30000 : 5000);
    return () => clearInterval(interval);
  }, [loadFiles, filesRtConnected]);

  const deleteFile = async (path: string) => {
    // Optimistic: drop from local list first; restore on failure.
    const prev = files;
    setFiles(files.filter(f => f.file_path !== path));
    if (selectedFile === path) onSelect(null);
    try {
      const encodedPath = path.split("/").map(encodeURIComponent).join("/");
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files/${encodedPath}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        setFiles(prev);  // rollback
        setUploadError(translateHostedAgent('en', 'deleteFailedValue1', { value1: path }));
      }
    } catch {
      setFiles(prev);
      setUploadError(translateHostedAgent('en', 'deleteFailedNetwork'));
    }
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
        // nodeMap changes after loadFiles() → the sync effect produces a new expandedItems
        // reference automatically → headless-tree rebuilds. No explicit flush needed here.
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
    const items: { file_path: string; content: string; file_type: string }[] = [];

    // First pass: filter binary/oversize and read text content. Reading
    // is sequential so the progress counter stays meaningful for users
    // with a slow disk on a folder upload.
    for (let i = 0; i < fileArr.length; i++) {
      const file = fileArr[i];
      const filePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      const name = filePath.split("/").pop() || file.name;
      setUploadProgress({ current: i + 1, total: fileArr.length, name });

      const ext = "." + (filePath.split(".").pop()?.toLowerCase() || "");
      if (BINARY_EXTS.includes(ext)) { skipped.push(tr('skippedBinaryFile', { path: filePath })); continue; }
      if (file.size > MAX_UPLOAD_BYTES) { skipped.push(tr('skippedOversizedFile', { path: filePath, size: new Intl.NumberFormat(localeTag(locale), { maximumFractionDigits: 0 }).format(file.size / 1024) })); continue; }
      try {
        const text = await file.text();
        items.push({
          file_path: filePath,
          content: text,
          file_type: filePath.endsWith(".md") && filePath.toLowerCase().includes("skill") ? "skill" : "text",
        });
      } catch (e) {
        skipped.push(`${filePath} (${e instanceof Error ? e.message : tr('readError')})`);
      }
    }

    let successCount = 0;
    const failed: string[] = [];

    // Second pass: send in batches of UPLOAD_BATCH_SIZE so a 200-file
    // folder upload doesn't block the BE on a single huge transaction.
    const UPLOAD_BATCH_SIZE = 25;
    for (let i = 0; i < items.length; i += UPLOAD_BATCH_SIZE) {
      const chunk = items.slice(i, i + UPLOAD_BATCH_SIZE);
      try {
        const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files/batch`, {
          method: "POST",
          body: JSON.stringify({ files: chunk }),
        });
        if (res.ok) {
          const data = await res.json();
          successCount += (data.written ?? []).length;
          for (const f of data.failed ?? []) failed.push(`${f.file_path} (${f.error})`);
        } else {
          // Whole chunk failed atomically — flag every path in this batch.
          const d = await res.json().catch(() => ({}));
          const msg = typeof d.detail === "string" ? d.detail : tr('errorValue1', { value1: res.status });
          for (const it of chunk) failed.push(`${it.file_path} (${msg})`);
        }
      } catch (e) {
        for (const it of chunk) failed.push(`${it.file_path} (${e instanceof Error ? e.message : "network"})`);
      }
    }

    const problems: string[] = [];
    if (skipped.length) problems.push(tr('skippedValue1Value2Value3', { value1: skipped.length, value2: skipped.slice(0, 3).join(", "), value3: skipped.length > 3 ? "…" : "" }));
    if (failed.length) problems.push(tr('failedValue1Value2Value3', { value1: failed.length, value2: failed.slice(0, 3).join(", "), value3: failed.length > 3 ? "…" : "" }));
    if (problems.length) setUploadError(problems.join(" · "));

    if (successCount > 0) {
      await loadFiles();
      // nodeMap changes after loadFiles() → the sync effect produces a new expandedItems
      // reference automatically → headless-tree rebuilds. No explicit flush needed here.
    }
    setUploading(false);
    setUploadProgress({ current: 0, total: 0, name: "" });
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (folderInputRef.current) folderInputRef.current.value = "";
  };

  const downloadZip = async () => {
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files/download`);
      if (!res.ok) return;
      const disposition = res.headers.get("content-disposition") ?? "";
      // Parse filename from: attachment; filename="foo.zip" or filename*=UTF-8''foo.zip
      const match = disposition.match(/filename\*?=(?:UTF-8''|"?)([^";]+)"?/i);
      const filename = match ? decodeURIComponent(match[1].trim()) : "agent-workspace.zip";
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url;
      a.download = filename; a.click(); URL.revokeObjectURL(url);
    } catch { /* ignore */ }
  };

  // ── Helpers (stable, no deps) ──────────────────────────────────────────────

  const fileIconFor = (f: AgentFile) => {
    const p = f.file_path.toLowerCase();
    const name = p.split("/").pop() || p;
    const ext = name.split(".").pop() || "";
    if (name === "agent.yaml" || name === "agent.yml") return { color: "text-violet-300", char: "⚙" };
    if (name.endsWith("agent.md") || name.endsWith("system.md")) return { color: "text-violet-300", char: "◆" };
    if (name.endsWith("skill.md") || f.file_type === "skill") return { color: "text-cyan-300", char: "◇" };
    if (p.startsWith(".deep/memory") || f.file_type === "memory") return { color: "text-amber-300", char: "◈" };
    if (name === "readme.md") return { color: "text-emerald-300", char: "★" };
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

  const fmtSize = (b: number) => {
    if (b < 1024) return `${b}B`;
    if (b < 1024 * 1024) return `${new Intl.NumberFormat(localeTag(locale), { maximumFractionDigits: 1 }).format(b / 1024)}K`;
    return `${new Intl.NumberFormat(localeTag(locale), { maximumFractionDigits: 1 }).format(b / 1024 / 1024)}M`;
  };

  // ── Tree data ─────────────────────────────────────────────────────────────
  // Each node is either a "file" (leaf) or "folder" (internal).
  // Node IDs: folders use their full dir path ("src/foo"), files use "file:src/foo/bar.py".
  // Root virtual node id = "__root__".

  type TreeNodeData =
    | { kind: "folder"; name: string; fullPath: string; childCount: number }
    | { kind: "file"; name: string; file: AgentFile };

  const totalSize = files.reduce((sum, f) => sum + f.size_bytes, 0);
  const searchLower = search.trim().toLowerCase();
  const visibleFiles = searchLower
    ? files.filter(f => f.file_path.toLowerCase().includes(searchLower))
    : files;

  // Build trie: maps nodeId -> { data, children ids }
  const { nodeMap, rootChildren } = useMemo(() => {
    // Use full visibleFiles so the memo is correct, but also capture them outside
    const nodeData = new Map<string, TreeNodeData>();
    const nodeChildren = new Map<string, string[]>();

    // Ensure root
    nodeChildren.set("__root__", []);

    for (const f of visibleFiles) {
      const parts = f.file_path.split("/");
      // Register all ancestor folders
      for (let depth = 1; depth < parts.length; depth++) {
        const folderPath = parts.slice(0, depth).join("/");
        const folderName = parts[depth - 1];
        const parentPath = depth === 1 ? "__root__" : parts.slice(0, depth - 1).join("/");
        if (!nodeData.has(folderPath)) {
          nodeData.set(folderPath, { kind: "folder", name: folderName, fullPath: folderPath, childCount: 0 });
          nodeChildren.set(folderPath, []);
          const parentChildren = nodeChildren.get(parentPath) ?? [];
          if (!parentChildren.includes(folderPath)) parentChildren.push(folderPath);
          nodeChildren.set(parentPath, parentChildren);
        }
      }
      // Register the file itself
      const fileId = "file:" + f.file_path;
      nodeData.set(fileId, { kind: "file", name: parts[parts.length - 1], file: f });
      nodeChildren.set(fileId, []); // files are leaves
      const parentPath = parts.length === 1 ? "__root__" : parts.slice(0, -1).join("/");
      const parentChildren = nodeChildren.get(parentPath) ?? [];
      if (!parentChildren.includes(fileId)) parentChildren.push(fileId);
      nodeChildren.set(parentPath, parentChildren);
    }

    // Patch folder childCounts
    for (const [id, data] of nodeData.entries()) {
      if (data.kind === "folder") {
        (data as { kind: "folder"; name: string; fullPath: string; childCount: number }).childCount =
          (nodeChildren.get(id) ?? []).length;
      }
    }

    // Sort each folder's children: directories first, then files, alphabetical within each group
    for (const [, kids] of nodeChildren.entries()) {
      kids.sort((a, b) => {
        const aIsFile = a.startsWith("file:");
        const bIsFile = b.startsWith("file:");
        if (aIsFile !== bIsFile) return aIsFile ? 1 : -1;
        const an = nodeData.get(a)?.name ?? "";
        const bn = nodeData.get(b)?.name ?? "";
        return an.localeCompare(bn);
      });
    }

    return {
      nodeMap: { data: nodeData, children: nodeChildren },
      rootChildren: nodeChildren.get("__root__") ?? [],
    };
  }, [visibleFiles]);

  // Default expanded: all top-level dirs (depth-1 folders that are direct children of root)
  const defaultExpanded = useMemo<string[]>(() => {
    return rootChildren.filter(id => !id.startsWith("file:"));
  }, [rootChildren]);

  const [expandedItems, setExpandedItems] = useState<string[]>(defaultExpanded);

  // Escape-hatch: keep the parent's cancelCreateRef pointing at the current reset function.
  // The parent calls this from EditorPanel's onClose (× button) to cancel an in-progress
  // new-file create when the editor is closed while showNewFile is active.
  // Writing to a ref during render is the recommended React escape-hatch for stable callbacks;
  // this is intentional (see https://react.dev/learn/referencing-values-with-refs#best-practices).
  if (cancelCreateRef) {
    cancelCreateRef.current = () => { // eslint-disable-line react-hooks/refs
      setShowNewFile(false);
      setNewFileName("");
    };
  }

  // Sync expandedItems whenever nodeMap changes (covers ALL file mutations: create/delete/upload).
  // headless-tree only calls rebuildItemMeta() when the expandedItems *reference* changes
  // (setConfig uses strict-eq hasChangedExpandedItems). We therefore ALWAYS produce a new array
  // reference here so every nodeMap change (= any visibleFiles change) forces a full rebuild.
  //
  // Strategy: prune stale folder IDs that no longer exist in nodeMap, then merge any newly-
  // appeared dirs from defaultExpanded. The identity bail-out that was here previously was
  // intentionally REMOVED: returning `current` (same reference) when content is unchanged
  // prevented headless-tree from seeing the mutation, which caused ghost folder rows after
  // root-file delete (the deleted item's stale itemInstance rendered via getItem fallback).
  //
  // Anti-loop proof: nodeMap is a useMemo derived from visibleFiles (deps: files + search).
  // setExpandedItems does NOT mutate files or search, so nodeMap is stable after the effect
  // runs — the effect does not re-trigger itself.
  const prevDefaultExpandedRef = useRef<string[]>(defaultExpanded);
  useEffect(() => {
    if (searchLower) return; // search expansion handled by separate effect below
    const prev = prevDefaultExpandedRef.current;
    const added = defaultExpanded.filter(id => !prev.includes(id));
    setExpandedItems(current => {
      // Keep only IDs that still have a live node in nodeMap; add any newly-appeared dirs.
      const live = current.filter(id => nodeMap.data.has(id));
      const merged = [...live];
      for (const id of added) {
        if (!merged.includes(id)) merged.push(id);
      }
      // Always return a new array reference so headless-tree always rebuilds on nodeMap change.
      // Do NOT restore the identity bail-out here — it breaks ghost-row cleanup on root-file delete.
      return merged;
    });
    prevDefaultExpandedRef.current = defaultExpanded;
  }, [defaultExpanded, searchLower, nodeMap]);

  // When search changes, auto-expand all ancestor folders of matching files
  useEffect(() => {
    if (!searchLower) {
      setExpandedItems(defaultExpanded);
      return;
    }
    const toExpand = new Set<string>();
    for (const f of visibleFiles) {
      const parts = f.file_path.split("/");
      for (let d = 1; d < parts.length; d++) {
        toExpand.add(parts.slice(0, d).join("/"));
      }
    }
    setExpandedItems(Array.from(toExpand));
  }, [searchLower]); // eslint-disable-line react-hooks/exhaustive-deps

  const tree = useTree<TreeNodeData>({
    rootItemId: "__root__",
    state: { expandedItems, focusedItem: null },
    setState: patch => {
      if (typeof patch === "function") {
        setExpandedItems(prev => {
          const next = patch({ expandedItems: prev, focusedItem: null });
          return next.expandedItems ?? prev;
        });
      } else {
        if (patch.expandedItems !== undefined) setExpandedItems(patch.expandedItems);
      }
    },
    features: [syncDataLoaderFeature, selectionFeature, hotkeysCoreFeature],
    dataLoader: {
      getItem: (id: string) => {
        if (id === "__root__") return { kind: "folder" as const, name: "root", fullPath: "", childCount: 0 };
        // Return a safe fallback when the id is not in the current nodeMap.
        // This can happen transiently when nodeMap rebuilds after a search change
        // while headless-tree still holds references to old item ids.
        return (nodeMap.data.get(id) ?? { kind: "folder" as const, name: "", fullPath: id, childCount: 0 }) as TreeNodeData;
      },
      getChildren: (id: string) => nodeMap.children.get(id) ?? [],
    },
    isItemFolder: item => item.getItemData()?.kind === "folder",
    getItemName: item => item.getItemData()?.kind === "file"
      ? item.getItemData().name
      : (item.getItemData() as { kind: "folder"; name: string })?.name ?? "",
  });

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
          <span className="text-[11px] font-mono uppercase tracking-[0.15em] text-neutral-600">{tr('files')}</span>
          {visibleFiles.length > 0 && (
            <span className="text-[9px] font-mono text-neutral-700 bg-white/[0.03] px-1.5 py-0.5 rounded">
              {new Intl.NumberFormat(localeTag(locale)).format(visibleFiles.length)} &middot; {fmtSize(totalSize)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => setShowUploadZone(!showUploadZone)}
            className={`p-1 rounded transition-colors ${showUploadZone ? "bg-emerald-400/15 text-emerald-400" : "text-neutral-600 hover:text-emerald-400 hover:bg-emerald-400/[0.06]"}`}
            title={tr('uploadFiles')}>
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
            </svg>
          </button>
          <button onClick={downloadZip}
            className="p-1 rounded text-neutral-600 hover:text-cyan-400 hover:bg-cyan-400/[0.06] transition-colors"
            title={tr('downloadZip')}>
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12M12 16.5V3" />
            </svg>
          </button>
          <button onClick={() => setShowNewFile(!showNewFile)}
            className={`p-1 rounded transition-colors ${showNewFile ? "bg-violet-500/15 text-violet-400" : "text-neutral-600 hover:text-violet-400 hover:bg-violet-500/[0.06]"}`}
            title={tr('newFile')}>
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
          </button>
          <button
            onClick={() => setShowHidden(v => !v)}
            title={showHidden ? tr('hideHiddenFiles') : tr('showHiddenFiles')}
            aria-label={showHidden ? tr('hideHiddenFiles') : tr('showHiddenFiles')}
            className={`p-1 rounded transition-colors ${showHidden ? "bg-amber-400/15 text-amber-400" : "text-neutral-600 hover:text-amber-400 hover:bg-amber-400/[0.06]"}`}
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              {showHidden
                ? <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                : <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
              }
            </svg>
          </button>
        </div>
      </div>

      {/* Search */}
      {files.length > 3 && (
        <div className="px-2 py-1.5 border-b border-neutral-800/40 shrink-0 flex items-center gap-1.5">
          <svg className="w-3 h-3 text-neutral-600 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
          </svg>
          <input type="text" value={search} onChange={e => setSearch(e.target.value)}
            placeholder={tr('filterFiles')}
            className="flex-1 bg-transparent text-[11px] font-mono text-white placeholder:text-neutral-600 focus:outline-none" />
          {search && (
            <button aria-label={tr('clearSearch')} onClick={() => setSearch("")} className="text-neutral-600 hover:text-neutral-400 text-xs shrink-0">×</button>
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
            <span className="text-[10px] font-mono text-emerald-400/60 group-hover:text-emerald-400/90">{tr('clickToUploadFiles')}</span>
          </button>
          <button onClick={() => folderInputRef.current?.click()} disabled={uploading}
            className="w-full flex items-center justify-center gap-1.5 py-1.5 text-[10px] font-mono text-neutral-500 hover:text-violet-400 border border-neutral-800/30 rounded-lg hover:bg-violet-500/[0.04] hover:border-violet-500/20 disabled:opacity-40 transition-colors">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
            </svg>
            {tr('uploadFolder')}</button>
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
            <button onClick={createFile} className="flex-1 text-[9px] font-mono bg-violet-500/10 text-violet-400 rounded py-0.5 hover:bg-violet-500/20">{tr('create')}</button>
            <button onClick={() => { setShowNewFile(false); setNewFileName(""); }} className="text-[9px] font-mono text-neutral-600 px-1.5">{tr('cancel')}</button>
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
            <span className="text-[9px] font-mono text-emerald-400/60">{new Intl.NumberFormat(localeTag(locale)).format(uploadProgress.current)}/{new Intl.NumberFormat(localeTag(locale)).format(uploadProgress.total)}</span>
          </div>
          <div className="h-1 bg-white/[0.04] rounded-full overflow-hidden">
            <div className="h-full bg-emerald-400/50 rounded-full transition-all duration-300" style={{ width: `${(uploadProgress.current / uploadProgress.total) * 100}%` }} />
          </div>
        </div>
      )}

      {/* Upload error */}
      {uploadError && (
        <div className="px-2.5 py-1.5 border-b border-neutral-800/40 shrink-0 flex items-center justify-between">
          <span className="text-[10px] font-mono text-red-400/90 truncate">{displayHostedAgentText(locale, uploadError)}</span>
          <button aria-label={tr('close')} onClick={() => setUploadError(null)} className="text-red-400/50 hover:text-red-400 text-xs ml-2 shrink-0">×</button>
        </div>
      )}

      {/* Tree */}
      <div className="flex-1 overflow-y-auto p-1.5" {...tree.getContainerProps(tr('agentFiles'))}>
        {visibleFiles.length === 0 && !uploading ? (
          <div className="flex flex-col items-center justify-center py-10 px-4 gap-3">
            <svg className="w-10 h-10 text-neutral-800" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={0.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
            </svg>
            <p className="text-[10px] font-mono text-neutral-700 text-center">{tr('noFilesYet')}</p>
            <div className="flex flex-col gap-1.5 w-full max-w-[140px]">
              <button onClick={() => setShowUploadZone(true)}
                className="flex items-center justify-center gap-1.5 py-1.5 text-[10px] font-mono bg-emerald-400/[0.08] text-emerald-400/80 border border-emerald-400/15 rounded-lg hover:bg-emerald-400/15 transition-colors">
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
                {tr('uploadFiles')}</button>
              <button onClick={() => setShowNewFile(true)}
                className="text-[10px] font-mono text-neutral-600 hover:text-violet-400 transition-colors">
                {tr('orCreateANewFile')}</button>
            </div>
          </div>
        ) : (
          tree.getItems().map(item => {
            const data = item.getItemData();
            const level = item.getItemMeta().level;
            const paddingLeft = 4 + level * 14;

            if (data?.kind === "folder") {
              const isExpanded = item.isExpanded();
              const childCount = (data as { kind: "folder"; name: string; fullPath: string; childCount: number }).childCount;
              return (
                <div key={item.getId()} {...item.getProps()}
                  onClick={() => { item.isExpanded() ? item.collapse() : item.expand(); }}
                  className="flex items-center gap-1.5 py-1 text-xs font-mono text-neutral-500 hover:text-neutral-300 rounded hover:bg-white/[0.02] cursor-pointer select-none"
                  style={{ paddingLeft, paddingRight: "4px" }}>
                  <svg
                    className={`w-3 h-3 text-neutral-600 shrink-0 transition-transform duration-100 ${isExpanded ? "rotate-90" : ""}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                  </svg>
                  <svg className="w-3 h-3 text-neutral-600 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    {isExpanded
                      ? <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                      : <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                    }
                  </svg>
                  <span className="truncate text-[11px]">{data.name}</span>
                  <span className="text-[9px] text-neutral-700 ml-auto shrink-0">{new Intl.NumberFormat(localeTag(locale)).format(childCount)}</span>
                </div>
              );
            }

            if (data?.kind === "file") {
              const icon = fileIconFor(data.file);
              const filePath = data.file.file_path;
              return (
                <div key={item.getId()} {...item.getProps()}
                  onClick={() => onSelect(filePath)}
                  className={`flex items-center justify-between py-1.5 rounded cursor-pointer text-xs font-mono transition-colors group ${
                    selectedFile === filePath
                      ? "bg-violet-500/[0.12] text-violet-200 border-l-2 border-violet-400/60"
                      : "text-neutral-400 hover:bg-white/[0.03] hover:text-neutral-300 border-l-2 border-transparent"
                  }`}
                  style={{ paddingLeft, paddingRight: "4px" }}>
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className={`shrink-0 inline-flex items-center justify-center w-4 h-4 rounded text-[9px] font-mono ${icon.color} bg-white/[0.03] border border-white/[0.04]`}>
                      {icon.char}
                    </span>
                    <span className="truncate">{data.name}</span>
                    <span className="text-[9px] text-neutral-700 ml-1 shrink-0">{fmtSize(data.file.size_bytes)}</span>
                  </div>
                  {confirmDelete === filePath ? (
                    <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
                      <button onClick={() => { deleteFile(filePath); setConfirmDelete(null); }}
                        className="text-[11px] text-red-400 hover:text-red-300 bg-red-400/10 px-1.5 py-0.5 rounded">{tr('del')}</button>
                      <button aria-label={tr('cancel')} onClick={() => setConfirmDelete(null)}
                        className="text-[11px] text-neutral-600 px-1">×</button>
                    </div>
                  ) : (
                    <button aria-label={tr('deleteFile')} onClick={e => { e.stopPropagation(); setConfirmDelete(filePath); }}
                      className="text-neutral-700 hover:text-red-400 opacity-0 group-hover:opacity-100 text-sm px-1 shrink-0">×</button>
                  )}
                </div>
              );
            }

            return null;
          })
        )}
      </div>

      {/* Full-panel drag overlay */}
      {dragOver && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-black/60 backdrop-blur-sm rounded-xl">
          <svg className="w-8 h-8 text-violet-400/80 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
          </svg>
          <p className="text-xs font-mono text-violet-300/90">{tr('dropFilesToUpload')}</p>
          <p className="text-[9px] font-mono text-neutral-500 mt-1">{tr('filesOrFolders')}</p>
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
  const { locale } = useLocale();
  const tr = useTranslations(hostedAgentMessages);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [loadedVersion, setLoadedVersion] = useState<string>("");
  const [conflict, setConflict] = useState<{ currentVersion: string; currentContent: string } | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [isBinary, setIsBinary] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setDirty(false);
    setError("");
    setConflict(null);
    (async () => {
      try {
        const encodedFilePath = filePath.split("/").map(encodeURIComponent).join("/");
        const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files/${encodedFilePath}`);
        if (!cancelled) {
          if (res.ok) {
            const f: AgentFile & { version?: string; truncated?: boolean; is_binary?: boolean } = await res.json();
            setContent(f.content || "");
            setLoadedVersion(f.version ?? "");
            setTruncated(!!f.truncated);
            setIsBinary(!!f.is_binary);
          } else {
            setError(translateHostedAgent('en', 'failedToLoadValue1', { value1: res.status }));
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : translateHostedAgent('en', 'failedToLoad'));
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [agentId, filePath]);

  const save = useCallback(async (overrideContent?: string, force = false) => {
    setSaving(true);
    setError("");
    // Optimistic: clear dirty immediately so the user can keep typing.
    // Rolled back on 4xx below.
    const prevDirty = dirty;
    setDirty(false);
    const headers: Record<string, string> = {};
    if (loadedVersion && !force) headers["If-Match"] = `"${loadedVersion}"`;
    try {
      const body = overrideContent !== undefined ? overrideContent : content;
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agentId}/files`, {
        method: "PUT",
        headers,
        body: JSON.stringify({ file_path: filePath, content: body }),
      });
      if (res.status === 412) {
        const data = await res.json().catch(() => ({}));
        setConflict({
          currentVersion: data.current_version ?? "",
          currentContent: data.current_content ?? "",
        });
        setDirty(prevDirty);
      } else if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(typeof data.detail === "string" ? data.detail : translateHostedAgent('en', 'saveFailedValue1', { value1: res.status }));
        setDirty(prevDirty);
      } else {
        const data = await res.json().catch(() => ({}));
        if (data.version) setLoadedVersion(data.version);
        setToast(translateHostedAgent('en', 'saved'));
        setTimeout(() => setToast(""), 2000);
      }
    } catch {
      setError(translateHostedAgent('en', 'networkError'));
      setDirty(prevDirty);
    }
    setSaving(false);
  }, [agentId, filePath, content, loadedVersion, dirty]);

  const acceptTheirs = () => {
    if (!conflict) return;
    setContent(conflict.currentContent);
    setLoadedVersion(conflict.currentVersion);
    setDirty(false);
    setConflict(null);
  };

  const overwrite = async () => {
    if (!conflict) return;
    // Bump our known version to the server's current so the next save
    // doesn't 412 again.
    const currentVersion = conflict.currentVersion;
    setLoadedVersion(currentVersion);
    setConflict(null);
    await save(undefined, true);
  };

  const fileName = filePath.split("/").pop() || filePath;
  const FILE_HINTS: Record<string, string> = {
    "AGENT.md": tr('systemPromptYourAgentSCoreInstructions'),
    "SKILL.md": tr('platformAPIReferenceAutoLoadedByAgent'),
    ".deep/memory/main/MEMORY.md": tr('persistentMemoryAcrossSessions'),
  };

  return (
    <div className="h-full flex flex-col bg-white/[0.02] border border-neutral-800/50 rounded-xl overflow-hidden relative">
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
          {toast && <span className="text-[9px] font-mono text-emerald-400 animate-fade-in">{displayHostedAgentText(locale, toast)}</span>}
          {error && <span className="text-[9px] font-mono text-red-400 truncate max-w-[200px]" title={displayHostedAgentText(locale, error)}>{displayHostedAgentText(locale, error)}</span>}
          <button onClick={() => save()} disabled={saving || !dirty || truncated || isBinary}
            className="px-2.5 py-1 text-[10px] font-mono bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 rounded hover:bg-emerald-400/20 disabled:opacity-30 transition-colors">
            {saving ? "…" : tr('save')}
          </button>
          <button aria-label={tr('close')} onClick={onClose} className="text-neutral-600 hover:text-neutral-400 text-sm transition-colors" title={tr('closeEditor')}>×</button>
        </div>
      </div>

      {/* Hint */}
      {FILE_HINTS[filePath] && (
        <div className="px-3 py-1 border-b border-neutral-800/30 shrink-0">
          <span className="text-[9px] font-mono text-neutral-600">{FILE_HINTS[filePath]}</span>
        </div>
      )}

      {/* Truncated / binary banner — read-only mode */}
      {(truncated || isBinary) && (
        <div className="px-3 py-2 border-b border-amber-400/20 bg-amber-400/[0.04] shrink-0">
          <span className="text-[10px] font-mono text-amber-300/90">
            {isBinary
              ? tr('binaryFileContentNotDisplayedDownloadViaThe')
              : tr('fileTooLargeToEdit500KBDownloadVia')}
          </span>
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
            onSave={() => save()}
            filePath={filePath}
          />
        </Suspense>
      )}

      {/* Conflict modal — agent edited the file while user was typing */}
      {conflict && (
        <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="w-[480px] max-w-[90vw] bg-neutral-950 border border-amber-400/30 rounded-xl p-5 shadow-2xl">
            <h3 className="text-sm font-mono text-amber-300 mb-2">{tr('fileChangedByAgent')}</h3>
            <p className="text-[11px] font-mono text-neutral-400 leading-relaxed mb-4">
              {tr('theAgentEdited')}{' '}<span className="text-neutral-200">{filePath}</span> {tr('whileYouWereTypingYourVersionIsBased')}{' '}{loadedVersion.slice(0, 8) || tr('unknownVersion')}{tr('currentIs')}{' '}{conflict.currentVersion.slice(0, 8) || tr('unknownVersion')}{tr('chooseHowToResolveTheConflict')}</p>
            <div className="bg-white/[0.02] border border-neutral-800/50 rounded p-2 mb-4 max-h-[180px] overflow-auto">
              <pre className="text-[10px] font-mono text-neutral-400 whitespace-pre-wrap">
                {(conflict.currentContent || "").slice(0, 800)}
                {(conflict.currentContent || "").length > 800 ? "…" : ""}
              </pre>
            </div>
            <div className="flex items-center justify-end gap-2">
              <button onClick={() => setConflict(null)}
                className="px-3 py-1.5 text-[10px] font-mono text-neutral-500 hover:text-neutral-300">
                {tr('cancel')}</button>
              <button onClick={acceptTheirs}
                className="px-3 py-1.5 text-[10px] font-mono bg-cyan-400/10 text-cyan-300 border border-cyan-400/20 rounded hover:bg-cyan-400/20">
                {tr('useAgentSVersion')}</button>
              <button onClick={overwrite}
                className="px-3 py-1.5 text-[10px] font-mono bg-red-400/10 text-red-300 border border-red-400/20 rounded hover:bg-red-400/20">
                {tr('overwriteWithMine')}</button>
            </div>
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="px-3 py-1 border-t border-neutral-800/30 flex items-center justify-between shrink-0">
        <span className="text-[9px] font-mono text-neutral-700">
          {new Intl.NumberFormat(localeTag(locale)).format(content.split("\n").length)} {tr('lines')}{new Intl.NumberFormat(localeTag(locale), { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format((content.length / 1024))} {tr('kB')}</span>
        <div className="flex items-center gap-3">
          {loadedVersion && (
            <span className="text-[9px] font-mono text-neutral-700" title={tr('versionValue1', { value1: loadedVersion })}>
              v: {loadedVersion.slice(0, 7)}
            </span>
          )}
          <span className="text-[9px] font-mono text-neutral-700">{tr('ctrlSToSave')}</span>
        </div>
      </div>
    </div>
  );
}

/* ── Tool call display helpers ── */



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



function ToolCallDisplay({ tool, args, status, result, agentId }: { tool: string; args: unknown; status: string; result?: string; agentId?: string }) {
  const { locale } = useLocale();
  const tr = useTranslations(hostedAgentMessages);
const TOOL_LABELS: Record<string, { icon: string; label: string; color: string }> = {
  execute: { icon: "▸", label: tr('runningCommand'), color: "text-amber-300/80" },
  write_file: { icon: "✎", label: tr('writingFile'), color: "text-emerald-300/80" },
  read_file: { icon: "◉", label: tr('readingFile'), color: "text-blue-300/80" },
  hashline_edit: { icon: "✎", label: tr('editingFile'), color: "text-emerald-300/80" },
  fetch_url: { icon: "↗", label: tr('fetchingURL'), color: "text-cyan-300/80" },
  search: { icon: "⌕", label: tr('searching'), color: "text-violet-300/80" },
  todo: { icon: "☐", label: tr('task'), color: "text-neutral-300/80" },
  write_todos: { icon: "☐", label: tr('writingTasks'), color: "text-neutral-300/80" },
  add_todo: { icon: "☐", label: tr('addingTask'), color: "text-neutral-300/80" },
  update_todo_status: { icon: "☑", label: tr('updatingTask'), color: "text-neutral-300/80" },
  memory_write: { icon: "◈", label: tr('savingMemory'), color: "text-amber-200/80" },
  memory_read: { icon: "◇", label: tr('readingMemory'), color: "text-amber-200/80" },
};
const DONE_LABELS: Record<string, string> = {
  [tr('runningCommand')]: tr('ranCommand'),
  [tr('writingFile')]: tr('wroteFile'),
  [tr('readingFile')]: tr('readFile'),
  [tr('editingFile')]: tr('editedFile'),
  [tr('fetchingURL')]: tr('fetchedURL'),
  [tr('searching')]: tr('searched'),
  [tr('savingMemory')]: tr('savedMemory'),
  [tr('readingMemory')]: tr('readMemory'),
  [tr('writingTasks')]: tr('wroteTasks'),
  [tr('addingTask')]: tr('addedTask'),
  [tr('updatingTask')]: tr('updatedTask'),
};
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
          <div className="text-[10px] text-neutral-600 mb-1 uppercase tracking-wider">{tr('output')}</div>
          <div className={`text-neutral-500 text-[11px] bg-emerald-500/[0.04] border border-emerald-500/10 px-2.5 py-1.5 rounded whitespace-pre-wrap break-all leading-relaxed ${expanded ? "max-h-[500px]" : "max-h-24"} overflow-y-auto`}>
            {result}
          </div>
        </div>
      )}
      {result && !expanded && result.length > 200 && (
        <div className="px-3 pb-2">
          <button onClick={() => setExpanded(true)} className="text-[10px] text-cyan-500/60 hover:text-cyan-400/80 transition-colors">
            {tr('showOutput')}{new Intl.NumberFormat(localeTag(locale)).format(Math.ceil(result.length / 1000))}{tr('kChars')}</button>
        </div>
      )}
      {showDiff && <ChatDiffPreview agentId={agentId!} path={editedPath} />}
    </div>
  );
}

/* ── Chat inline diff preview ── */

function ChatDiffPreview({ agentId, path }: { agentId: string; path: string }) {
  const tr = useTranslations(hostedAgentMessages);
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
        if (!res.ok) throw new Error(translateHostedAgent('en', 'hTTPValue1', { value1: res.status }));
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

  if (loading) return <div className="text-[10px] text-neutral-600 px-3 pb-2 font-mono">{tr('loadingDiff')}</div>;
  if (err) return <div className="text-[10px] text-red-400/70 px-3 pb-2 font-mono">{err}</div>;
  if (!file) return <div className="text-[10px] text-neutral-600 px-3 pb-2 font-mono italic">{tr('noPendingChangesFor')}{path} {tr('alreadyCommittedOrIdentical')}</div>;

  return (
    <div className="px-3 pb-3 pt-1">
      <Suspense fallback={<div className="text-[10px] text-neutral-600 font-mono">{tr('loadingDiffViewer')}</div>}>
        <DiffViewer files={[file]} />
      </Suspense>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════ */
/* Chat Panel                                                                 */
/* ═══════════════════════════════════════════════════════════════════════════ */

function ChatPanel({ agentId, status, onNewMessage, onRequestForceRestart }: { agentId: string; status: string; onNewMessage?: () => void; onRequestForceRestart?: () => void }) {
  const { locale } = useLocale();
  const tr = useTranslations(hostedAgentMessages);
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
  const [streamPhase, setStreamPhase] = useState<"idle" | "starting_agent" | "connecting" | "waiting" | "streaming">("idle");
  const [startingEtaS, setStartingEtaS] = useState(15);
  const [startingElapsedS, setStartingElapsedS] = useState(0);
  const [coldStartError, setColdStartError] = useState<{ message: string; retryable: boolean } | null>(null);
  const startingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastSentRef = useRef<string | null>(null);
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

  // Chat drag-drop upload
  const [chatDragOver, setChatDragOver] = useState(false);
  const [chatUploadMsg, setChatUploadMsg] = useState<string | null>(null);
  const chatUploadMsgTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const TEXT_EXTS = new Set([".md", ".txt", ".py", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml", ".csv", ".log", ".sh", ".sql"]);

  const chatUploadFiles = async (fileList: FileList) => {
    if (fileList.length === 0) return;
    const files = Array.from(fileList);
    let uploaded = 0;
    const binaryNames: string[] = [];

    for (const file of files) {
      const ext = "." + (file.name.split(".").pop()?.toLowerCase() ?? "");
      const isText = file.type.startsWith("text/") || TEXT_EXTS.has(ext);
      if (!isText) { binaryNames.push(file.name); continue; }

      try {
        const content = await file.text();
        await fetch(`/api/v1/hosted-agents/${agentId}/files`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_path: file.name, content, file_type: "text" }),
        });
        uploaded++;
      } catch {
        binaryNames.push(file.name + tr('uploadError'));
      }
    }

    if (chatUploadMsgTimerRef.current) clearTimeout(chatUploadMsgTimerRef.current);
    if (binaryNames.length > 0 && uploaded === 0) {
      setChatUploadMsg(translateHostedAgent('en', 'binaryFilesNotSupportedYet'));
    } else if (binaryNames.length > 0) {
      setChatUploadMsg(translateHostedAgent('en', 'uploadedValue1FileValue2Value3SkippedBinary', { value1: uploaded, value2: uploaded !== 1 ? "s" : "", value3: binaryNames.length }));
    } else {
      setChatUploadMsg(translateHostedAgent('en', 'uploadedValue1FileValue2', { value1: uploaded, value2: uploaded !== 1 ? "s" : "" }));
    }
    chatUploadMsgTimerRef.current = setTimeout(() => setChatUploadMsg(null), 3000);
  };

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
    if (startingTimerRef.current != null) clearInterval(startingTimerRef.current);
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
        const sorted = [...data].reverse();
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
        setChatError(data.detail || translateHostedAgent('en', 'rewindFailedValue1', { value1: res.status }));
        return;
      }
      setShowCheckpoints(false);
      await loadMessages();
    } catch {
      setChatError(translateHostedAgent('en', 'networkErrorDuringRewind'));
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
        setChatError(data.detail || translateHostedAgent('en', 'clearFailedValue1', { value1: res.status }));
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
      setChatError(translateHostedAgent('en', 'networkErrorDuringClear'));
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
    lastSentRef.current = text;

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
    setColdStartError(null);
    setStreamText("");
    setStreamTools([]);
    setStreamThinking("");
    setStreamPhase("connecting");
    setSendElapsed(0);
    setStartingElapsedS(0);
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
        setChatError(data.detail || tr('errorValue1', { value1: res.status }));
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
              case "phase":
                switch (event.phase) {
                  case "starting_agent":
                    setStreamPhase("starting_agent");
                    setStartingEtaS(event.eta_s ?? 15);
                    setStartingElapsedS(0);
                    if (startingTimerRef.current) clearInterval(startingTimerRef.current);
                    startingTimerRef.current = setInterval(() => setStartingElapsedS(p => p + 1), 1000);
                    break;
                  case "agent_started":
                    if (startingTimerRef.current) { clearInterval(startingTimerRef.current); startingTimerRef.current = null; }
                    setStreamPhase("connecting");
                    break;
                  default:
                    break;
                }
                break;
              case "error":
                if (event.phase === "starting_agent") {
                  setColdStartError({ message: event.message ?? tr('unknownError'), retryable: event.retryable ?? false });
                  if (startingTimerRef.current) { clearInterval(startingTimerRef.current); startingTimerRef.current = null; }
                  setStreamPhase("idle");
                } else {
                  setChatError(event.message);
                }
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
          content: streamTextRef.current || tr('responseIncomplete'),
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
            content: streamTextRef.current || tr('generationStopped'),
            thinking: streamThinkingRef.current || undefined,
            tool_calls: streamToolsRef.current.length > 0 ? streamToolsRef.current as OwnerMessage["tool_calls"] : undefined,
            edited_at: null,
            is_deleted: false,
            created_at: new Date().toISOString(),
          };
          setMessages(prev => [...prev, partial]);
        }
      } else {
        setChatError(tr('networkErrorCheckYourConnection'));
      }
    }
    abortRef.current = null;
    setSending(false);
    setStreamPhase("idle");
    if (sendTimerRef.current) { clearInterval(sendTimerRef.current); sendTimerRef.current = null; }
    if (startingTimerRef.current) { clearInterval(startingTimerRef.current); startingTimerRef.current = null; }
    setStreamText("");
    setStreamTools([]);
    setStreamThinking("");
  };

  const toggle = (set: Set<string>, id: string) => { const n = new Set(set); n.has(id) ? n.delete(id) : n.add(id); return n; };

  const SUGGESTIONS = [
    tr('createAPythonScriptThatFetchesDataFrom'),
    tr('listYourAvailableToolsAndSkills'),
    tr('writeAFunctionToParseCSVFiles'),
    tr('helpMeBuildASimpleWebScraper'),
  ];

  const filteredMessages = search
    ? messages.filter(m => m.content.toLowerCase().includes(search.toLowerCase()))
    : messages;

  const isStreaming = sending && (streamText || streamThinking || streamTools.length > 0);

  return (
    <div
      className={`h-full flex flex-col bg-white/[0.02] border rounded-xl overflow-hidden relative transition-colors ${chatDragOver ? "border-violet-500/40 bg-violet-500/[0.04]" : "border-neutral-800/50"}`}
      onDragOver={e => { e.preventDefault(); e.stopPropagation(); setChatDragOver(true); }}
      onDragLeave={e => { e.preventDefault(); e.stopPropagation(); setChatDragOver(false); }}
      onDrop={e => { e.preventDefault(); e.stopPropagation(); setChatDragOver(false); void chatUploadFiles(e.dataTransfer.files); }}>

      {/* Chat upload toast */}
      {chatUploadMsg && (
        <div className="absolute top-10 right-3 z-30 px-3 py-1.5 text-[11px] font-mono bg-black/80 text-violet-200 border border-violet-400/30 rounded-lg shadow-lg backdrop-blur-sm">
          {displayHostedAgentText(locale, chatUploadMsg)}
        </div>
      )}

      {/* Chat drag overlay */}
      {chatDragOver && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-black/60 backdrop-blur-sm rounded-xl">
          <svg className="w-8 h-8 text-violet-400/80 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
          </svg>
          <p className="text-xs font-mono text-violet-300/90">{tr('dropFilesToUpload')}</p>
          <p className="text-[9px] font-mono text-neutral-500 mt-1">{tr('textFilesOnly')}</p>
        </div>
      )}

      {/* Header */}
      <div className="px-4 py-2 border-b border-neutral-800/40 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-mono uppercase tracking-[0.15em] text-neutral-600">{tr('chat')}</span>
          <span className="text-xs font-mono text-neutral-700">
            {status === "running" ? `🟢 ${tr('statusOnline')}` : `⭘ ${tr('statusOffline')}`}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {status === "running" && (
            <div className="relative">
              <button
                onClick={() => { const next = !showCheckpoints; setShowCheckpoints(next); if (next) loadCheckpoints(); }}
                className="text-xs font-mono text-neutral-600 hover:text-neutral-300 transition-colors px-2 py-1 border border-neutral-800/40 rounded"
                title={tr('rewindToAPreviousCheckpoint')}
                disabled={!!rewinding}>
                {tr('rewind')}</button>
              {showCheckpoints && (
                <div className="absolute right-0 top-full mt-1 w-80 max-h-96 overflow-y-auto bg-[#0d0d0d] border border-neutral-800/60 rounded-lg shadow-2xl z-50">
                  <div className="px-3 py-2 border-b border-neutral-800/40 flex items-center justify-between">
                    <span className="text-[10px] font-mono uppercase tracking-wider text-neutral-500">{tr('checkpoints2')}</span>
                    <button aria-label={tr('close')} onClick={() => setShowCheckpoints(false)} className="text-neutral-600 hover:text-neutral-400 text-sm">×</button>
                  </div>
                  {checkpointsLoading ? (
                    <div className="p-3 text-xs font-mono text-neutral-600">{tr('loading2')}</div>
                  ) : checkpoints.length === 0 ? (
                    <div className="p-3 text-xs font-mono text-neutral-600">
                      {tr('noCheckpointsYetTheyAreRecordedTurnBy')}</div>
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
                                {cp.label || tr('turnValue1', { value1: cp.turn })}
                              </span>
                              <span className="text-[10px] font-mono text-neutral-600">
                                {new Intl.NumberFormat(localeTag(locale)).format(cp.message_count)} {tr('msgs')}</span>
                            </div>
                            <div className="text-[10px] font-mono text-neutral-600 mt-0.5">
                              {cp.created_at ? timeAgo(cp.created_at, locale) : ""}
                              {rewinding === cp.id && ` · ${tr('rewinding')}`}
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
                {clearing ? tr('clearing') : tr('yesStartNew')}
              </button>
              <button onClick={() => setConfirmClear(false)} disabled={clearing}
                className="text-xs font-mono px-2 py-1 text-neutral-500 hover:text-neutral-300">{tr('cancel')}</button>
            </div>
          ) : (
            <button onClick={() => setConfirmClear(true)}
              className="text-xs font-mono text-neutral-600 hover:text-neutral-300 transition-colors px-2 py-1 border border-neutral-800/40 rounded"
              title={tr('hideAllMessagesAndStartAFreshSession')}>
              {tr('newSession')}</button>
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
            placeholder={tr('search')} autoFocus
            className="w-full bg-white/[0.03] border border-neutral-800/50 rounded px-3 py-1 text-xs font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30" />
        </div>
      )}

      {/* Generation warning banner */}
      {sending && (
        <div className="mx-4 mt-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center gap-2">
          <div className="w-1.5 h-1.5 bg-amber-400 rounded-full animate-pulse" />
          <span className="text-[11px] text-amber-300/80 font-mono">{tr('agentIsGeneratingDoNotRefreshThePage')}</span>
        </div>
      )}

      {/* Todos panel */}
      {todos.length > 0 && todos.some(t => t.status !== "completed") && (
        <div className="mx-4 mt-2 border border-neutral-800/40 rounded-lg overflow-hidden shrink-0">
          <button onClick={() => setTodosOpen(!todosOpen)}
            className="w-full px-3 py-1.5 flex items-center justify-between hover:bg-violet-500/[0.04] transition-colors">
            <span className="text-[11px] font-mono text-cyan-400/70">
              ☐ {new Intl.NumberFormat(localeTag(locale)).format(todos.filter(t => t.status === "completed").length)}/{new Intl.NumberFormat(localeTag(locale)).format(todos.length)} {tr('tasks2')}</span>
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

      {/* Cold-start banner */}
      {streamPhase === "starting_agent" && (
        <div
          role="status"
          aria-live="polite"
          className="mx-4 mb-1 shrink-0 flex items-center gap-3 px-3.5 py-2 rounded-lg bg-yellow-500/[0.08] border border-yellow-500/20 text-yellow-400 text-xs font-mono">
          <span className="w-3.5 h-3.5 border border-yellow-400 border-t-transparent rounded-full animate-spin shrink-0" aria-hidden="true" />
          <span className="whitespace-nowrap">{tr('wakingUpYourAgent')}{new Intl.NumberFormat(localeTag(locale)).format(startingElapsedS)}{tr('s')}{new Intl.NumberFormat(localeTag(locale)).format(startingEtaS)}{tr('s2')}</span>
          <div className="flex-1 min-w-0 h-1 bg-yellow-500/10 rounded-full overflow-hidden">
            <div
              className="h-full bg-yellow-400/50 rounded-full transition-all duration-1000"
              style={{ width: `${Math.min(100, (startingElapsedS / startingEtaS) * 100)}%` }}
            />
          </div>
        </div>
      )}

      {/* Cold-start error banner */}
      {coldStartError && (
        <div
          role="alert"
          aria-live="assertive"
          className="mx-4 mb-1 shrink-0 px-3.5 py-2 rounded-lg bg-red-500/[0.08] border border-red-500/20 text-xs font-mono flex items-center gap-2 flex-wrap">
          <span className="text-red-400 flex-1">⚠ {displayHostedAgentText(locale, coldStartError.message)}</span>
          {coldStartError.retryable && lastSentRef.current && (
            <button
              onClick={() => { setColdStartError(null); send(undefined, lastSentRef.current!); }}
              className="px-2.5 py-1 text-[10px] font-mono text-violet-300 bg-violet-500/10 border border-violet-500/20 rounded hover:bg-violet-500/20 transition-colors shrink-0">
              {tr('retry')}</button>
          )}
          {!coldStartError.retryable && (
            <button
              onClick={() => { setColdStartError(null); onRequestForceRestart?.(); }}
              className="px-2.5 py-1 text-[10px] font-mono text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded hover:bg-amber-500/20 transition-colors shrink-0">
              {tr('forceRestart')}</button>
          )}
          <button aria-label={tr('close')} onClick={() => setColdStartError(null)} className="text-red-400/40 hover:text-red-400 shrink-0">×</button>
        </div>
      )}

      {/* Messages */}
      <div ref={chatContainerRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !sending && (
          <div className="text-center py-10">
            <div className="text-2xl opacity-20 mb-3">◇</div>
            {status === "running" ? (
              <div className="space-y-4">
                <p className="text-neutral-500 text-xs font-mono">{tr('yourAgentIsReadyTryAsking')}</p>
                <div className="flex flex-wrap gap-2 justify-center max-w-lg mx-auto">
                  {SUGGESTIONS.map((s, i) => (
                    <button key={i} onClick={() => { setContent(s); textareaRef.current?.focus(); }}
                      className="text-[10px] font-mono text-violet-400/70 bg-violet-500/[0.06] border border-violet-500/10 rounded-lg px-3 py-1.5 hover:bg-violet-500/10 hover:text-violet-300 transition-colors text-left">
                      {s}
                    </button>
                  ))}
                </div>
                <p className="text-neutral-700 text-[10px] font-mono">{tr('privateChatOnlyYouCanSeeIt')}</p>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-neutral-500 text-xs font-mono">{tr('agentIsStopped')}</p>
                <p className="text-neutral-700 text-[10px] font-mono">{tr('pressStartAboveToBegin')}</p>
              </div>
            )}
          </div>
        )}

        {filteredMessages.map(m => m.sender_type === "system" ? (
          <div key={m.id} className="flex justify-center py-0.5">
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-neutral-800/20 border border-neutral-800/30">
              <span className="text-[10px] font-mono text-neutral-500">{m.content}</span>
              <span className="text-[9px] font-mono text-neutral-700">{timeAgo(m.created_at, locale)}</span>
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
                <span className="italic text-neutral-600 text-xs px-3.5 py-2.5 block">{tr('deleted')}</span>
              ) : (
                <>
                  {m.thinking && (
                    <button onClick={() => setExpandedThinking(s => toggle(s, m.id))}
                      className="w-full text-left px-3.5 py-1.5 border-b border-violet-500/10 flex items-center gap-2 hover:bg-violet-500/[0.06] transition-colors">
                      <span className="text-[10px] text-amber-400/80">{tr('thinking2')}</span>
                      <span className="text-[9px] text-neutral-600 tabular-nums">{m.thinking.length.toLocaleString(localeTag(locale))} {tr('chars')}</span>
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
                      <span className="text-[9px] text-neutral-600">{timeAgo(m.created_at, locale)}</span>
                      {m.sender_type === "agent" && m.content && (
                        <CopyButton text={m.content} className="opacity-0 group-hover/msg:opacity-100" />
                      )}
                    </div>
                  </div>

                  {m.tool_calls && m.tool_calls.length > 0 && (
                    <div className="border-t border-violet-500/10">
                      <button onClick={() => setExpandedTools(s => toggle(s, m.id))}
                        className="w-full text-left px-3.5 py-2 flex items-center gap-2 hover:bg-violet-500/[0.06] transition-colors">
                        <span className="text-xs text-cyan-400/80 shrink-0">⚡ {new Intl.NumberFormat(localeTag(locale)).format(m.tool_calls.length)}</span>
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
                    <span className="text-[10px] text-amber-400/70">{tr('thinking2')}</span>
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
                  {streamPhase === "starting_agent" ? tr('wakingUpAgent') : streamPhase === "connecting" ? tr('connecting') : streamPhase === "waiting" ? tr('waitingForModel') : tr('thinking3')}
                </span>
                <span className="text-[9px] text-neutral-600 font-mono tabular-nums">{new Intl.NumberFormat(localeTag(locale)).format(sendElapsed)}{tr('s2')}</span>
              </div>
            </div>
          </div>
        )}

        {chatError && (
          <div className="flex justify-center">
            <div className="px-3.5 py-2 rounded-xl bg-red-400/[0.06] border border-red-400/15 text-xs font-mono text-red-400/80 flex items-center gap-2 flex-wrap">
              <span>⚠ {displayHostedAgentText(locale, chatError)}</span>
              {lastSent && !sending && (
                <button onClick={() => { setChatError(null); send(undefined, lastSent); }}
                  className="px-2 py-0.5 text-[10px] font-mono text-violet-300 bg-violet-500/10 border border-violet-500/20 rounded hover:bg-violet-500/20 transition-colors">
                  {tr('retry2')}</button>
              )}
              <button aria-label={tr('close')} onClick={() => setChatError(null)} className="text-red-400/40 hover:text-red-400">×</button>
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
            {tr('newMessages')}</button>
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
            placeholder={
              sending
                ? tr('generating')
                : status === "running"
                  ? tr('typeAMessage')
                  : tr('sendAMessageYourAgentWillWakeUp')
            }
            rows={1}
            className="flex-1 bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3.5 py-2.5 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30 transition-colors resize-none overflow-hidden" />
          {sending ? (
            <button onClick={() => { abortRef.current?.abort(); }}
              className="px-4 py-2.5 text-xs font-mono bg-red-400/15 text-red-400 border border-red-400/25 rounded-lg hover:bg-red-400/25 transition-colors shrink-0">
              {tr('stop')}</button>
          ) : (
            <button onClick={() => send()} disabled={!content.trim()}
              className="px-4 py-2.5 text-xs font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25 rounded-lg hover:bg-violet-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors shrink-0">
              {tr('send')}</button>
          )}
        </div>
        <p className="text-[9px] font-mono text-neutral-700 mt-1.5">{sending ? tr('agentIsGeneratingDoNotRefreshThePage2') : tr('enterToSendShiftEnterForNewLine')}</p>
      </div>
    </div>
  );
}

/* ── Shared UI helpers ── */

function BudgetBar({ current, total }: { current: number; total: number }) {
  const tr = useTranslations(hostedAgentMessages);
  const { locale } = useLocale();
  const pct = total > 0 ? Math.min(100, (current / total) * 100) : 0;
  const color = pct >= 100 ? "bg-red-400" : pct >= 80 ? "bg-amber-400" : pct >= 50 ? "bg-cyan-400" : "bg-emerald-400/80";
  return (
    <div className="flex items-center gap-1.5 shrink-0" title={tr('spentValue1OfValue2Budget', { value1: new Intl.NumberFormat(localeTag(locale), { minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(current), value2: new Intl.NumberFormat(localeTag(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(total) })}>
      <div className="w-16 h-[3px] rounded-full bg-white/[0.05] overflow-hidden">
        <div className={`h-full ${color} transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-neutral-500">
        ${new Intl.NumberFormat(localeTag(locale), { minimumFractionDigits: current < 0.1 ? 4 : 2, maximumFractionDigits: current < 0.1 ? 4 : 2 }).format(current)}
        <span className="text-neutral-700"> / ${new Intl.NumberFormat(localeTag(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(total)}</span>
      </span>
    </div>
  );
}

function CopyButton({ text, label, className = "" }: { text: string; label?: string; className?: string }) {
  const tr = useTranslations(hostedAgentMessages);
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
      title={tr('copyToClipboard')}>
      {copied ? `✓ ${tr('copiedFeedback')}` : label ?? tr('copyDefault')}
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

function SettingsModal({ agent, onClose, onUpdate, onForceRestart }: { agent: HostedAgent; onClose: () => void; onUpdate: () => void; onForceRestart: () => void }) {
  const { locale } = useLocale();
  const tr = useTranslations(hostedAgentMessages);
const PROVIDER_LABELS: Record<string, string> = {
  openrouter: "OpenRouter",
  cerebras: "Cerebras",
  groq: "Groq",
  mistral: "Mistral",
  nebius: "Nebius AI Studio",
  nvidia: "NVIDIA NIM",
  sambanova: "SambaNova",
  together: "Together AI",
  zai: "Z.AI",
  cloudflare: "Cloudflare Workers AI",
  deepseek: tr('deepSeekPaid'),
};
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
      if (res.ok) { setToast(tr('saved')); setTimeout(() => setToast(""), 2000); onUpdate(); }
      else { const d = await res.json().catch(() => ({})); setError(d.detail || translateHostedAgent('en', 'error2')); }
    } catch { setError(translateHostedAgent('en', 'networkError')); }
    setSaving(false);
  };

  const handleDelete = async () => {
    if (!confirm(tr('deleteThisAgentThisCannotBeUndone'))) return;
    setDeleting(true);
    try {
      const res = await authFetch(`${API_URL}/api/v1/hosted-agents/${agent.id}`, { method: "DELETE" });
      if (res.ok) router.push("/hosted-agents");
      else { const d = await res.json().catch(() => ({})); setError(d.detail || translateHostedAgent('en', 'failed')); }
    } catch { setError(translateHostedAgent('en', 'networkError')); }
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
          <h3 className="text-sm font-mono text-white">{tr('settings3')}{agent.agent_name}</h3>
          <button aria-label={tr('close')} onClick={onClose} className="text-neutral-600 hover:text-neutral-400 text-sm">×</button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          <div>
            <label className={labelCls}>{tr('systemPromptAGENTMd')}</label>
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)} className={inputCls + " min-h-[120px] resize-y"} />
          </div>
          <div>
            <label className={labelCls}>{tr('model')}</label>
            {(() => {
              const modelsByProvider = models.reduce<Record<string, FreeModel[]>>((acc, m) => {
                const p = m.provider ?? "openrouter";
                if (!acc[p]) acc[p] = [];
                acc[p].push(m);
                return acc;
              }, {});
              return (
                <select value={model} onChange={e => setModel(e.target.value)} className={inputCls + " cursor-pointer"}>
                  {PROVIDER_ORDER.filter(p => (modelsByProvider[p]?.length ?? 0) > 0).map(p => (
                    <optgroup key={p} label={PROVIDER_LABELS[p] ?? p}>
                      {modelsByProvider[p].map(m => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              );
            })()}
            <p className="text-[10px] font-mono text-neutral-700 mt-1">
              {tr('allModelsAreFreeExceptDeepSeekPaidEscalation')}</p>
          </div>
          <div>
            <label className={labelCls}>{tr('budgetUSD')}</label>
            <input type="number" step="0.1" min="0.1" max="100" value={budget}
              onChange={e => setBudget(e.target.value)} className={inputCls + " max-w-[200px]"} />
            <p className="text-[10px] font-mono text-neutral-700 mt-1">{tr('currentSpend')}{new Intl.NumberFormat(localeTag(locale), { minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(agent.total_cost_usd)}</p>
          </div>
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className={labelCls + " mb-0"}>{tr('heartbeat')}</label>
              <button onClick={() => setHbEnabled(!hbEnabled)}
                className={`relative w-9 h-5 rounded-full transition-colors ${hbEnabled ? "bg-emerald-400/30" : "bg-neutral-800"}`}>
                <div className={`absolute top-0.5 w-4 h-4 rounded-full transition-all ${hbEnabled ? "left-[18px] bg-emerald-400" : "left-0.5 bg-neutral-600"}`} />
              </button>
            </div>
            {hbEnabled && (
              <select value={hbSeconds} onChange={e => setHbSeconds(e.target.value)}
                className={inputCls + " max-w-[200px] cursor-pointer text-xs"}>
                <option value="300">{tr('every5Min')}</option>
                <option value="900">{tr('every15Min')}</option>
                <option value="1800">{tr('every30Min')}</option>
                <option value="3600">{tr('every1Hour')}</option>
                <option value="7200">{tr('every2Hours')}</option>
              </select>
            )}
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className={labelCls + " mb-0"}>{tr('stuckLoopDetection')}</label>
              <button onClick={() => setStuckLoop(!stuckLoop)}
                className={`relative w-9 h-5 rounded-full transition-colors ${stuckLoop ? "bg-amber-400/30" : "bg-neutral-800"}`}>
                <div className={`absolute top-0.5 w-4 h-4 rounded-full transition-all ${stuckLoop ? "left-[18px] bg-amber-400" : "left-0.5 bg-neutral-600"}`} />
              </button>
            </div>
            <p className="text-[10px] font-mono text-neutral-600 leading-relaxed">
              {tr('injectsModelRetryWhenTheAgentRepeatsTheSame')}</p>
          </div>

          <div className="bg-white/[0.02] border border-neutral-800/50 rounded-lg p-4">
            <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600 mb-2">{tr('info')}</p>
            <div className="grid grid-cols-2 gap-1.5 text-xs font-mono">
              <span className="text-neutral-600">{tr('agentID')}</span><span className="text-neutral-400 truncate">{agent.agent_id}</span>
              <span className="text-neutral-600">{tr('handle')}</span><span className="text-neutral-400">@{agent.agent_handle}</span>
              <span className="text-neutral-600">.deep/</span><span className="text-neutral-400">{new Intl.NumberFormat(localeTag(locale)).format(agent.memory_limit_mb)} {tr('mB')}</span>
              <span className="text-neutral-600">{tr('created')}</span><span className="text-neutral-400">{timeAgo(agent.created_at, locale)}</span>
            </div>
          </div>
          {error && (
            <div className="px-3 py-2 text-xs font-mono text-red-400/90 bg-red-400/[0.06] border border-red-400/15 rounded-lg">{displayHostedAgentText(locale, error)}</div>
          )}
        </div>
        <div className="px-6 py-3 border-t border-neutral-800/40 shrink-0">
          <button
            onClick={onForceRestart}
            className="w-full px-3 py-2 text-xs font-mono text-amber-300 bg-amber-400/[0.06] border border-amber-400/15 rounded-lg hover:bg-amber-400/10 transition-colors flex items-center gap-1.5">
            <span aria-hidden="true">⚡</span> {tr('forceRestart')}</button>
        </div>
        <div className="px-6 py-4 border-t border-neutral-800/40 flex items-center justify-between shrink-0">
          <button onClick={handleDelete} disabled={deleting}
            className="px-3 py-1.5 text-xs font-mono text-red-400/70 border border-red-400/15 rounded-lg hover:bg-red-400/[0.06] hover:text-red-400 disabled:opacity-40 transition-colors">
            {deleting ? tr('deleting') : tr('deleteAgent')}
          </button>
          <div className="flex items-center gap-3">
            {toast && <span className="text-[10px] font-mono text-emerald-400">{displayHostedAgentText(locale, toast)}</span>}
            <button aria-label={tr('close')} onClick={onClose} className="px-3 py-1.5 text-xs font-mono text-neutral-500 hover:text-neutral-300 transition-colors">{tr('cancel')}</button>
            <button onClick={save} disabled={saving}
              className="px-4 py-1.5 text-xs font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25 rounded-lg hover:bg-violet-500/25 disabled:opacity-40 transition-colors">
              {saving ? tr('saving2') : tr('save')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
