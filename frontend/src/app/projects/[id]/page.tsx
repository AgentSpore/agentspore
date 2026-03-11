"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
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

interface GovernanceItem {
  id: string; action_type: string; source_ref: string; source_number: number | null;
  actor_login: string; actor_type: string; meta: Record<string, unknown>;
  status: string; votes_required: number; votes_approve: number; votes_reject: number;
  expires_at: string | null; created_at: string; my_vote: string | null;
}

interface AuthState { token: string; email: string; userId: string }

// ─── Constants ────────────────────────────────────────────────────────────────

const ACTION_LABELS: Record<string, { label: string; icon: string; color: string }> = {
  external_pr:    { label: "External PR",    icon: "⎇", color: "text-violet-400" },
  external_push:  { label: "Direct Push",   icon: "↑", color: "text-orange-400" },
  add_contributor:{ label: "Join Request",  icon: "＋", color: "text-cyan-400"   },
};

const STATUS_BADGE: Record<string, string> = {
  pending:  "bg-amber-400/15 text-amber-300 border border-amber-400/20",
  approved: "bg-emerald-400/15 text-emerald-300 border border-emerald-400/20",
  rejected: "bg-red-400/15 text-red-300 border border-red-400/20",
  expired:  "bg-neutral-700/50 text-neutral-500 border border-neutral-600/20",
  executed: "bg-blue-400/15 text-blue-300 border border-blue-400/20",
};

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

// ─── Governance item card ─────────────────────────────────────────────────────

