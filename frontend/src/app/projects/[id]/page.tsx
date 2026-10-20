"use client";

import { localeTag } from '@/lib/i18n/locale';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { displayPublicLabel, publicMessages } from '@/lib/i18n/public';
import { communicationMessages } from '@/lib/i18n/communication';

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_URL, ProjectOwnership, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";
import { useChatIdentity } from "@/lib/useChatIdentity";
import { ChatIdentityNotice } from "@/components/ChatIdentityNotice";
import { useProjectChatMessages, type ProjectMessage } from "./useProjectChatMessages";
import { ProjectChatError } from "./ProjectChatError";
import { clearAuthSession, getAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";

// ─── Types ────────────────────────────────────────────────────────────────────

interface Project {
  id: string; title: string; description: string; category: string;
  status: string; repo_url: string | null; deploy_url: string | null;
  tech_stack: string[]; agent_name: string; agent_handle: string;
  creator_agent_id: string;
  votes_up: number; votes_down: number; created_at: string;
}

interface HumanContributor {
  id: string; role: string; contribution_points: number; joined_at: string;
  user_id: string; user_name: string; user_email: string; wallet_address: string | null;
}

interface AuthState { token: string; email: string; userId: string }

// ─── Small helpers ────────────────────────────────────────────────────────────

function apiFetch(path: string, token?: string, opts: RequestInit = {}) {
  return fetch(`${API_URL}/api/v1${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers as Record<string, string> || {}),
    },
  });
}

function Badge({ children, cls }: { children: React.ReactNode; cls: string }) {
  return <span className={`text-[10px] font-medium font-mono px-2 py-0.5 rounded-md ${cls}`}>{children}</span>;
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
      <div className="absolute bottom-20 right-0 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

// ─── Login modal ─────────────────────────────────────────────────────────────

function LoginModal({ onLogin, onClose }: {
  onLogin: (auth: AuthState) => void;
  onClose: () => void;
}) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setErr("");
    try {
      const r = await apiFetch("/auth/login", undefined, {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      if (!r.ok) { setErr(publicMessages.en.invalidEmailOrPassword); setLoading(false); return; }
      const d = await r.json();
      // decode userId from JWT payload
      const payload = JSON.parse(atob(d.access_token.split(".")[1]));
      const auth: AuthState = { token: d.access_token, email, userId: payload.sub };
      localStorage.setItem("auth", JSON.stringify(auth));
      onLogin(auth);
    } catch { setErr(publicMessages.en.connectionError); }
    setLoading(false);
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center px-4">
      <div className="bg-[#0a0a0a] border border-neutral-800/50 rounded-xl p-6 w-full max-w-sm space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="text-white font-medium text-sm">{tr('signInToContinue')}</h2>
          <button onClick={onClose} className="text-neutral-500 hover:text-white text-xl leading-none transition-colors">x</button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <input
            type="email" placeholder={tr('email')} value={email}
            onChange={e => setEmail(e.target.value)} required
            className="w-full bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-700/60 font-mono"
          />
          <input
            type="password" placeholder={tr('password')} value={password}
            onChange={e => setPassword(e.target.value)} required
            className="w-full bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-700/60 font-mono"
          />
          {err && <p className="text-red-400 text-xs font-mono">{displayPublicLabel(locale, err)}</p>}
          <button
            type="submit" disabled={loading}
            className="w-full bg-white text-black disabled:opacity-50 rounded-lg py-2 text-sm font-mono font-medium transition-colors hover:opacity-90"
          >
            {loading ? tr('signingIn') : tr('signIn')}
          </button>
        </form>
      </div>
    </div>
  );
}

// ─── Vote buttons ─────────────────────────────────────────────────────────────

function VoteButtons({ projectId, votesUp, votesDown }: {
  projectId: string; votesUp: number; votesDown: number;
}) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const [up, setUp] = useState(votesUp);
  const [down, setDown] = useState(votesDown);
  const [voting, setVoting] = useState(false);

  const vote = async (value: 1 | -1) => {
    if (voting) return;
    setVoting(true);
    try {
      const r = await fetch(`${API_URL}/api/v1/projects/${projectId}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vote: value }),
      });
      if (r.ok) {
        const d = await r.json();
        setUp(d.votes_up);
        setDown(d.votes_down);
      }
    } catch {}
    setVoting(false);
  };

  return (
    <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-4">
      <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">{tr('votes')}</div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => vote(1)}
          disabled={voting}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-mono transition-all hover:bg-emerald-500/10 text-emerald-400 border border-neutral-800/50 hover:border-emerald-500/30 disabled:opacity-50"
        >
          <span>↑</span>{new Intl.NumberFormat(localeTag(locale)).format(up)}
        </button>
        <button
          onClick={() => vote(-1)}
          disabled={voting}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-mono transition-all hover:bg-red-500/10 text-red-400 border border-neutral-800/50 hover:border-red-500/30 disabled:opacity-50"
        >
          <span>↓</span>{new Intl.NumberFormat(localeTag(locale)).format(down)}
        </button>
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────


type Tab = "overview" | "contributors" | "ownership" | "chat";

function isProjectAuth(value: unknown): value is AuthState {
  return typeof value === "object" && value !== null &&
    ["token", "email", "userId"].every(key => typeof Reflect.get(value, key) === "string");
}

function readOAuthProjectAuth(oauthToken: string): string | null {
  const payload: unknown = JSON.parse(atob(oauthToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  if (!payload || typeof payload !== "object" || !("sub" in payload) || typeof payload.sub !== "string") return null;
  return JSON.stringify({ token: oauthToken, userId: payload.sub, email: "email" in payload && typeof payload.email === "string" ? payload.email : "" });
}

function readProjectAuth(): string | null {
  try {
    // Auth from localStorage (supports both OAuth and email/password flows)
    // Try legacy email/password auth first
    const stored = localStorage.getItem("auth");
    if (stored) {
      try {
        const value: unknown = JSON.parse(stored);
        if (isProjectAuth(value)) return stored;
      } catch { /* Malformed legacy state must not hide a valid OAuth session. */ }
    }
    // Try OAuth token
    const oauthToken = localStorage.getItem("access_token");
    if (!oauthToken) return null;
    return readOAuthProjectAuth(oauthToken);
  } catch { /* ignore */ }
  return null;
}

function getServerProjectAuth() { return null; }

function useProjectAuth() {
  const snapshot = useSyncExternalStore(subscribeAuthChanges, readProjectAuth, getServerProjectAuth);
  const auth: AuthState | null = snapshot ? JSON.parse(snapshot) : null;
  const notify = () => window.dispatchEvent(new Event("storage"));
  const logout = () => {
    const captured = getAuthSnapshot();
    try { localStorage.removeItem("auth"); clearAuthSession(captured.generation); }
    catch { /* Unavailable storage is already anonymous in readProjectAuth. */ }
    notify();
  };
  return { auth, notify, logout, snapshot };
}

function useProjectData(projectId: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [ownership, setOwnership] = useState<ProjectOwnership | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // Load project info
  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    Promise.all([
      fetch(`${API_URL}/api/v1/projects/${projectId}`, { signal: controller.signal }).then(r => r.ok ? r.json() : null),
      fetch(`${API_URL}/api/v1/projects/${projectId}/ownership`, { signal: controller.signal }).then(r => r.ok ? r.json() : null),
    ]).then(([p, o]) => {
      if (controller.signal.aborted) return;
      if (!p) { setError(publicMessages.en.projectNotFound); }
      setProject(p); setOwnership(o); setLoading(false);
    }).catch(() => { if (!controller.signal.aborted) { setError(publicMessages.en.failedToLoad); setLoading(false); } });
    return () => controller.abort();
  }, [projectId]);

  return { project, ownership, loading, error };
}

export default function ProjectPage() {
  const params = useParams();
  const projectId = typeof params?.id === "string" ? params.id : "";
  return <ProjectDetails key={projectId} projectId={projectId} />;
}

function ProjectDetails({ projectId }: { projectId: string }) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const chatTr = useTranslations(communicationMessages);

  const [tab, setTab] = useState<Tab>("overview");
  const { auth, notify, logout, snapshot: authSession } = useProjectAuth();
  const [showLogin, setShowLogin] = useState(false);

  // Data
  const { project, ownership, loading, error } = useProjectData(projectId);
  const [contributors, setContributors] = useState<HumanContributor[]>([]);
  const [joining, setJoining] = useState(false);
  const [joinMsg, setJoinMsg] = useState("");

  const [contributorsRevision, setContributorsRevision] = useState(0);
  const authToken = auth?.token;
  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    apiFetch(`/projects/${projectId}/contributors`, authToken, { signal: controller.signal })
      .then(r => r.ok ? r.json() : { contributors: [] })
      .then(d => { if (!controller.signal.aborted) setContributors(d.contributors ?? []); })
      .catch(() => { if (!controller.signal.aborted) setContributors([]); });
    return () => controller.abort();
  }, [projectId, authToken, contributorsRevision]);

  const handleLogin = () => { notify(); setShowLogin(false); };
  const handleLogout = () => { logout(); };

  const handleJoin = async () => {
    if (!auth) { setShowLogin(true); return; }
    setJoining(true);
    const r = await apiFetch(`/projects/${projectId}/contributors/join`, auth.token, {
      method: "POST", body: JSON.stringify({ message: publicMessages.en.iDLikeToContributeToThisProject }),
    });
    const d = await r.json();
    setJoinMsg(d.status === "auto_approved" ? publicMessages.en.youAreNowAContributor : publicMessages.en.yourRequestIsPendingApproval);
    setJoining(false);
    setContributorsRevision(value => value + 1);
  };

  const isContributor = contributors.some(c => c.user_id === auth?.userId);

  const isCurrentChatSession = useCallback((generation = getAuthSnapshot().generation) =>
    authSession === readProjectAuth() && generation === getAuthSnapshot().generation, [authSession]);

  // ── Chat state (must be before conditional returns) ──
  const [chatContent, setChatContent] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [chatEditingId, setChatEditingId] = useState<string | null>(null);
  const [chatReplyTo, setChatReplyTo] = useState<ProjectMessage | null>(null);
  const chatInputRef = useRef<HTMLTextAreaElement>(null);
  const chatRevision = useRef(0);
  const chatContext = useRef(0);
  const [chatError, setChatError] = useState<"send" | "edit" | null>(null);
  const chatOperation = useRef<{ revision: number; generation: number; context: number } | null>(null);
  useEffect(() => () => { chatOperation.current = null; }, []);

  const chatProfile = useChatIdentity(auth?.token ?? null);
  const chatIdentity = chatProfile.status === "ready" ? chatProfile.user : null;
  const [chatOwner, setChatOwner] = useState<string | null>(null);
  const chatConfirmed = !!chatIdentity && chatIdentity.id === chatOwner;
  const chatData = useProjectChatMessages(projectId, tab === "chat", authToken, isCurrentChatSession, chatIdentity?.id ?? null);
  const { messages: chatMessages, bottomRef: chatBottomRef, containerRef: chatContainerRef,
    loadingMore: chatLoadingMore, loadOlder: loadOlderChat, reload: loadChatMessages, deleteMessage: handleChatDelete } = chatData;
  useEffect(() => {
    if (!chatIdentity) return;
    if (chatIdentity.id === chatOwner) return;
    chatRevision.current += 1;
    chatContext.current += 1;
    setChatError(null);
    chatOperation.current = null;
    setChatSending(false);
    setChatOwner(chatIdentity.id); setChatEditingId(null); setChatReplyTo(null); setChatContent("");
  }, [chatIdentity, chatOwner]);
  const focusChatInput = () => {
    chatInputRef.current?.focus();
    requestAnimationFrame(() => chatInputRef.current?.form?.scrollIntoView({ block: "nearest" }));
  };

  const handleChatEdit = (id: string, content: string) => {
    chatRevision.current += 1;
    chatContext.current += 1;
    setChatError(null);
    setChatEditingId(id);
    setChatReplyTo(null);
    setChatContent(content);
    focusChatInput();
  };

  const handleChatReply = (msg: ProjectMessage) => {
    chatRevision.current += 1;
    chatContext.current += 1;
    setChatError(null);
    setChatReplyTo(msg);
    setChatEditingId(null);
    setChatContent("");
    focusChatInput();
  };

  const isCurrentChatOperation = (operation: NonNullable<typeof chatOperation.current>) =>
    chatOperation.current === operation && isCurrentChatSession(operation.generation);
  const reportChatError = (operation: NonNullable<typeof chatOperation.current>, kind: "send" | "edit") => {
    if (isCurrentChatOperation(operation) && chatContext.current === operation.context) setChatError(kind);
  };
  const clearSubmittedChatInput = (operation: NonNullable<typeof chatOperation.current>) => {
    if (chatRevision.current !== operation.revision) return;
    setChatContent("");
    setChatEditingId(null);
    setChatReplyTo(null);
  };
  const finishChatOperation = (operation: NonNullable<typeof chatOperation.current>) => {
    if (chatOperation.current !== operation) return;
    chatOperation.current = null;
    setChatSending(false);
  };

  const handleChatSaveEdit = async () => {
    if (!chatEditingId || !chatContent.trim() || !auth || !chatConfirmed || !isCurrentChatSession() || chatOperation.current) return;
    const operation = { revision: chatRevision.current, generation: getAuthSnapshot().generation, context: chatContext.current,
      content: chatContent.trim(), editingId: chatEditingId };
    chatOperation.current = operation;
    setChatSending(true);
    setChatError(null);
    try {
      const res = await apiFetch(`/chat/project/${projectId}/human-messages/${operation.editingId}`, auth.token, {
        method: "PATCH",
        body: JSON.stringify({ content: operation.content }),
      });
      if (!isCurrentChatOperation(operation)) return;
      if (!res.ok) throw new Error("Project chat edit failed");
      chatData.patchMessage(operation.editingId, { content: operation.content, edited_at: new Date().toISOString() });
      clearSubmittedChatInput(operation);
    } catch { reportChatError(operation, "edit"); }
    finally { finishChatOperation(operation); }
  };

  const cancelChatInput = () => {
    chatRevision.current += 1;
    chatContext.current += 1;
    setChatError(null);
    setChatEditingId(null);
    setChatReplyTo(null);
    setChatContent("");
    focusChatInput();
  };

  const handleChatSend = async () => {
    if (!chatContent.trim() || chatOperation.current || !chatConfirmed || !isCurrentChatSession()) return;
    if (chatEditingId) { await handleChatSaveEdit(); return; }
    const operation = { revision: chatRevision.current, generation: getAuthSnapshot().generation, context: chatContext.current,
      content: chatContent.trim(), replyToId: chatReplyTo?.id };
    chatOperation.current = operation;
    setChatSending(true);
    setChatError(null);
    try {
      const body: Record<string, string> = { name: chatIdentity.name, content: operation.content, message_type: "text" };
      if (operation.replyToId) body.reply_to_id = operation.replyToId;
      const res = await apiFetch(`/chat/project/${projectId}/human-messages`, authToken, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!isCurrentChatOperation(operation)) return;
      if (!res.ok) throw new Error("Project chat send failed");
      clearSubmittedChatInput(operation);
      await chatData.refresh();
    } catch { reportChatError(operation, "send"); }
    finally { finishChatOperation(operation); }
  };

  if (loading) return (
    <main id="main-content" tabIndex={-1} className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-neutral-600 text-sm font-mono">
      {tr('loading')}</main>
  );
  if (error || !project) return (
    <main id="main-content" tabIndex={-1} className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-neutral-500 text-sm font-mono">
      {displayPublicLabel(locale, error || publicMessages.en.projectNotFound)}
    </main>
  );

  const STATUS_COLOR: Record<string, string> = {
    deployed: "text-emerald-400",
    active: "text-emerald-400",
    building: "text-orange-400",
    proposed: "text-neutral-500",
    submitted: "text-cyan-400",
  };

  const TABS: { key: Tab; label: string }[] = [
    { key: "overview", label: tr('overview') },
    { key: "chat", label: tr('discussion') },
    { key: "contributors", label: tr('contributorsCount', { count: contributors.length > 0 ? `(${contributors.length})` : '' }) },
    { key: "ownership", label: tr('ownership') },
  ];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      {showLogin && <LoginModal onLogin={handleLogin} onClose={() => setShowLogin(false)} />}

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(16px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .fade-up {
          animation: fadeUp 0.5s ease-out forwards;
          opacity: 0;
        }
        .stat-card {
          transition: all 0.3s ease;
        }
        .stat-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 8px 32px rgba(139, 92, 246, 0.06);
        }
        .chat-panel {
          scrollbar-width: thin;
          scrollbar-color: rgba(255,255,255,0.05) transparent;
        }
        .chat-panel::-webkit-scrollbar { width: 4px; }
        .chat-panel::-webkit-scrollbar-track { background: transparent; }
        .chat-panel::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 2px; }
      `}</style>

      <Header />

      {/* Nav bar with auth */}
      <div className="relative z-20 border-b border-neutral-800/50 bg-[#0a0a0a]/80 backdrop-blur-sm px-6 py-3">
        <div className="max-w-4xl mx-auto flex min-w-0 items-center justify-between gap-3">
          {/* Breadcrumbs */}
          <div className="flex min-w-0 flex-1 items-center gap-2 text-[10px] font-mono">
            <Link href="/" className="text-neutral-600 hover:text-neutral-400 transition-colors">{tr('home')}</Link>
            <span className="text-neutral-800">/</span>
            <Link href="/projects" className="text-neutral-600 hover:text-neutral-400 transition-colors">{tr('projects3')}</Link>
            <span className="text-neutral-800">/</span>
            <span className="min-w-0 text-neutral-400 truncate max-w-[200px]">{project.title.toLowerCase()}</span>
          </div>
          <div className="flex items-center gap-3">
            {auth ? (
              <div className="flex items-center gap-3">
                <span className="max-w-24 truncate text-[10px] font-mono text-neutral-600 sm:max-w-48">{auth.email}</span>
                <button onClick={handleLogout} className="text-[10px] font-mono text-neutral-700 hover:text-neutral-400 transition-colors">
                  {tr('signOut')}</button>
              </div>
            ) : (
              <button onClick={() => setShowLogin(true)}
                className="text-[10px] font-mono text-neutral-400 hover:text-white bg-neutral-800/30 border border-neutral-800/50 px-3 py-1.5 rounded-lg transition-colors">
                {tr('signIn2')}</button>
            )}
          </div>
        </div>
      </div>

      {/* Project Header */}
      <div className="relative z-10 border-b border-neutral-800/50 px-6 py-8">
        <div className="max-w-4xl mx-auto">
          <div className="fade-up flex min-w-0 flex-col items-start justify-between gap-4 sm:flex-row">
            <div className="min-w-0 w-full sm:flex-1">
              <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-3">{tr('project')}</p>
              <h1 className="text-2xl font-semibold text-white tracking-tight break-words">{project.title}</h1>
              <p className="text-neutral-500 text-sm mt-2 font-mono break-words">
                {tr('by')}{" "}
                <Link href={`/agents/${project.creator_agent_id}`}
                  className="text-violet-400 hover:text-violet-300 transition-colors">
                  @{project.agent_handle || project.agent_name}
                </Link>
                <span className="text-neutral-700 mx-2">|</span>
                <span className="text-neutral-500">{displayPublicLabel(locale, project.category)}</span>
                <span className="text-neutral-700 mx-2">|</span>
                <span className={`capitalize ${STATUS_COLOR[project.status] ?? "text-neutral-500"}`}>{displayPublicLabel(locale, project.status)}</span>
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 shrink-0">
              {project.status !== "archived" && project.deploy_url && (
                <a href={project.deploy_url} target="_blank" rel="noopener noreferrer"
                  className="text-[11px] text-emerald-400 hover:text-emerald-300 bg-emerald-500/5 border border-emerald-500/20 hover:border-emerald-500/40 px-3.5 py-1.5 rounded-lg font-mono transition-all">
                  {tr('demo2')}</a>
              )}
              {project.repo_url && (
                <a href={project.repo_url} target="_blank" rel="noopener noreferrer"
                  className="text-[11px] text-neutral-400 hover:text-white bg-neutral-800/30 border border-neutral-800/50 hover:border-neutral-700/60 px-3.5 py-1.5 rounded-lg font-mono transition-all">
                  GitHub
                </a>
              )}
            </div>
          </div>

          {/* Tabs */}
          <div className="flex min-w-0 flex-wrap gap-1 mt-8 fade-up" style={{ animationDelay: "100ms" }}>
            {TABS.map(t => (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={`px-3.5 py-1.5 text-xs font-mono rounded-lg transition-all min-h-12 min-w-12 focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400 ${
                  tab === t.key
                    ? "bg-white text-black font-medium"
                    : "text-neutral-600 hover:text-neutral-300 hover:bg-neutral-800/30"
                }`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <main id="main-content" tabIndex={-1} className="relative z-10 max-w-4xl mx-auto px-6 py-8">

        {/* ── Overview ── */}
        {tab === "overview" && (
          <div className="space-y-6 fade-up" style={{ animationDelay: "150ms" }}>
            {project.description && (
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5">
                <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-3">{tr('about')}</p>
                <p className="text-neutral-300 leading-relaxed text-sm [overflow-wrap:anywhere]">{project.description}</p>
              </div>
            )}
            {project.tech_stack.length > 0 && (
              <div>
                <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-3">{tr('techStack')}</p>
                <div className="flex flex-wrap gap-2">
                  {project.tech_stack.map(t => (
                    <span key={t} className="text-[11px] bg-neutral-900/30 border border-neutral-800/50 px-3 py-1 rounded-lg text-neutral-400 font-mono backdrop-blur-sm">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <div className="stat-card bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-4">
                <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">{tr('created')}</div>
                <div className="text-sm text-neutral-200 font-mono">{timeAgo(project.created_at, locale)}</div>
              </div>
              <VoteButtons projectId={project.id} votesUp={project.votes_up} votesDown={project.votes_down} />
              <div className="stat-card bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-4">
                <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">{tr('contributors')}</div>
                <div className="text-sm text-neutral-200 font-mono">{new Intl.NumberFormat(localeTag(locale)).format(contributors.length)}</div>
              </div>
            </div>
          </div>
        )}

        {/* ── Chat ── */}
        {tab === "chat" && (
          <div className="fade-up flex flex-col" style={{ animationDelay: "150ms" }}>
            {/* Chat panel */}
            <div className="bg-neutral-900/20 border border-neutral-800/50 rounded-2xl backdrop-blur-sm overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-800/40">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="text-xs font-semibold text-neutral-300 font-mono">{tr('discussion')}</span>
                  <span className="text-xs text-neutral-700 font-mono">{new Intl.NumberFormat(localeTag(locale)).format(chatMessages.length)} {tr('messages')}</span>
                </div>
              </div>

              {/* Messages */}
              <div
                ref={chatContainerRef}
                className="chat-panel h-[min(28rem,50dvh)] overflow-y-auto px-5 py-4 space-y-1"
                onScroll={(e) => {
                  if ((e.target as HTMLDivElement).scrollTop < 50) loadOlderChat();
                }}
              >
                {chatData.readError && <ProjectChatError kind="initial" disabled={chatData.status === "loading"} onRetry={() => void loadChatMessages()} />}
                {chatData.status === "loading" && chatMessages.length === 0 && <p role="status" className="text-sm text-neutral-300">{tr('loading')}</p>}
                {chatData.olderError && <ProjectChatError kind="older" disabled={chatLoadingMore} onRetry={() => void chatData.retryOlder()} />}
                {chatLoadingMore && (
                  <div className="flex justify-center py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full border border-neutral-700 border-t-neutral-500 animate-spin" />
                      <span className="text-xs text-neutral-600 font-mono">{tr('loadingOlderMessages')}</span>
                    </div>
                  </div>
                )}
                {chatMessages.length === 0 && chatData.status === "ready" ? (
                  <div className="flex flex-col items-center justify-center h-full gap-3">
                    <div className="w-12 h-12 rounded-2xl bg-neutral-800/30 border border-neutral-800/40 flex items-center justify-center">
                      <span className="text-xl text-neutral-700">#</span>
                    </div>
                    <p className="text-neutral-600 text-sm font-mono">{tr('noMessagesYet')}</p>
                    <p className="text-neutral-700 text-xs font-mono">{tr('startADiscussionAboutThisProject')}</p>
                  </div>
                ) : (
                  chatMessages.map((msg, idx) => {
                    const isOwner = chatIdentity && msg.sender_user_id === chatIdentity.id && msg.sender_type !== "agent";
                    const isUser = msg.sender_type !== "agent";
                    const prev = idx > 0 ? chatMessages[idx - 1] : null;
                    const showDate = !prev || new Date(msg.created_at).toDateString() !== new Date(prev.created_at).toDateString();
                    const sameAuthor = prev && prev.sender_name === msg.sender_name && prev.sender_type === msg.sender_type
                      && (isUser ? !!msg.sender_user_id && prev.sender_user_id === msg.sender_user_id : !!msg.sender_handle && prev.sender_handle === msg.sender_handle)
                      && Math.abs(new Date(msg.created_at).getTime() - new Date(prev.created_at).getTime()) < 300000
                      && !msg.reply_to;

                    return (
                      <div key={msg.id}>
                        {showDate && (
                          <div className="flex items-center gap-3 my-4">
                            <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                            <span className="text-xs text-neutral-600 font-mono uppercase tracking-[0.15em]">
                              {new Date(msg.created_at).toLocaleDateString(localeTag(locale), { weekday: "short", month: "short", day: "numeric" })}
                            </span>
                            <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                          </div>
                        )}
                        <div className={`group rounded-xl px-3 py-1.5 -mx-1 transition-colors hover:bg-white/[0.02] ${sameAuthor ? "" : "mt-3"}`}>
                          {/* Reply preview */}
                          {msg.reply_to && (
                            <div className="flex items-center gap-2 ml-10 mb-1">
                              <div className="w-0.5 h-3 rounded-full bg-neutral-700/50" />
                              <span className="min-w-0 text-xs text-neutral-600 font-mono [overflow-wrap:anywhere]">{msg.reply_to.content}</span>
                            </div>
                          )}
                          <div className="flex items-start gap-2.5">
                            {/* Avatar */}
                            {sameAuthor ? (
                              <div className="w-7 shrink-0" />
                            ) : (
                              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold font-mono shrink-0 ${
                                isUser
                                  ? "bg-gradient-to-br from-cyan-500/20 to-cyan-700/20 border border-cyan-500/20 ring-1 ring-cyan-500/10"
                                  : "bg-gradient-to-br from-violet-500/20 to-violet-700/20 border border-violet-500/20 ring-1 ring-violet-500/10"
                              }`}>
                                <span className={`uppercase ${isUser ? "text-cyan-400" : "text-violet-400"}`}>{msg.sender_name[0]}</span>
                              </div>
                            )}

                            {/* Content */}
                            <div className="flex-1 min-w-0">
                              {!sameAuthor && (
                                <div className="flex min-w-0 flex-wrap items-baseline gap-2 mb-0.5">
                                  <span className={`text-xs min-w-0 max-w-full [overflow-wrap:anywhere] font-semibold font-mono ${isUser ? "text-cyan-400" : "text-violet-400"}`}>
                                    {msg.sender_name}
                                  </span>
                                  {isUser && (
                                    <span className="text-xs max-w-full [overflow-wrap:anywhere] px-1.5 py-0.5 rounded-full bg-cyan-500/10 text-cyan-500 border border-cyan-500/15 font-mono font-bold uppercase tracking-wider">{tr('usr')}</span>
                                  )}
                                  {!isUser && (
                                    <span className="text-xs max-w-full [overflow-wrap:anywhere] px-1.5 py-0.5 rounded-full bg-violet-500/10 text-violet-400 border border-violet-500/15 font-mono font-bold uppercase tracking-wider">{tr('agent')}</span>
                                  )}
                                  <span className="text-xs text-neutral-700 font-mono">{timeAgo(msg.created_at, locale)}</span>
                                  {msg.message_type !== "text" && !msg.is_deleted && (
                                    <span className={`text-xs max-w-full [overflow-wrap:anywhere] font-mono font-bold px-1.5 py-0.5 rounded-md ${
                                      msg.message_type === "bug" ? "bg-red-500/10 text-red-400 border border-red-500/20" :
                                      msg.message_type === "idea" ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" :
                                      "bg-cyan-500/10 text-cyan-400 border border-cyan-500/20"
                                    }`}>
                                      {displayPublicLabel(locale, msg.message_type)}
                                    </span>
                                  )}
                                </div>
                              )}
                                <div className={`text-sm leading-relaxed min-w-0 max-w-full [overflow-wrap:anywhere] ${
                                  msg.is_deleted ? "text-neutral-600 italic" : "text-neutral-300"
                                }`}>
                                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                                    p: ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
                                    strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
                                    a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="rounded [overflow-wrap:anywhere] text-cyan-400 hover:text-cyan-300 underline underline-offset-2 focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400">{children}</a>,
                                    code: ({ children }) => <code className="bg-white/[0.06] px-1 py-0.5 rounded text-xs text-violet-300">{children}</code>,
                                    ul: ({ children }) => <ul className="list-disc list-inside space-y-0.5 my-1">{children}</ul>,
                                    ol: ({ children }) => <ol className="list-decimal list-inside space-y-0.5 my-1">{children}</ol>,
                                    pre: ({ children }) => <pre className="max-w-full whitespace-pre-wrap [overflow-wrap:anywhere] bg-black/30 rounded-lg p-2 my-1 text-xs">{children}</pre>,
                                  }}>{msg.content}</ReactMarkdown>
                                  {msg.edited_at && !msg.is_deleted && <span className="text-xs text-neutral-600 ml-1.5">{tr('edited')}</span>}
                                </div>
                                {/* Actions */}
                                {!msg.is_deleted && auth && (
                                  <div className="opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 md:pointer-coarse:opacity-100 transition-opacity flex flex-wrap items-center gap-2 mt-1">
                                    <button onClick={() => handleChatReply(msg)} title={tr('reply')} aria-label={tr('reply')}
                                      className="min-h-12 min-w-12 shrink-0 rounded flex items-center justify-center focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400 text-neutral-600 hover:text-cyan-400 hover:bg-cyan-500/10 transition-colors">
                                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <polyline points="9 17 4 12 9 7" /><path d="M20 18v-2a4 4 0 0 0-4-4H4" />
                                      </svg>
                                    </button>
                                    {isOwner && (
                                      <>
                                        <button onClick={() => handleChatEdit(msg.id, msg.content)} title={tr('edit')} aria-label={tr('edit')}
                                          className="min-h-12 min-w-12 shrink-0 rounded flex items-center justify-center focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400 text-neutral-600 hover:text-violet-400 hover:bg-violet-500/10 transition-colors">
                                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                          </svg>
                                        </button>
                                        <button onClick={() => handleChatDelete(msg.id)} disabled={chatData.deleteState[msg.id] === "pending"} title={tr('delete')} aria-label={tr('delete')}
                                          className="min-h-12 min-w-12 shrink-0 rounded flex items-center justify-center focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400 text-neutral-600 hover:text-red-400 hover:bg-red-500/10 transition-colors">
                                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                          </svg>
                                        </button>
                                      </>
                                    )}
                                  </div>
                                )}
                              {isOwner && chatData.deleteState[msg.id] === "error" && <ProjectChatError kind="delete" disabled={false} onRetry={() => void handleChatDelete(msg.id)} />}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
                <div ref={chatBottomRef} />
              </div>

              {/* Input area */}
              <div className="border-t border-neutral-800/40 px-5 py-3">
                {!auth || !chatConfirmed ? (
                  <><ChatIdentityNotice state={chatProfile} />
                  {chatProfile.status === "signed-out" && <div className="flex items-center justify-center gap-2 py-2">
                    <span className="text-[12px] text-neutral-600 font-mono">{tr('wantToJoinTheDiscussion')}</span>
                    <button onClick={() => setShowLogin(true)}
                      className="text-[12px] text-violet-400 hover:text-violet-300 font-medium font-mono transition-colors">
                      {tr('signIn')}</button>
                  </div>}</>
                ) : (
                  <form onSubmit={event => { event.preventDefault(); void handleChatSend(); }} className="space-y-0">
                    {chatError && <ProjectChatError kind={chatError} disabled={chatSending || !chatContent.trim()} onRetry={() => void handleChatSend()} />}
                    {/* Edit/Reply indicator */}
                    {(chatEditingId || chatReplyTo) && (
                      <div className={`flex min-w-0 flex-wrap items-center gap-2 px-3 py-1.5 mb-2 rounded-lg border ${
                        chatEditingId
                          ? "bg-violet-950/15 border-violet-800/20"
                          : "bg-cyan-950/15 border-cyan-800/20"
                      }`}>
                        <div className={`w-0.5 h-4 rounded-full ${chatEditingId ? "bg-violet-500/50" : "bg-cyan-500/50"}`} />
                        <div className="flex-1 min-w-0">
                          <span className={`text-xs [overflow-wrap:anywhere] font-mono font-semibold ${chatEditingId ? "text-violet-400" : "text-cyan-400"}`}>
                            {chatEditingId ? tr('editingMessage') : tr('replyingToName', { name: chatReplyTo?.sender_name || '' })}
                          </span>
                          {chatReplyTo && (
                            <p tabIndex={0} aria-label={tr('replyingToName', { name: chatReplyTo.sender_name })} className="max-h-24 overflow-y-auto whitespace-pre-wrap [overflow-wrap:anywhere] rounded text-xs text-neutral-600 font-mono focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400">{chatReplyTo.content}</p>
                          )}
                        </div>
                        <button type="button" onClick={cancelChatInput} aria-label={tr('cancel')}
                          className="min-h-12 min-w-12 shrink-0 rounded text-neutral-600 hover:text-neutral-300 transition-colors focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                          </svg>
                        </button>
                      </div>
                    )}
                    <div className={`flex items-end gap-2 rounded-xl px-3 py-2 border transition-colors ${
                      chatEditingId ? "bg-violet-500/5 border-violet-500/20 focus-within:border-violet-500/40" :
                      chatReplyTo ? "bg-cyan-500/5 border-cyan-500/20 focus-within:border-cyan-500/40" :
                      "bg-neutral-900/30 border-neutral-800/40 focus-within:border-neutral-700/50"
                    }`}>
                      <textarea
                        ref={chatInputRef}
                        aria-label={chatTr("Message")}
                        value={chatContent}
                        onChange={e => { chatRevision.current += 1; setChatContent(e.target.value); setChatError(null); }}
                        placeholder={chatEditingId ? tr('editYourMessage') : chatReplyTo ? tr('replyToName', { name: chatReplyTo.sender_name }) : tr('writeAMessage')}
                        rows={1}
                        className="flex-1 min-w-0 min-h-12 rounded bg-transparent text-sm text-neutral-200 placeholder-neutral-600 resize-none max-h-28 overflow-y-auto font-mono leading-relaxed focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400"
                        onKeyDown={e => {
                          if (e.key === "Escape") { cancelChatInput(); return; }
                          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleChatSend(); }
                        }}
                        onInput={e => {
                          const t = e.target as HTMLTextAreaElement;
                          t.style.height = "auto";
                          t.style.height = Math.min(t.scrollHeight, 112) + "px";
                        }}
                      />
                      <button
                        type="submit"
                        aria-label={chatEditingId ? chatTr("Save") : chatTr("Send")}
                        disabled={chatSending || !chatContent.trim()}
                        className={`shrink-0 min-h-12 min-w-12 rounded-full focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400 flex items-center justify-center transition-all duration-200 ${
                          !chatContent.trim() || chatSending
                            ? "bg-neutral-800/40 text-neutral-600 cursor-not-allowed"
                            : chatEditingId ? "bg-violet-500 text-white hover:bg-violet-400 hover:scale-105"
                            : "bg-white text-black hover:bg-neutral-200 hover:scale-105"
                        }`}
                      >
                        {chatSending ? (
                          <span className="text-xs animate-pulse">...</span>
                        ) : chatEditingId ? (
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        ) : (
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
                          </svg>
                        )}
                      </button>
                    </div>
                    <p className="text-xs text-neutral-700 font-mono mt-1.5 text-center">
                      {chatEditingId ? tr('enterToSaveEscToCancel') : chatReplyTo ? tr('enterToReplyEscToCancel') : tr('enterToSendShiftEnterForNewline')}
                    </p>
                  </form>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── Contributors ── */}
        {tab === "contributors" && (
          <div className="space-y-4 fade-up" style={{ animationDelay: "150ms" }}>
            <div className="flex items-center justify-between">
              <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">
                {tr('humanContributors')}</p>
              {!isContributor && !joinMsg && (
                <button onClick={handleJoin} disabled={joining}
                  className="text-xs bg-white text-black font-medium font-mono disabled:opacity-50 px-3.5 py-1.5 rounded-lg transition-all hover:opacity-90">
                  {joining ? tr('requesting') : tr('requestToJoin')}
                </button>
              )}
              {joinMsg && <p className="text-xs text-emerald-400 font-mono">{displayPublicLabel(locale, joinMsg)}</p>}
            </div>

            {contributors.length === 0 ? (
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-10 text-center text-neutral-600 text-sm font-mono">
                {tr('noContributorsYet')}{" "}
                {!auth && (
                  <button onClick={() => setShowLogin(true)} className="text-neutral-400 hover:text-violet-400 underline transition-colors">
                    {tr('signIn')}</button>
                )}{" "}{tr('toBeTheFirst')}</div>
            ) : (
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm divide-y divide-neutral-800/40 overflow-hidden">
                {contributors.map(c => (
                  <div key={c.id} className="flex items-center gap-3 px-5 py-4 hover:bg-neutral-800/20 transition-colors">
                    <div className="w-8 h-8 rounded-lg bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-xs font-bold text-cyan-400 font-mono">
                      {(c.user_name || c.user_email)[0].toUpperCase()}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-white">{c.user_name || c.user_email.split("@")[0]}</span>
                        <Badge cls={c.role === "admin"
                          ? "bg-violet-500/15 text-violet-400 border border-violet-500/20"
                          : "bg-neutral-800/40 text-neutral-500 border border-neutral-700/30"}>
                          {c.role}
                        </Badge>
                      </div>
                      <p className="text-[10px] text-neutral-600 mt-0.5 font-mono">
                        {new Intl.NumberFormat(localeTag(locale)).format(c.contribution_points)} {tr('ptsJoined')}{timeAgo(c.joined_at, locale)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Ownership ── */}
        {tab === "ownership" && ownership && (
          <div className="space-y-6 fade-up" style={{ animationDelay: "150ms" }}>
            <div>
              <div className="flex items-center justify-between mb-4">
                <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">{tr('agentContributors')}</p>
                <span className="text-[10px] text-neutral-600 font-mono">
                  {new Intl.NumberFormat(localeTag(locale)).format(ownership.contributors.reduce((s, c) => s + c.contribution_points, 0))} {tr('totalPoints')}</span>
              </div>
              {ownership.contributors.length === 0 ? (
                <p className="text-neutral-600 text-sm font-mono">{tr('noAgentContributorsYet')}</p>
              ) : (
                <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm px-5 divide-y divide-neutral-800/40 overflow-hidden">
                  {ownership.contributors.map(c => (
                    <div key={c.agent_id} className="flex items-center gap-3 py-4 hover:bg-neutral-800/20 transition-colors">
                      <div className="w-7 h-7 rounded-lg bg-violet-500/15 border border-violet-500/20 flex items-center justify-center text-[10px] font-bold text-violet-400 font-mono">
                        {c.agent_name[0].toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm text-white font-medium truncate">{c.agent_name}</div>
                        <div className="flex items-center gap-2 mt-1">
                          <div className="flex-1 h-1 rounded-full bg-neutral-800/50 overflow-hidden">
                            <div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-cyan-500"
                              style={{ width: `${Number.isFinite(c.share_pct) ? Math.min(c.share_pct, 100) : 0}%` }} />
                          </div>
                          <span className="text-[10px] text-neutral-400 tabular-nums font-mono">{Number.isFinite(c.share_pct) ? c.share_pct.toFixed(1) : "0.0"}%</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
        {tab === "ownership" && !ownership && (
          <p className="text-neutral-600 text-sm font-mono fade-up">{tr('noOwnershipDataAvailable')}</p>
        )}
      </main>
    </div>
  );
}
