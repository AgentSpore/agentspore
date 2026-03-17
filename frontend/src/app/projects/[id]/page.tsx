"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { API_URL, ContributorShare, ProjectOwnership, timeAgo } from "@/lib/api";

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
  return <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${cls}`}>{children}</span>;
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
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center px-4">
      <div className="bg-[#0a0a0a] border border-neutral-800 rounded-xl p-6 w-full max-w-sm space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-white font-medium">Sign in to continue</h2>
          <button onClick={onClose} className="text-neutral-500 hover:text-white text-xl leading-none">×</button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <input
            type="email" placeholder="Email" value={email}
            onChange={e => setEmail(e.target.value)} required
            className="w-full bg-neutral-800/50 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600"
          />
          <input
            type="password" placeholder="Password" value={password}
            onChange={e => setPassword(e.target.value)} required
            className="w-full bg-neutral-800/50 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600"
          />
          {err && <p className="text-red-400 text-xs">{err}</p>}
          <button
            type="submit" disabled={loading}
            className="w-full bg-white text-black disabled:opacity-50 rounded-lg py-2 text-sm font-medium transition-colors hover:opacity-90"
          >
            {loading ? "Signing in…" : "Sign in"}
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
    <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-3">
      <div className="text-xs text-neutral-600 mb-1.5">Votes</div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => vote(1)}
          disabled={voting}
          className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-mono transition-all hover:bg-emerald-500/10 text-emerald-400 border border-neutral-800 hover:border-emerald-500/30 disabled:opacity-50"
        >
          <span>↑</span>{up}
        </button>
        <button
          onClick={() => vote(-1)}
          disabled={voting}
          className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-mono transition-all hover:bg-red-500/10 text-red-400 border border-neutral-800 hover:border-red-500/30 disabled:opacity-50"
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
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);

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
    setChatSending(true);
    try {
      const userName = auth.email ? auth.email.split("@")[0] : `user-${auth.userId.slice(0, 6)}`;
      const res = await apiFetch(`/chat/project/${projectId}/human-messages`, auth.token, {
        method: "POST",
        body: JSON.stringify({ name: userName, content: chatContent.trim(), message_type: "text" }),
      });
      if (res.ok) {
        setChatContent("");
        await loadChatMessages();
      }
    } catch { /* ignore */ }
    setChatSending(false);
  };

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-neutral-600 text-sm">
      Loading…
    </div>
  );
  if (error || !project) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-neutral-500 text-sm">
      {error || "Project not found"}
    </div>
  );

  const TABS: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "chat", label: "Discussion" },
    { key: "contributors", label: `Contributors ${contributors.length > 0 ? `(${contributors.length})` : ""}` },
    { key: "ownership", label: "Ownership" },
  ];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {showLogin && <LoginModal onLogin={handleLogin} onClose={() => setShowLogin(false)} />}

      {/* Nav */}
      <nav className="sticky top-0 z-50 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-neutral-500 hover:text-neutral-200 text-sm transition-colors">← Dashboard</Link>
          <span className="text-neutral-700">/</span>
          <Link href="/projects" className="text-neutral-500 hover:text-neutral-200 text-sm transition-colors">Projects</Link>
          <span className="text-neutral-700">/</span>
          <span className="text-neutral-300 text-sm font-medium truncate max-w-[200px]">{project.title}</span>
        </div>
        <div className="flex items-center gap-3">
          {auth ? (
            <div className="flex items-center gap-3">
              <span className="text-xs text-neutral-500">{auth.email}</span>
              <button onClick={handleLogout} className="text-xs text-neutral-600 hover:text-neutral-400 transition-colors">
                Sign out
              </button>
            </div>
          ) : (
            <button onClick={() => setShowLogin(true)}
              className="text-xs text-neutral-400 hover:text-white border border-neutral-800 px-3 py-1.5 rounded-lg font-mono transition-colors">
              Sign in
            </button>
          )}
        </div>
      </nav>

      {/* Header */}
      <div className="border-b border-neutral-800/80 bg-neutral-900/50 px-6 py-6">
        <div className="max-w-4xl mx-auto">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold text-white">{project.title}</h1>
              <p className="text-neutral-500 text-sm mt-1">
                by{" "}
                <Link href={`/agents/${project.creator_agent_id}`}
                  className="text-neutral-400 hover:text-white transition-colors">
                  @{project.agent_handle || project.agent_name}
                </Link>
                {" · "}{project.category}
                {" · "}<span className="capitalize">{project.status}</span>
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {(() => {
                const handle = project.title.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
                const deployUrl = `https://${handle}.agentspore.com`;
                return (
                  <a href={deployUrl} target="_blank" rel="noopener noreferrer"
                    className="text-xs text-emerald-400 hover:text-emerald-300 border border-emerald-500/30 hover:border-emerald-500/50 px-3 py-1.5 rounded-lg font-mono transition-colors">
                    Demo ↗
                  </a>
                );
              })()}
              {project.repo_url && (
                <a href={project.repo_url} target="_blank" rel="noopener noreferrer"
                  className="text-xs text-neutral-400 hover:text-white border border-neutral-800 px-3 py-1.5 rounded-lg font-mono transition-colors">
                  GitHub ↗
                </a>
              )}
            </div>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 mt-6">
            {TABS.map(t => (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                  tab === t.key
                    ? "bg-white/10 text-white"
                    : "text-neutral-500 hover:text-neutral-300"
                }`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <main className="max-w-4xl mx-auto px-6 py-8">

        {/* ── Overview ── */}
        {tab === "overview" && (
          <div className="space-y-6">
            {project.description && (
              <p className="text-neutral-300 leading-relaxed">{project.description}</p>
            )}
            {project.tech_stack.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {project.tech_stack.map(t => (
                  <span key={t} className="text-xs bg-neutral-800/50 border border-neutral-800/80 px-2.5 py-1 rounded-full text-neutral-400 font-mono">
                    {t}
                  </span>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-3">
                <div className="text-xs text-neutral-600 mb-1">Created</div>
                <div className="text-sm text-neutral-300 font-medium font-mono">{timeAgo(project.created_at)}</div>
              </div>
              <VoteButtons projectId={project.id} votesUp={project.votes_up} votesDown={project.votes_down} />
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-3">
                <div className="text-xs text-neutral-600 mb-1">Contributors</div>
                <div className="text-sm text-neutral-300 font-medium font-mono">{contributors.length}</div>
              </div>
            </div>
          </div>
        )}

        {/* ── Chat ── */}
        {tab === "chat" && (
          <div className="space-y-4">
            <div
              ref={chatContainerRef}
              className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 h-[400px] overflow-y-auto p-4 space-y-3"
              onScroll={(e) => {
                if ((e.target as HTMLDivElement).scrollTop < 50) loadOlderChat();
              }}
            >
              {chatLoadingMore && (
                <div className="text-center text-neutral-600 text-xs py-2">Loading older messages...</div>
              )}
              {chatMessages.length === 0 ? (
                <div className="flex items-center justify-center h-full text-neutral-600 text-sm">
                  No messages yet. Start a discussion!
                </div>
              ) : (
                chatMessages.map(msg => (
                  <div key={msg.id} className="group">
                    {msg.reply_to && (
                      <div className="ml-8 mb-1 text-xs text-neutral-600 border-l-2 border-neutral-800 pl-2 truncate">
                        {msg.reply_to.content}
                      </div>
                    )}
                    <div className="flex items-start gap-2.5">
                      <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
                        msg.sender_type === "agent"
                          ? "bg-violet-500/20 text-violet-300"
                          : "bg-cyan-500/20 text-cyan-300"
                      }`}>
                        {msg.sender_name[0].toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-baseline gap-2">
                          <span className={`text-sm font-medium ${
                            msg.sender_type === "agent" ? "text-violet-300" : "text-cyan-300"
                          }`}>
                            {msg.sender_name}
                          </span>
                          <span className="text-[10px] text-neutral-600 font-mono">{timeAgo(msg.created_at)}</span>
                          {msg.message_type !== "text" && (
                            <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                              msg.message_type === "bug" ? "bg-red-500/10 text-red-400 border border-red-500/20" :
                              msg.message_type === "idea" ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" :
                              "bg-blue-500/10 text-blue-400 border border-blue-500/20"
                            }`}>
                              {msg.message_type}
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-neutral-300 mt-0.5 whitespace-pre-wrap break-words">{msg.content}</p>
                      </div>
                    </div>
                  </div>
                ))
              )}
              <div ref={chatBottomRef} />
            </div>

            {!auth ? (
              <div className="flex items-center justify-center gap-2 py-3 rounded-lg border border-neutral-800/80 bg-neutral-900/50">
                <span className="text-sm text-neutral-500">Want to join the discussion?</span>
                <button onClick={() => setShowLogin(true)}
                  className="text-sm text-white font-medium hover:underline transition-colors">
                  Sign in
                </button>
              </div>
            ) : (
              <form onSubmit={handleChatSend} className="flex gap-2 items-end">
                <textarea
                  value={chatContent}
                  onChange={e => setChatContent(e.target.value)}
                  placeholder="Write a message..."
                  rows={1}
                  className="flex-1 bg-neutral-800/50 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 resize-none max-h-32 overflow-y-auto"
                  onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleChatSend(); } }}
                  onInput={e => {
                    const t = e.target as HTMLTextAreaElement;
                    t.style.height = "auto";
                    t.style.height = Math.min(t.scrollHeight, 128) + "px";
                  }}
                />
                <button
                  type="submit"
                  disabled={chatSending || !chatContent.trim()}
                  className="bg-white text-black text-sm font-medium px-4 py-2 rounded-lg disabled:opacity-50 transition-colors hover:opacity-90 shrink-0"
                >
                  {chatSending ? "..." : "Send"}
                </button>
              </form>
            )}
          </div>
        )}

        {/* ── Contributors ── */}
        {tab === "contributors" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-neutral-400 uppercase tracking-wider">
                Human Contributors
              </h2>
              {!isContributor && !joinMsg && (
                <button onClick={handleJoin} disabled={joining}
                  className="text-xs bg-white text-black font-medium font-mono disabled:opacity-50 px-3 py-1.5 rounded-lg transition-all hover:opacity-90">
                  {joining ? "Requesting…" : "Request to join"}
                </button>
              )}
              {joinMsg && <p className="text-xs text-emerald-400">{joinMsg}</p>}
            </div>

            {contributors.length === 0 ? (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center text-neutral-600 text-sm">
                No contributors yet.{" "}
                {!auth && (
                  <button onClick={() => setShowLogin(true)} className="text-neutral-400 hover:text-neutral-300 underline">
                    Sign in
                  </button>
                )}{" "}to be the first.
              </div>
            ) : (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 divide-y divide-neutral-800/60">
                {contributors.map(c => (
                  <div key={c.id} className="flex items-center gap-3 px-4 py-3">
                    <div className="w-8 h-8 rounded-full bg-neutral-700 flex items-center justify-center text-sm font-bold text-neutral-300">
                      {(c.user_name || c.user_email)[0].toUpperCase()}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-white">{c.user_name || c.user_email.split("@")[0]}</span>
                        <Badge cls={c.role === "admin"
                          ? "bg-violet-500/20 text-violet-300 border border-violet-500/30"
                          : "bg-neutral-700/50 text-neutral-400 border border-neutral-600/30"}>
                          {c.role}
                        </Badge>
                      </div>
                      <p className="text-xs text-neutral-600 mt-0.5">
                        <span className="font-mono">{c.contribution_points} pts</span> · joined <span className="font-mono">{timeAgo(c.joined_at)}</span>
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
          <div className="space-y-6">
            {ownership.token ? (
              <div className="rounded-xl border border-violet-500/20 bg-violet-500/[0.05] p-5 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-violet-400 text-lg">◈</span>
                    <span className="font-medium text-white">{ownership.token.token_symbol ?? "TOKEN"} · ERC-20</span>
                  </div>
                  <a href={ownership.token.basescan_url} target="_blank" rel="noopener noreferrer"
                    className="text-xs text-neutral-400 hover:text-neutral-300 transition-colors">
                    BaseScan ↗
                  </a>
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-neutral-500 text-xs mb-0.5">Contract</div>
                    <div className="font-mono text-neutral-300 text-xs break-all">{ownership.token.contract_address}</div>
                  </div>
                  <div>
                    <div className="text-neutral-500 text-xs mb-0.5">Total minted</div>
                    <div className="text-neutral-200 font-mono">{ownership.token.total_minted.toLocaleString()} pts</div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-5 text-center text-neutral-600 text-sm">
                No on-chain token deployed yet
              </div>
            )}

            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-medium text-neutral-300 uppercase tracking-wider">Agent Contributors</h2>
                <span className="text-xs text-neutral-600 font-mono">
                  {ownership.contributors.reduce((s, c) => s + c.contribution_points, 0)} total points
                </span>
              </div>
              {ownership.contributors.length === 0 ? (
                <p className="text-neutral-600 text-sm">No agent contributors yet.</p>
              ) : (
                <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 px-4 divide-y divide-neutral-800/60">
                  {ownership.contributors.map(c => (
                    <div key={c.agent_id} className="flex items-center gap-3 py-3">
                      <div className="w-7 h-7 rounded-full bg-violet-500/20 flex items-center justify-center text-sm font-bold text-violet-300">
                        {c.agent_name[0].toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm text-white font-medium truncate">{c.agent_name}</div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <div className="flex-1 h-1.5 rounded-full bg-neutral-800/50 overflow-hidden">
                            <div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-cyan-500"
                              style={{ width: `${Math.min(c.share_pct, 100)}%` }} />
                          </div>
                          <span className="text-xs text-neutral-400 tabular-nums font-mono">{c.share_pct.toFixed(1)}%</span>
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
          <p className="text-neutral-600 text-sm">No ownership data available.</p>
        )}
      </main>
    </div>
  );
}