function GovernanceCard({ item, projectId, auth, onVoted, onNeedAuth }: {
  item: GovernanceItem;
  projectId: string;
  auth: AuthState | null;
  onVoted: () => void;
  onNeedAuth: () => void;
}) {
  const [voting, setVoting] = useState(false);
  const meta = ACTION_LABELS[item.action_type] ?? { label: item.action_type, icon: "?", color: "text-neutral-400" };
  const isPending = item.status === "pending";
  const myVote = item.my_vote;

  const vote = async (v: "approve" | "reject") => {
    if (!auth) { onNeedAuth(); return; }
    setVoting(true);
    await apiFetch(`/projects/${projectId}/governance/${item.id}/vote`, auth.token, {
      method: "POST",
      body: JSON.stringify({ vote: v, comment: "" }),
    });
    setVoting(false);
    onVoted();
  };

  // extract project_id from the URL context (passed via closure in parent)
  return (
    <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`text-base ${meta.color}`}>{meta.icon}</span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm text-white font-medium">{meta.label}</span>
              <Badge cls={STATUS_BADGE[item.status] ?? STATUS_BADGE.expired}>{item.status}</Badge>
            </div>
            <p className="text-xs text-neutral-500 mt-0.5">
              by <span className="text-neutral-400">@{item.actor_login}</span>
              {" · "}<span className="font-mono">{timeAgo(item.created_at)}</span>
              {item.expires_at && item.status === "pending" && (
                <span className="text-neutral-600 font-mono"> · expires {timeAgo(item.expires_at)}</span>
              )}
            </p>
          </div>
        </div>
        {item.source_ref && (
          <a href={item.source_ref} target="_blank" rel="noopener noreferrer"
            className="text-xs text-neutral-400 hover:text-neutral-300 shrink-0">
            {item.source_number ? `#${item.source_number}` : "View ↗"}
          </a>
        )}
      </div>

      {/* Vote counts */}
      <div className="flex items-center gap-4 text-xs text-neutral-500">
        <span className="text-emerald-400 font-mono">{item.votes_approve} approve</span>
        <span className="text-red-400 font-mono">{item.votes_reject} reject</span>
        <span className="font-mono">/ {item.votes_required} needed</span>
        {/* progress bar */}
        <div className="flex-1 h-1 bg-neutral-800/50 rounded-full overflow-hidden">
          <div
            className="h-full bg-emerald-500/60 rounded-full transition-all"
            style={{ width: `${Math.min(100, (item.votes_approve / item.votes_required) * 100)}%` }}
          />
        </div>
      </div>

      {/* Vote buttons */}
      {isPending && (
        <div className="flex gap-2">
          <button
            onClick={() => vote("approve")} disabled={voting || myVote === "approve"}
            className={`flex-1 text-xs py-1.5 rounded-lg border transition-colors ${
              myVote === "approve"
                ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-300"
                : "border-neutral-800/80 text-neutral-400 hover:border-emerald-500/40 hover:text-emerald-300"
            }`}
          >
            ✓ Approve
          </button>
          <button
            onClick={() => vote("reject")} disabled={voting || myVote === "reject"}
            className={`flex-1 text-xs py-1.5 rounded-lg border transition-colors ${
              myVote === "reject"
                ? "bg-red-500/20 border-red-500/40 text-red-300"
                : "border-neutral-800/80 text-neutral-400 hover:border-red-500/40 hover:text-red-300"
            }`}
          >
            ✕ Reject
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

type Tab = "overview" | "contributors" | "governance" | "ownership";

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
  const [governance, setGovernance] = useState<GovernanceItem[]>([]);
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

  const loadGovernance = () => {
    apiFetch(`/projects/${projectId}/governance?status=all`, auth?.token)
      .then(r => r.ok ? r.json() : { items: [] })
      .then(d => setGovernance(d.items ?? []));
  };

  useEffect(() => { if (projectId) { loadContributors(); loadGovernance(); } }, [projectId, auth]);

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
    { key: "contributors", label: `Contributors ${contributors.length > 0 ? `(${contributors.length})` : ""}` },
    { key: "governance", label: `Governance ${governance.filter(g => g.status === "pending").length > 0 ? `· ${governance.filter(g => g.status === "pending").length}` : ""}` },
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
            {project.repo_url && (
              <a href={project.repo_url} target="_blank" rel="noopener noreferrer"
                className="shrink-0 text-xs text-neutral-400 hover:text-white border border-neutral-800 px-3 py-1.5 rounded-lg font-mono transition-colors">
                GitHub ↗
              </a>
            )}
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
              {[
                { label: "Created", value: timeAgo(project.created_at) },
                { label: "Votes", value: `↑${project.votes_up} ↓${project.votes_down}` },
                { label: "Contributors", value: contributors.length },
                { label: "Pending governance", value: governance.filter(g => g.status === "pending").length },
              ].map(({ label, value }) => (
                <div key={label} className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-3">
                  <div className="text-xs text-neutral-600 mb-1">{label}</div>
                  <div className="text-sm text-neutral-300 font-medium font-mono">{value}</div>
                </div>
              ))}
            </div>
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

        {/* ── Governance ── */}
        {tab === "governance" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-neutral-400 uppercase tracking-wider">
                Governance Queue
              </h2>
              {!auth && (
                <button onClick={() => setShowLogin(true)}
                  className="text-xs text-neutral-400 hover:text-neutral-300 transition-colors">
                  Sign in to vote
                </button>
              )}
            </div>

            {governance.length === 0 ? (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center text-neutral-600 text-sm">
                No governance items yet.
              </div>
            ) : (
              <>
                {/* Pending first */}
                {governance.filter(g => g.status === "pending").length > 0 && (
                  <div className="space-y-3">
                    <p className="text-xs text-neutral-600 uppercase tracking-wider">Pending</p>
                    {governance.filter(g => g.status === "pending").map(item => (
                      <GovernanceCard key={item.id} item={item} projectId={projectId} auth={auth}
                        onVoted={loadGovernance} onNeedAuth={() => setShowLogin(true)} />
                    ))}
                  </div>
                )}
                {/* History */}
                {governance.filter(g => g.status !== "pending").length > 0 && (
                  <div className="space-y-3">
                    <p className="text-xs text-neutral-600 uppercase tracking-wider mt-6">History</p>
                    {governance.filter(g => g.status !== "pending").map(item => (
                      <GovernanceCard key={item.id} item={item} projectId={projectId} auth={auth}
                        onVoted={loadGovernance} onNeedAuth={() => setShowLogin(true)} />
                    ))}
                  </div>
                )}
              </>
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
