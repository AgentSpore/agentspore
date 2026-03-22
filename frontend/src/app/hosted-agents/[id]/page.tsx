"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, lazy, Suspense } from "react";
import { API_URL, HostedAgent, AgentFile, OwnerMessage, HOSTED_STATUS, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const CodeMirrorEditor = lazy(() => import("@/components/CodeMirrorEditor"));

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
  const [activeTab, setActiveTab] = useState<"chat" | "files">("chat");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [hasUnreadFiles, setHasUnreadFiles] = useState(false);

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
  useEffect(() => {
    const interval = setInterval(loadAgent, 15000);
    return () => clearInterval(interval);
  }, [loadAgent]);

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
            <div className="w-7 h-7 rounded-lg flex items-center justify-center text-xs font-mono shrink-0"
              style={{ background: "linear-gradient(135deg, rgba(139,92,246,0.2), rgba(34,211,238,0.1))", border: "1px solid rgba(139,92,246,0.25)" }}>
              {agent.agent_name.charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-mono font-medium text-white truncate">{agent.agent_name}</span>
                <span className="text-[10px] font-mono text-neutral-600 hidden sm:inline">@{agent.agent_handle}</span>
                <span className={`text-[11px] font-mono px-2.5 py-0.5 rounded-full border shrink-0 ${st.classes}`}>{st.label}</span>
              </div>
              <div className="flex items-center gap-2 text-[11px] font-mono text-neutral-600">
                <span className="truncate">{modelShort(agent.model)}</span>
                <span>·</span>
                <span className="shrink-0">${agent.total_cost_usd.toFixed(4)} / ${agent.budget_usd.toFixed(2)}</span>
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
    const interval = setInterval(loadFiles, 10000);
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

  const uploadFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const fileArr = Array.from(fileList);
    setUploading(true);
    setShowUploadZone(false);
    setUploadError(null);
    setUploadProgress({ current: 0, total: fileArr.length, name: "" });
    let uploaded = 0;
    const binaryExts = [".jpeg", ".jpg", ".png", ".gif", ".webp", ".ico", ".bmp", ".zip", ".tar", ".gz", ".pdf", ".exe", ".bin", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".wav"];
    try {
      for (let i = 0; i < fileArr.length; i++) {
        const file = fileArr[i];
        const filePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
        const ext = "." + filePath.split(".").pop()?.toLowerCase();
        if (binaryExts.includes(ext)) {
          setUploadError(`Skipped: ${filePath} — binary files not supported (text files only)`);
          continue;
        }
        setUploadProgress({ current: i + 1, total: fileArr.length, name: filePath.split("/").pop() || file.name });
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
          setUploadError(`Failed: ${filePath} — ${errMsg || res.status}`);
          break;
        }
        uploaded++;
      }
    } catch (e) {
      setUploadError(`Upload error: ${e instanceof Error ? e.message : "unknown"}`);
    }
    if (uploaded > 0) await loadFiles();
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

  const FILE_ICON: Record<string, string> = { config: "◆", memory: "◈", skill: "◇", text: "◻" };

  // Hidden directories — internal/generated, not useful to show
  const HIDDEN_PREFIXES = ["venv/", ".venv/", "__pycache__/", "node_modules/", ".git/", ".pip/", ".cache/"];
  const isHidden = (f: AgentFile) => {
    const p = f.file_path;
    if (HIDDEN_PREFIXES.some(h => p.startsWith(h) || p.includes("/" + h))) return true;
    return false;
  };

  const visibleFiles = files.filter(f => !isHidden(f));
  const totalSize = visibleFiles.reduce((sum, f) => sum + f.size_bytes, 0);

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

  const renderItem = (f: AgentFile, indent = 0) => (
    <div key={f.id} onClick={() => onSelect(f.file_path)}
      className={`flex items-center justify-between py-1.5 rounded cursor-pointer text-xs font-mono transition-colors group ${
        selectedFile === f.file_path
          ? "bg-violet-500/[0.1] text-violet-300"
          : "text-neutral-400 hover:bg-white/[0.03] hover:text-neutral-300"
      }`}
      style={{ paddingLeft: `${6 + indent * 12}px`, paddingRight: "4px" }}>
      <div className="flex items-center gap-1 min-w-0">
        <span className="text-[10px] opacity-40">{FILE_ICON[f.file_type] || "◻"}</span>
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
    default: {
      const entries = Object.entries(obj).filter(([k]) => !["new_content", "content"].includes(k));
      preview = entries.map(([k, v]) => `${k}: ${String(v).slice(0, 60)}`).join(", ");
      full = entries.map(([k, v]) => `${k}: ${String(v)}`).join("\n");
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

function ToolCallDisplay({ tool, args, status, result }: { tool: string; args: unknown; status: string; result?: string }) {
  const [expanded, setExpanded] = useState(false);
  const info = TOOL_LABELS[tool] || { icon: "⚡", label: tool, color: "text-cyan-300/80" };
  const { preview, full } = formatToolArgs(tool, args);
  const hasMore = full.length > preview.length || (result && result.length > 100);
  const doneLabel = DONE_LABELS[info.label] || info.label;
  const isDone = status === "done";

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
  const [streamTools, setStreamTools] = useState<Array<{ tool: string; args: unknown; status: string }>>([]);
  const [streamThinking, setStreamThinking] = useState("");
  const [streamPhase, setStreamPhase] = useState<"idle" | "connecting" | "waiting" | "streaming">("idle");
  const [sendElapsed, setSendElapsed] = useState(0);
  const sendTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const streamTextRef = useRef("");
  const streamToolsRef = useRef<Array<{ tool: string; args: unknown; status: string }>>([]);
  const streamThinkingRef = useRef("");

  const bottomRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const prevCountRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const onNewMessageRef = useRef(onNewMessage);
  onNewMessageRef.current = onNewMessage;

  // Abort stream on unmount
  useEffect(() => () => { abortRef.current?.abort(); }, []);

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
    if (!force && !isNearBottom()) return;
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  };

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

  useEffect(() => { loadMessages(); }, [loadMessages]);
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

  const send = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!content.trim() || sending) return;
    const text = content.trim();

    const optimisticMsg: OwnerMessage = {
      id: `opt-${Date.now()}`, sender_type: "user", content: text,
      edited_at: null, is_deleted: false, created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimisticMsg]);
    setContent("");
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
                setStreamText(prev => { const v = prev + event.content; streamTextRef.current = v; return v; });
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
                      updated[i] = { ...updated[i], status: "done" };
                      break;
                    }
                  }
                  streamToolsRef.current = updated;
                  return updated;
                });
                break;
              case "thinking_delta":
                setStreamPhase("streaming");
                setStreamThinking(prev => { const v = prev + event.content; streamThinkingRef.current = v; return v; });
                break;
              case "done":
                gotDone = true;
                await loadMessages();
                break;
              case "error":
                setChatError(event.message);
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
    <div className="h-full flex flex-col bg-white/[0.02] border border-neutral-800/50 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2 border-b border-neutral-800/40 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-mono uppercase tracking-[0.15em] text-neutral-600">Chat</span>
          <span className="text-xs font-mono text-neutral-700">
            {status === "running" ? "🟢 Online" : "⭘ Offline"}
          </span>
        </div>
        {messages.length > 0 && (
          <button onClick={() => setShowSearch(!showSearch)}
            className="text-sm font-mono text-neutral-600 hover:text-neutral-400 transition-colors px-1">
            {showSearch ? "×" : "🔍"}
          </button>
        )}
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
          <div key={m.id} className={`flex ${m.sender_type === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] rounded-xl text-sm font-mono ${
              m.sender_type === "user"
                ? "bg-cyan-500/[0.08] border border-cyan-500/15 text-cyan-100 px-3.5 py-2.5"
                : "bg-violet-500/[0.06] border border-violet-500/12 text-violet-100"
            }`}>
              {m.is_deleted ? (
                <span className="italic text-neutral-600 text-xs px-3.5 py-2.5 block">[deleted]</span>
              ) : (
                <>
                  {m.thinking && (
                    <button onClick={() => setExpandedThinking(s => toggle(s, m.id))}
                      className="w-full text-left px-3.5 py-1.5 border-b border-violet-500/10 flex items-center gap-2 hover:bg-violet-500/[0.04] transition-colors">
                      <span className="text-[10px] text-amber-400/70">◈ thinking</span>
                      <svg className={`w-3 h-3 text-neutral-600 transition-transform ${expandedThinking.has(m.id) ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                      </svg>
                    </button>
                  )}
                  {m.thinking && expandedThinking.has(m.id) && (
                    <div className="px-3.5 py-2 border-b border-violet-500/10 text-xs leading-relaxed text-amber-200/50 whitespace-pre-wrap max-h-48 overflow-y-auto font-mono italic">{m.thinking}</div>
                  )}

                  <div className="px-3.5 py-2.5 prose-agent">
                    <AgentMarkdown content={m.content} isUser={m.sender_type === "user"} />
                    <span className="text-[9px] text-neutral-600 mt-1 block">{timeAgo(m.created_at)}</span>
                  </div>

                  {m.tool_calls && m.tool_calls.length > 0 && (
                    <div className="border-t border-violet-500/10">
                      <button onClick={() => setExpandedTools(s => toggle(s, m.id))}
                        className="w-full text-left px-3.5 py-2 flex items-center gap-2 hover:bg-violet-500/[0.04] transition-colors">
                        <span className="text-xs text-cyan-400/70">⚡ {m.tool_calls.length} tool{m.tool_calls.length > 1 ? "s" : ""} used</span>
                        <svg className={`w-3.5 h-3.5 text-neutral-600 transition-transform ${expandedTools.has(m.id) ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                        </svg>
                      </button>
                      {expandedTools.has(m.id) && (
                        <div className="px-3.5 py-2 border-t border-violet-500/10 space-y-2 max-h-64 overflow-y-auto">
                          {m.tool_calls.map((tc: { tool: string; args: unknown; status: string; result?: string }, i: number) => (
                            <ToolCallDisplay key={i} tool={tc.tool} args={tc.args} status={tc.status} result={tc.result} />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}

        {/* Streaming response */}
        {isStreaming && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-xl bg-violet-500/[0.06] border border-violet-500/12 text-violet-100">
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
                    <ToolCallDisplay key={i} tool={tc.tool} args={tc.args} status={tc.status} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {sending && !isStreaming && (
          <div className="flex justify-start">
            <div className="px-3.5 py-2.5 rounded-xl bg-violet-500/[0.06] border border-violet-500/12">
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1">
                  <div className="w-1.5 h-1.5 bg-violet-400/60 rounded-full animate-pulse" />
                  <div className="w-1.5 h-1.5 bg-violet-400/40 rounded-full animate-pulse" style={{ animationDelay: "0.2s" }} />
                  <div className="w-1.5 h-1.5 bg-violet-400/20 rounded-full animate-pulse" style={{ animationDelay: "0.4s" }} />
                </div>
                <span className="text-[10px] text-neutral-500 font-mono">
                  {streamPhase === "connecting" ? "Connecting to agent…" : "Waiting for model response…"}
                </span>
                <span className="text-[9px] text-neutral-700 font-mono tabular-nums">{sendElapsed}s</span>
              </div>
            </div>
          </div>
        )}

        {chatError && (
          <div className="flex justify-center">
            <div className="px-3.5 py-2 rounded-xl bg-red-400/[0.06] border border-red-400/15 text-xs font-mono text-red-400/80 flex items-center gap-2">
              <span>⚠ {chatError}</span>
              <button onClick={() => setChatError(null)} className="text-red-400/40 hover:text-red-400">×</button>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

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
            const lang = className?.replace("language-", "") || "";
            const text = String(children).replace(/\n$/, "");
            return (
              <div className="relative group my-2">
                {lang && <span className="absolute top-1 right-2 text-[9px] font-mono text-neutral-600">{lang}</span>}
                <pre className="bg-black/30 rounded-lg px-3 py-2.5 text-[12px] overflow-x-auto border border-neutral-800/30">
                  <code>{text}</code>
                </pre>
                <button onClick={() => navigator.clipboard.writeText(text)}
                  className="absolute top-1 left-2 text-[9px] font-mono text-neutral-700 opacity-0 group-hover:opacity-100 hover:text-neutral-400 transition-all">
                  copy
                </button>
              </div>
            );
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
