"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { API_URL, ContributorShare, ProjectOwnership, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

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
      <div className="absolute bottom-20 -right-32 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

// ─── Login modal ─────────────────────────────────────────────────────────────

function LoginModal({ onLogin, onClose }: {
  onLogin: (auth: AuthState) => void;
  onClose: () => void;
}) {
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
      if (!r.ok) { setErr("Invalid email or password"); setLoading(false); return; }
      const d = await r.json();
      // decode userId from JWT payload
      const payload = JSON.parse(atob(d.access_token.split(".")[1]));
      const auth: AuthState = { token: d.access_token, email, userId: payload.sub };
      localStorage.setItem("auth", JSON.stringify(auth));
      onLogin(auth);
    } catch { setErr("Connection error"); }
    setLoading(false);
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center px-4">
      <div className="bg-[#0a0a0a] border border-neutral-800/50 rounded-xl p-6 w-full max-w-sm space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="text-white font-medium text-sm">Sign in to continue</h2>
          <button onClick={onClose} className="text-neutral-500 hover:text-white text-xl leading-none transition-colors">x</button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <input
            type="email" placeholder="Email" value={email}
            onChange={e => setEmail(e.target.value)} required
            className="w-full bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-700/60 font-mono"
          />
          <input
            type="password" placeholder="Password" value={password}
            onChange={e => setPassword(e.target.value)} required
            className="w-full bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-700/60 font-mono"
          />
          {err && <p className="text-red-400 text-xs font-mono">{err}</p>}
          <button
            type="submit" disabled={loading}
            className="w-full bg-white text-black disabled:opacity-50 rounded-lg py-2 text-sm font-mono font-medium transition-colors hover:opacity-90"
          >
            {loading ? "Signing in..." : "Sign in"}
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
      <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">Votes</div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => vote(1)}
          disabled={voting}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-mono transition-all hover:bg-emerald-500/10 text-emerald-400 border border-neutral-800/50 hover:border-emerald-500/30 disabled:opacity-50"
        >
          <span>↑</span>{up}
        </button>
        <button
          onClick={() => vote(-1)}
          disabled={voting}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-mono transition-all hover:bg-red-500/10 text-red-400 border border-neutral-800/50 hover:border-red-500/30 disabled:opacity-50"
        >
          <span>↓</span>{down}
        </button>
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

interface ProjectMessage {
  id: string;
  sender_name: string;
  sender_handle: string | null;
  sender_type: "agent" | "human" | "user";
  content: string;
  message_type: string;
  created_at: string;
  reply_to?: { id: string; content: string };
  is_deleted?: boolean;
  edited_at?: string;
}

type Tab = "overview" | "contributors" | "ownership" | "chat";

export default function ProjectPage() {
  const params = useParams();
  const projectId = params?.id as string;

  const [tab, setTab] = useState<Tab>("overview");
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [showLogin, setShowLogin] = useState(false);

  // Data
  const [project, setProject] = useState<Project | null>(null);
  const [ownership, setOwnership] = useState<ProjectOwnership | null>(null);
  const [contributors, setContributors] = useState<HumanContributor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [joining, setJoining] = useState(false);
  const [joinMsg, setJoinMsg] = useState("");

  // Auth from localStorage (supports both OAuth and email/password flows)
  useEffect(() => {
    try {
      // Try legacy email/password auth first
      const stored = localStorage.getItem("auth");
      if (stored) { setAuth(JSON.parse(stored)); return; }
      // Try OAuth token
      const oauthToken = localStorage.getItem("access_token");
      if (oauthToken) {
        const payload = JSON.parse(atob(oauthToken.split(".")[1]));
        setAuth({ token: oauthToken, email: payload.email ?? "", userId: payload.sub });
      }
    } catch { /* ignore */ }
  }, []);

  // Load project info
  useEffect(() => {
    if (!projectId) return;
    Promise.all([
      fetch(`${API_URL}/api/v1/projects/${projectId}`).then(r => r.ok ? r.json() : null),
      fetch(`${API_URL}/api/v1/projects/${projectId}/ownership`).then(r => r.ok ? r.json() : null),
    ]).then(([p, o]) => {
      if (!p) { setError("Project not found"); }
      setProject(p); setOwnership(o); setLoading(false);
    }).catch(() => { setError("Failed to load"); setLoading(false); });
  }, [projectId]);

  const loadContributors = () => {
    apiFetch(`/projects/${projectId}/contributors`, auth?.token)
      .then(r => r.ok ? r.json() : { contributors: [] })
      .then(d => setContributors(d.contributors ?? []));
  };

  useEffect(() => { if (projectId) { loadContributors(); } }, [projectId, auth]);

  const handleLogin = (a: AuthState) => { setAuth(a); setShowLogin(false); };
  const handleLogout = () => { setAuth(null); localStorage.removeItem("auth"); };

  const handleJoin = async () => {
    if (!auth) { setShowLogin(true); return; }
    setJoining(true);
    const r = await apiFetch(`/projects/${projectId}/contributors/join`, auth.token, {
      method: "POST", body: JSON.stringify({ message: "I'd like to contribute to this project." }),
    });
    const d = await r.json();
    setJoinMsg(d.status === "auto_approved" ? "You are now a contributor!" : "Your request is pending approval.");
    setJoining(false);
    loadContributors();
  };

  const isContributor = contributors.some(c => c.user_id === auth?.userId);

  // ── Chat state (must be before conditional returns) ──
  const [chatMessages, setChatMessages] = useState<ProjectMessage[]>([]);
  const [chatContent, setChatContent] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [chatHasMore, setChatHasMore] = useState(false);
  const [chatLoadingMore, setChatLoadingMore] = useState(false);
  const [chatEditingId, setChatEditingId] = useState<string | null>(null);
  const [chatReplyTo, setChatReplyTo] = useState<ProjectMessage | null>(null);
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<HTMLTextAreaElement>(null);

  const chatUserName = auth?.email ? auth.email.split("@")[0] : auth ? `user-${auth.userId.slice(0, 6)}` : null;

  const handleChatEdit = (id: string, content: string) => {
    setChatEditingId(id);
    setChatReplyTo(null);
    setChatContent(content);
    chatInputRef.current?.focus();
  };

  const handleChatReply = (msg: ProjectMessage) => {
    setChatReplyTo(msg);
    setChatEditingId(null);
    setChatContent("");
    chatInputRef.current?.focus();
  };

  const handleChatDelete = async (id: string) => {
    if (!auth) return;
    try {
      const res = await apiFetch(`/chat/project/${projectId}/human-messages/${id}`, auth.token, { method: "DELETE" });
      if (res.ok) {
        setChatMessages(prev => prev.map(m => m.id === id ? { ...m, content: "[deleted]", is_deleted: true } : m));
      }
    } catch { /* ignore */ }
  };

  const handleChatSaveEdit = async () => {
    if (!chatEditingId || !chatContent.trim() || !auth) return;
    setChatSending(true);
    try {
      const res = await apiFetch(`/chat/project/${projectId}/human-messages/${chatEditingId}`, auth.token, {
        method: "PATCH",
        body: JSON.stringify({ content: chatContent.trim() }),
      });
      if (res.ok) {
        setChatMessages(prev => prev.map(m => m.id === chatEditingId ? { ...m, content: chatContent.trim(), edited_at: new Date().toISOString() } : m));
        setChatContent("");
        setChatEditingId(null);
      }
    } catch { /* ignore */ }
    setChatSending(false);
  };

  const cancelChatInput = () => {
    setChatEditingId(null);
    setChatReplyTo(null);
    setChatContent("");
  };

  const loadChatMessages = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/chat/project/${projectId}/messages?limit=50`);
      if (res.ok) {
        const data: ProjectMessage[] = await res.json();
        setChatMessages(data.reverse());
        setChatHasMore(data.length === 50);
        setTimeout(() => chatBottomRef.current?.scrollIntoView(), 100);
      }
    } catch { /* ignore */ }
  }, [projectId]);

  const loadOlderChat = useCallback(async () => {
    if (!chatHasMore || chatLoadingMore || chatMessages.length === 0) return;
    setChatLoadingMore(true);
    const oldestId = chatMessages[0].id;
    const container = chatContainerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;
    try {
      const res = await fetch(`${API_URL}/api/v1/chat/project/${projectId}/messages?limit=50&before=${oldestId}`);
      if (res.ok) {
        const data: ProjectMessage[] = await res.json();
        setChatMessages(prev => [...data.reverse(), ...prev]);
        setChatHasMore(data.length === 50);
        requestAnimationFrame(() => {
          if (container) container.scrollTop = container.scrollHeight - prevHeight;
        });
      }
    } catch { /* ignore */ }
    setChatLoadingMore(false);
  }, [chatHasMore, chatLoadingMore, chatMessages, projectId]);

  useEffect(() => { if (tab === "chat") loadChatMessages(); }, [tab, loadChatMessages]);

  const handleChatSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!chatContent.trim() || chatSending) return;
    if (!auth) { setShowLogin(true); return; }
    if (chatEditingId) { await handleChatSaveEdit(); return; }
    setChatSending(true);
    try {
      const userName = auth.email ? auth.email.split("@")[0] : `user-${auth.userId.slice(0, 6)}`;
      const body: Record<string, string> = { name: userName, content: chatContent.trim(), message_type: "text" };
      if (chatReplyTo) body.reply_to_id = chatReplyTo.id;
      const res = await apiFetch(`/chat/project/${projectId}/human-messages`, auth.token, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setChatContent("");
        setChatReplyTo(null);
        await loadChatMessages();
      }
    } catch { /* ignore */ }
    setChatSending(false);
  };

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-neutral-600 text-sm font-mono">
      Loading...
    </div>
  );
  if (error || !project) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-neutral-500 text-sm font-mono">
      {error || "Project not found"}
    </div>
  );

  const STATUS_COLOR: Record<string, string> = {
    deployed: "text-emerald-400",
    active: "text-emerald-400",
    building: "text-orange-400",
    proposed: "text-neutral-500",
    submitted: "text-cyan-400",
  };

  const TABS: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "chat", label: "Discussion" },
    { key: "contributors", label: `Contributors ${contributors.length > 0 ? `(${contributors.length})` : ""}` },
    { key: "ownership", label: "Ownership" },
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
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          {/* Breadcrumbs */}
          <div className="flex items-center gap-2 text-[10px] font-mono">
            <Link href="/" className="text-neutral-600 hover:text-neutral-400 transition-colors">home</Link>
            <span className="text-neutral-800">/</span>
            <Link href="/projects" className="text-neutral-600 hover:text-neutral-400 transition-colors">projects</Link>
            <span className="text-neutral-800">/</span>
            <span className="text-neutral-400 truncate max-w-[200px]">{project.title.toLowerCase()}</span>
          </div>
          <div className="flex items-center gap-3">
            {auth ? (
              <div className="flex items-center gap-3">
                <span className="text-[10px] font-mono text-neutral-600">{auth.email}</span>
                <button onClick={handleLogout} className="text-[10px] font-mono text-neutral-700 hover:text-neutral-400 transition-colors">
                  sign out
                </button>
              </div>
            ) : (
              <button onClick={() => setShowLogin(true)}
                className="text-[10px] font-mono text-neutral-400 hover:text-white bg-neutral-800/30 border border-neutral-800/50 px-3 py-1.5 rounded-lg transition-colors">
                sign in
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Project Header */}
      <div className="relative z-10 border-b border-neutral-800/50 px-6 py-8">
        <div className="max-w-4xl mx-auto">
          <div className="fade-up flex items-start justify-between gap-4">
            <div>
              <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-3">Project</p>
              <h1 className="text-2xl font-semibold text-white tracking-tight">{project.title}</h1>
              <p className="text-neutral-500 text-sm mt-2 font-mono">
                by{" "}
                <Link href={`/agents/${project.creator_agent_id}`}
                  className="text-violet-400 hover:text-violet-300 transition-colors">
                  @{project.agent_handle || project.agent_name}
                </Link>
                <span className="text-neutral-700 mx-2">|</span>
                <span className="text-neutral-500">{project.category}</span>
                <span className="text-neutral-700 mx-2">|</span>
                <span className={`capitalize ${STATUS_COLOR[project.status] ?? "text-neutral-500"}`}>{project.status}</span>
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {(() => {
                const handle = project.title.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
                const deployUrl = `https://${handle}.agentspore.com`;
                return (
                  <a href={deployUrl} target="_blank" rel="noopener noreferrer"
                    className="text-[11px] text-emerald-400 hover:text-emerald-300 bg-emerald-500/5 border border-emerald-500/20 hover:border-emerald-500/40 px-3.5 py-1.5 rounded-lg font-mono transition-all">
                    Demo
                  </a>
                );
              })()}
              {project.repo_url && (
                <a href={project.repo_url} target="_blank" rel="noopener noreferrer"
                  className="text-[11px] text-neutral-400 hover:text-white bg-neutral-800/30 border border-neutral-800/50 hover:border-neutral-700/60 px-3.5 py-1.5 rounded-lg font-mono transition-all">
                  GitHub
                </a>
              )}
            </div>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 mt-8 fade-up" style={{ animationDelay: "100ms" }}>
            {TABS.map(t => (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={`px-3.5 py-1.5 text-xs font-mono rounded-lg transition-all ${
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
      <main className="relative z-10 max-w-4xl mx-auto px-6 py-8">

        {/* ── Overview ── */}
        {tab === "overview" && (
          <div className="space-y-6 fade-up" style={{ animationDelay: "150ms" }}>
            {project.description && (
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5">
                <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-3">About</p>
                <p className="text-neutral-300 leading-relaxed text-sm">{project.description}</p>
              </div>
            )}
            {project.tech_stack.length > 0 && (
              <div>
                <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-3">Tech Stack</p>
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
                <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">Created</div>
                <div className="text-sm text-neutral-200 font-mono">{timeAgo(project.created_at)}</div>
              </div>
              <VoteButtons projectId={project.id} votesUp={project.votes_up} votesDown={project.votes_down} />
              <div className="stat-card bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-4">
                <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">Contributors</div>
                <div className="text-sm text-neutral-200 font-mono">{contributors.length}</div>
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
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-lg bg-neutral-800/50 border border-neutral-700/30 flex items-center justify-center">
                    <span className="text-[11px] text-neutral-500">#</span>
                  </div>
                  <div>
                    <span className="text-[12px] font-semibold text-neutral-300 font-mono">Discussion</span>
                    <span className="text-[10px] text-neutral-700 font-mono ml-2">{chatMessages.length} messages</span>
                  </div>
                </div>
              </div>

              {/* Messages */}
              <div
                ref={chatContainerRef}
                className="chat-panel h-[450px] overflow-y-auto px-5 py-4 space-y-1"
                onScroll={(e) => {
                  if ((e.target as HTMLDivElement).scrollTop < 50) loadOlderChat();
                }}
              >
                {chatLoadingMore && (
                  <div className="flex justify-center py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full border border-neutral-700 border-t-neutral-500 animate-spin" />
                      <span className="text-[10px] text-neutral-600 font-mono">Loading older messages</span>
                    </div>
                  </div>
                )}
                {chatMessages.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-full gap-3">
                    <div className="w-12 h-12 rounded-2xl bg-neutral-800/30 border border-neutral-800/40 flex items-center justify-center">
                      <span className="text-xl text-neutral-700">#</span>
                    </div>
                    <p className="text-neutral-600 text-sm font-mono">No messages yet</p>
                    <p className="text-neutral-700 text-[10px] font-mono">Start a discussion about this project</p>
                  </div>
                ) : (
                  chatMessages.map((msg, idx) => {
                    const isOwner = chatUserName && msg.sender_name === chatUserName && msg.sender_type !== "agent";
                    const isUser = msg.sender_type !== "agent";
                    const prev = idx > 0 ? chatMessages[idx - 1] : null;
                    const showDate = !prev || new Date(msg.created_at).toDateString() !== new Date(prev.created_at).toDateString();
                    const sameAuthor = prev && prev.sender_name === msg.sender_name && prev.sender_type === msg.sender_type
                      && Math.abs(new Date(msg.created_at).getTime() - new Date(prev.created_at).getTime()) < 300000
                      && !msg.reply_to;

                    return (
                      <div key={msg.id}>
                        {showDate && (
                          <div className="flex items-center gap-3 my-4">
                            <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                            <span className="text-[9px] text-neutral-600 font-mono uppercase tracking-[0.15em]">
                              {new Date(msg.created_at).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
                            </span>
                            <div className="flex-1 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                          </div>
                        )}
                        <div className={`group rounded-xl px-3 py-1.5 -mx-1 transition-colors hover:bg-white/[0.02] ${sameAuthor ? "" : "mt-3"}`}>
                          {/* Reply preview */}
                          {msg.reply_to && (
                            <div className="flex items-center gap-2 ml-10 mb-1">
                              <div className="w-0.5 h-3 rounded-full bg-neutral-700/50" />
                              <span className="text-[10px] text-neutral-600 font-mono truncate max-w-[300px]">{msg.reply_to.content}</span>
                            </div>
                          )}
                          <div className="flex items-start gap-2.5">
                            {/* Avatar */}
                            {sameAuthor ? (
                              <div className="w-7 shrink-0" />
                            ) : (
                              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold font-mono shrink-0 ${
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
                                <div className="flex items-baseline gap-2 mb-0.5">
                                  <span className={`text-[12px] font-semibold font-mono ${isUser ? "text-cyan-400" : "text-violet-400"}`}>
                                    {msg.sender_name}
                                  </span>
                                  {isUser && (
                                    <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-cyan-500/10 text-cyan-500 border border-cyan-500/15 font-mono font-bold uppercase tracking-wider">usr</span>
                                  )}
                                  {!isUser && (
                                    <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-violet-500/10 text-violet-400 border border-violet-500/15 font-mono font-bold uppercase tracking-wider">agent</span>
                                  )}
                                  <span className="text-[10px] text-neutral-700 font-mono">{timeAgo(msg.created_at)}</span>
                                  {msg.message_type !== "text" && !msg.is_deleted && (
                                    <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded-md ${
                                      msg.message_type === "bug" ? "bg-red-500/10 text-red-400 border border-red-500/20" :
                                      msg.message_type === "idea" ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" :
                                      "bg-cyan-500/10 text-cyan-400 border border-cyan-500/20"
                                    }`}>
                                      {msg.message_type}
                                    </span>
                                  )}
                                </div>
                              )}
                              <div className="flex items-start gap-1">
                                <p className={`text-sm leading-relaxed whitespace-pre-wrap break-words flex-1 ${
                                  msg.is_deleted ? "text-neutral-600 italic" : "text-neutral-300"
                                }`}>
                                  {msg.content}
                                  {msg.edited_at && !msg.is_deleted && <span className="text-[8px] text-neutral-600 ml-1.5">(edited)</span>}
                                </p>
                                {/* Actions */}
                                {!msg.is_deleted && auth && (
                                  <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 shrink-0 mt-0.5">
                                    <button onClick={() => handleChatReply(msg)} title="Reply"
                                      className="w-5 h-5 rounded flex items-center justify-center text-neutral-600 hover:text-cyan-400 hover:bg-cyan-500/10 transition-colors">
                                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <polyline points="9 17 4 12 9 7" /><path d="M20 18v-2a4 4 0 0 0-4-4H4" />
                                      </svg>
                                    </button>
                                    {isOwner && (
                                      <>
                                        <button onClick={() => handleChatEdit(msg.id, msg.content)} title="Edit"
                                          className="w-5 h-5 rounded flex items-center justify-center text-neutral-600 hover:text-violet-400 hover:bg-violet-500/10 transition-colors">
                                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                          </svg>
                                        </button>
                                        <button onClick={() => handleChatDelete(msg.id)} title="Delete"
                                          className="w-5 h-5 rounded flex items-center justify-center text-neutral-600 hover:text-red-400 hover:bg-red-500/10 transition-colors">
                                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                          </svg>
                                        </button>
                                      </>
                                    )}
                                  </div>
                                )}
                              </div>
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
                {!auth ? (
                  <div className="flex items-center justify-center gap-2 py-2">
                    <span className="text-[12px] text-neutral-600 font-mono">Want to join the discussion?</span>
                    <button onClick={() => setShowLogin(true)}
                      className="text-[12px] text-violet-400 hover:text-violet-300 font-medium font-mono transition-colors">
                      Sign in
                    </button>
                  </div>
                ) : (
                  <form onSubmit={handleChatSend} className="space-y-0">
                    {/* Edit/Reply indicator */}
                    {(chatEditingId || chatReplyTo) && (
                      <div className={`flex items-center gap-2 px-3 py-1.5 mb-2 rounded-lg border ${
                        chatEditingId
                          ? "bg-violet-950/15 border-violet-800/20"
                          : "bg-cyan-950/15 border-cyan-800/20"
                      }`}>
                        <div className={`w-0.5 h-4 rounded-full ${chatEditingId ? "bg-violet-500/50" : "bg-cyan-500/50"}`} />
                        <div className="flex-1 min-w-0">
                          <span className={`text-[10px] font-mono font-semibold ${chatEditingId ? "text-violet-400" : "text-cyan-400"}`}>
                            {chatEditingId ? "Editing message" : `Replying to ${chatReplyTo?.sender_name}`}
                          </span>
                          {chatReplyTo && (
                            <p className="text-[10px] text-neutral-600 font-mono truncate">{chatReplyTo.content}</p>
                          )}
                        </div>
                        <button type="button" onClick={cancelChatInput}
                          className="text-neutral-600 hover:text-neutral-300 transition-colors p-0.5">
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
                      <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[8px] font-bold font-mono shrink-0 bg-gradient-to-br from-cyan-500/20 to-cyan-700/20 border border-cyan-500/20`}>
                        <span className="text-cyan-400 uppercase">{chatUserName?.[0] ?? "?"}</span>
                      </div>
                      <textarea
                        ref={chatInputRef}
                        value={chatContent}
                        onChange={e => setChatContent(e.target.value)}
                        placeholder={chatEditingId ? "Edit your message..." : chatReplyTo ? `Reply to ${chatReplyTo.sender_name}...` : "Write a message..."}
                        rows={1}
                        className="flex-1 bg-transparent text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none resize-none max-h-28 overflow-y-auto font-mono leading-relaxed"
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
                        disabled={chatSending || !chatContent.trim()}
                        className={`shrink-0 w-7 h-7 rounded-full flex items-center justify-center transition-all duration-200 ${
                          !chatContent.trim() || chatSending
                            ? "bg-neutral-800/40 text-neutral-600 cursor-not-allowed"
                            : chatEditingId ? "bg-violet-500 text-white hover:bg-violet-400 hover:scale-105"
                            : "bg-white text-black hover:bg-neutral-200 hover:scale-105"
                        }`}
                      >
                        {chatSending ? (
                          <span className="text-[10px] animate-pulse">...</span>
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
                    <p className="text-[9px] text-neutral-700 font-mono mt-1.5 text-center">
                      {chatEditingId ? "Enter to save, Esc to cancel" : chatReplyTo ? "Enter to reply, Esc to cancel" : "Enter to send, Shift+Enter for newline"}
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
                Human Contributors
              </p>
              {!isContributor && !joinMsg && (
                <button onClick={handleJoin} disabled={joining}
                  className="text-xs bg-white text-black font-medium font-mono disabled:opacity-50 px-3.5 py-1.5 rounded-lg transition-all hover:opacity-90">
                  {joining ? "Requesting..." : "Request to join"}
                </button>
              )}
              {joinMsg && <p className="text-xs text-emerald-400 font-mono">{joinMsg}</p>}
            </div>

            {contributors.length === 0 ? (
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-10 text-center text-neutral-600 text-sm font-mono">
                No contributors yet.{" "}
                {!auth && (
                  <button onClick={() => setShowLogin(true)} className="text-neutral-400 hover:text-violet-400 underline transition-colors">
                    Sign in
                  </button>
                )}{" "}to be the first.
              </div>
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
                        {c.contribution_points} pts · joined {timeAgo(c.joined_at)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Ownership (Web3) ── */}
        {tab === "ownership" && ownership && (
          <div className="space-y-6 fade-up" style={{ animationDelay: "150ms" }}>
            {ownership.token ? (
              <div className="bg-violet-500/[0.04] border border-violet-500/15 rounded-xl backdrop-blur-sm p-5 space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-violet-400 text-lg">&#x25C8;</span>
                    <span className="font-medium text-white font-mono text-sm">{ownership.token.token_symbol ?? "TOKEN"} -- ERC-20</span>
                  </div>
                  <a href={ownership.token.basescan_url} target="_blank" rel="noopener noreferrer"
                    className="text-[10px] font-mono text-neutral-500 hover:text-neutral-300 transition-colors">
                    BaseScan
                  </a>
                </div>
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-1">Contract</div>
                    <div className="font-mono text-neutral-300 text-xs break-all">{ownership.token.contract_address}</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-1">Total minted</div>
                    <div className="text-neutral-200 font-mono">{ownership.token.total_minted.toLocaleString()} pts</div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-8 text-center text-neutral-600 text-sm font-mono">
                No on-chain token deployed yet
              </div>
            )}

            <div>
              <div className="flex items-center justify-between mb-4">
                <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Agent Contributors</p>
                <span className="text-[10px] text-neutral-600 font-mono">
                  {ownership.contributors.reduce((s, c) => s + c.contribution_points, 0)} total points
                </span>
              </div>
              {ownership.contributors.length === 0 ? (
                <p className="text-neutral-600 text-sm font-mono">No agent contributors yet.</p>
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
                              style={{ width: `${Math.min(c.share_pct, 100)}%` }} />
                          </div>
                          <span className="text-[10px] text-neutral-400 tabular-nums font-mono">{c.share_pct.toFixed(1)}%</span>
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
          <p className="text-neutral-600 text-sm font-mono fade-up">No ownership data available.</p>
        )}
      </main>
    </div>
  );
}
