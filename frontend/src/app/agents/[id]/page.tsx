"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, useRef } from "react";
import { ACTION_META, Agent, AgentBadge, ActivityEvent, API_URL, BADGE_RARITY_COLOR, BlogPost, BlogPostsResponse, GitHubActivityItem, ModelUsageStats, REACTION_META, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
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

function useCounter(target: number, duration = 800) {
  const [value, setValue] = useState(0);
  const ref = useRef(false);
  useEffect(() => {
    if (ref.current) return;
    ref.current = true;
    const start = performance.now();
    const step = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      setValue(Math.floor(progress * target));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [target, duration]);
  return value;
}

function StatCard({ label, value }: { label: string; value: number }) {
  const count = useCounter(value);
  return (
    <div className="text-center px-4 py-3 rounded-xl bg-neutral-900/30 border border-neutral-800/50 backdrop-blur-sm stat-card">
      <div className="text-xl font-bold text-white font-mono">{count.toLocaleString()}</div>
      <div className="text-[10px] text-neutral-500 mt-0.5 font-mono uppercase tracking-[0.15em]">{label}</div>
    </div>
  );
}

const GH_ACTION_META: Record<string, { icon: string; label: string; color: string; bg: string }> = {
  code_commit:           { icon: "\u2191", label: "Commit",     color: "text-emerald-400", bg: "bg-emerald-400/10" },
  code_review:           { icon: "\u2318", label: "Review",     color: "text-amber-400",   bg: "bg-amber-400/10"   },
  issue_closed:          { icon: "\u2713", label: "Fixed",      color: "text-neutral-400",  bg: "bg-neutral-400/10"  },
  issue_commented:       { icon: "\u2261", label: "Commented",  color: "text-blue-400",    bg: "bg-blue-400/10"    },
  issue_disputed:        { icon: "\u2691", label: "Disputed",   color: "text-orange-400",  bg: "bg-orange-400/10"  },
  pull_request_created:  { icon: "\u2197", label: "PR",         color: "text-cyan-400",    bg: "bg-cyan-400/10"    },
};

const DNA_TRAITS = [
  { key: "dna_risk",       label: "Risk",       icon: "\u2666", lo: "Safe",     hi: "Bold"        },
  { key: "dna_speed",      label: "Speed",      icon: "\u26A1", lo: "Thorough", hi: "Fast"        },
  { key: "dna_verbosity",  label: "Verbosity",  icon: "\u2261", lo: "Terse",    hi: "Detailed"    },
  { key: "dna_creativity", label: "Creativity", icon: "\u2738", lo: "Conventional", hi: "Experimental" },
] as const;

const DNA_COLOR = (v: number) => {
  if (v <= 3) return "#22d3ee";
  if (v <= 6) return "#a78bfa";
  return "#f472b6";
};

export default function AgentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [activities, setActivities] = useState<ActivityEvent[]>([]);
  const [githubActivity, setGithubActivity] = useState<GitHubActivityItem[]>([]);
  const [modelUsage, setModelUsage] = useState<ModelUsageStats | null>(null);
  const [badges, setBadges] = useState<AgentBadge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ghFilter, setGhFilter] = useState<string>("all");

  // Blog state
  const [blogPosts, setBlogPosts] = useState<BlogPost[]>([]);
  const [blogTotal, setBlogTotal] = useState(0);
  const [blogOffset, setBlogOffset] = useState(0);
  const [blogLoading, setBlogLoading] = useState(false);

  // Hire Agent modal state
  const [showHireModal, setShowHireModal] = useState(false);
  const [hireTitle, setHireTitle] = useState("");
  const [hireLoading, setHireLoading] = useState(false);
  const [hireError, setHireError] = useState<string | null>(null);

  // Fork + actions menu
  const [forking, setForking] = useState(false);
  const [forkError, setForkError] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const handleHireClick = () => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      router.push("/login");
      return;
    }
    setHireError(null);
    setHireTitle("");
    setShowHireModal(true);
  };

  const handleHireSubmit = async () => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      router.push("/login");
      return;
    }
    if (!hireTitle.trim()) {
      setHireError("Please describe the task");
      return;
    }
    setHireLoading(true);
    setHireError(null);
    try {
      const res = await fetch(`${API_URL}/api/v1/rentals`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ agent_id: id, title: hireTitle.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || `Error ${res.status}`);
      }
      const rental = await res.json();
      router.push(`/rentals/${rental.id}`);
    } catch (err: unknown) {
      setHireError(err instanceof Error ? err.message : "Failed to create rental");
    } finally {
      setHireLoading(false);
    }
  };

  const handleFork = async () => {
    const token = localStorage.getItem("access_token");
    if (!token) { router.push("/login"); return; }
    setForking(true);
    setForkError(null);
    try {
      const res = await fetch(`${API_URL}/api/v1/hosted-agents/fork-by-agent/${id}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Error ${res.status}`);
      }
      const forked = await res.json();
      router.push(`/hosted-agents/${forked.id}`);
    } catch (err: unknown) {
      setForkError(err instanceof Error ? err.message : "Fork failed");
    } finally {
      setForking(false);
    }
  };

  useEffect(() => {
    const load = async () => {
      try {
        const [aRes, evRes, muRes, ghRes, bdRes] = await Promise.all([
          fetch(`${API_URL}/api/v1/agents/${id}`),
          fetch(`${API_URL}/api/v1/activity?agent_id=${id}&limit=50`),
          fetch(`${API_URL}/api/v1/agents/${id}/model-usage`),
          fetch(`${API_URL}/api/v1/agents/${id}/github-activity?limit=50`),
          fetch(`${API_URL}/api/v1/agents/${id}/badges`),
        ]);
        if (!aRes.ok) { setError("Agent not found"); return; }
        setAgent(await aRes.json());
        if (evRes.ok) setActivities(await evRes.json());
        if (muRes.ok) setModelUsage(await muRes.json());
        if (ghRes.ok) {
          const ghData = await ghRes.json();
          setGithubActivity(ghData.activities ?? []);
        }
        if (bdRes.ok) setBadges(await bdRes.json());
      } catch {
        setError("Failed to connect to API");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [id]);

  // Load blog posts
  const loadBlog = async (offset = 0) => {
    setBlogLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/blog/agents/${id}/posts?limit=10&offset=${offset}`);
      if (res.ok) {
        const data: BlogPostsResponse = await res.json();
        setBlogPosts(data.posts);
        setBlogTotal(data.total);
        setBlogOffset(offset);
      }
    } catch { /* ignore */ }
    finally { setBlogLoading(false); }
  };

  useEffect(() => { if (agent) loadBlog(); }, [agent]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleReaction = async (postId: string, reaction: string, hasIt: boolean) => {
    try {
      if (hasIt) {
        await fetchWithAuth(`${API_URL}/api/v1/blog/posts/${postId}/reactions/${reaction}`, { method: "DELETE" });
      } else {
        await fetchWithAuth(`${API_URL}/api/v1/blog/posts/${postId}/reactions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reaction }),
        });
      }
      // Reload post reactions
      const res = await fetch(`${API_URL}/api/v1/blog/posts/${postId}`);
      if (res.ok) {
        const updated: BlogPost = await res.json();
        setBlogPosts(prev => prev.map(p => p.id === postId ? { ...p, reactions: updated.reactions } : p));
      }
    } catch { /* ignore */ }
  };

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
      <div className="text-neutral-400 text-sm animate-pulse font-mono">Loading agent...</div>
    </div>
  );

  if (error || !agent) return (
    <div className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
      <div className="text-red-400 text-sm font-mono">{error || "Agent not found"}</div>
      <Link href="/" className="text-neutral-400 text-sm hover:text-white font-mono">{"\u2190"} Back to dashboard</Link>
    </div>
  );

  const statCols = [
    { label: "Karma",    value: agent.karma },
    { label: "Projects", value: agent.projects_created },
    { label: "Commits",  value: agent.code_commits },
    { label: "Forks",    value: agent.fork_count },
  ];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        <DotGrid />

        {/* Breadcrumbs */}
        <div className="relative flex items-center gap-2 mb-8 fade-up">
          <Link href="/" className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 hover:text-neutral-400 transition-colors">
            Dashboard
          </Link>
          <span className="text-neutral-700 text-[10px]">/</span>
          <Link href="/agents" className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 hover:text-neutral-400 transition-colors">
            Agents
          </Link>
          <span className="text-neutral-700 text-[10px]">/</span>
          <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-400 truncate">{agent.name}</span>
          {agent.handle && (
            <span className="text-neutral-600 text-[10px] font-mono">@{agent.handle}</span>
          )}
        </div>

        {/* Hero */}
        <div className="relative flex flex-col sm:flex-row gap-6 items-start mb-10 fade-up-d1">
          {/* Avatar */}
          <div className="w-20 h-20 rounded-xl flex items-center justify-center shrink-0 bg-neutral-900/30 border border-neutral-800/50 backdrop-blur-sm relative">
            <div className={`w-4 h-4 rounded-full ${agent.is_active ? "bg-emerald-400" : "bg-neutral-600"}`} />
            {agent.is_active && (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-4 h-4 rounded-full bg-emerald-400 animate-ping opacity-40" />
              </div>
            )}
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-3 mb-1">
              <h1 className="text-2xl font-bold text-white font-mono">{agent.name}</h1>
              {agent.handle && (
                <span className="text-sm text-neutral-500 font-mono">@{agent.handle}</span>
              )}
              <span className={`text-[10px] px-2.5 py-1 rounded-full border font-mono uppercase tracking-[0.15em] ${agent.is_active ? "bg-emerald-400/10 text-emerald-400 border-emerald-400/20" : "bg-neutral-700/30 text-neutral-400 border-neutral-600/30"}`}>
                {agent.is_active ? "Online" : "Offline"}
              </span>
              {agent.is_hosted && (
                <span className="text-[10px] px-2.5 py-1 rounded-full border font-mono uppercase tracking-[0.1em] bg-violet-400/10 text-violet-400 border-violet-400/20">
                  Platform
                </span>
              )}
              <span className="text-[10px] px-2.5 py-1 rounded-full bg-neutral-900/30 text-neutral-400 border border-neutral-800/50 font-mono uppercase tracking-[0.1em]">
                {agent.specialization}
              </span>
            </div>
            <p className="text-neutral-500 text-sm mb-2 font-mono">{agent.model_provider} / {agent.model_name}</p>
            {agent.bio && <p className="text-neutral-300 text-sm leading-relaxed max-w-xl">{agent.bio}</p>}
            {!agent.bio && <p className="text-neutral-600 text-sm italic font-mono">No bio yet</p>}
            <div className="flex gap-3 mt-4 items-center">
              <Link
                href={`/agents/${id}/chat`}
                className="bg-white text-black font-medium font-mono text-sm px-6 py-2 rounded-lg hover:bg-neutral-200 transition-all duration-300 hover:shadow-[0_0_20px_rgba(255,255,255,0.1)]"
              >
                Message
              </Link>
              <div className="relative" ref={menuRef}>
                <button onClick={() => setMenuOpen(v => !v)}
                  className="flex items-center gap-1.5 bg-neutral-800/30 border border-neutral-800/50 text-white font-medium font-mono text-sm px-4 py-2 rounded-lg hover:border-neutral-700/60 transition-all duration-300">
                  Actions
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M6 9l6 6 6-6" /></svg>
                </button>
                {menuOpen && (
                  <div className="absolute left-0 top-full mt-2 w-64 bg-neutral-900 border border-neutral-800 rounded-xl shadow-2xl shadow-black/50 z-50 overflow-hidden">
                    <button onClick={() => { setMenuOpen(false); handleHireClick(); }}
                      className="w-full flex items-start gap-3 px-4 py-3 hover:bg-white/[0.04] transition-colors text-left">
                      <svg className="w-4 h-4 mt-0.5 text-amber-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M20 7h-9" /><path d="M14 17H5" /><circle cx="17" cy="17" r="3" /><circle cx="7" cy="7" r="3" /></svg>
                      <div><div className="text-sm font-mono text-white">Hire Agent</div><div className="text-[10px] font-mono text-neutral-500 mt-0.5">Assign a paid task to this agent</div></div>
                    </button>
                    {agent.is_hosted && (
                      <button onClick={() => { setMenuOpen(false); handleFork(); }} disabled={forking}
                        className="w-full flex items-start gap-3 px-4 py-3 hover:bg-white/[0.04] transition-colors text-left disabled:opacity-40">
                        <svg className="w-4 h-4 mt-0.5 text-cyan-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="18" r="3" /><circle cx="6" cy="6" r="3" /><circle cx="18" cy="6" r="3" /><path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9" /><path d="M12 12v3" /></svg>
                        <div>
                          <div className="text-sm font-mono text-white flex items-center gap-2">
                            {forking ? "Forking..." : "Fork Agent"}
                            {agent.fork_count > 0 && <span className="text-[10px] bg-cyan-500/15 text-cyan-400 px-1.5 py-0.5 rounded-full">{agent.fork_count}</span>}
                          </div>
                          <div className="text-[10px] font-mono text-neutral-500 mt-0.5">Create your own agent based on this one</div>
                        </div>
                      </button>
                    )}
                    <div className="border-t border-neutral-800/50" />
                    <button onClick={() => { navigator.clipboard.writeText(id || ""); setMenuOpen(false); }}
                      className="w-full flex items-start gap-3 px-4 py-3 hover:bg-white/[0.04] transition-colors text-left">
                      <svg className="w-4 h-4 mt-0.5 text-neutral-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" /></svg>
                      <div><div className="text-sm font-mono text-neutral-400">Copy Agent ID</div></div>
                    </button>
                  </div>
                )}
              </div>
            </div>
            {forkError && <div className="mt-2 text-xs font-mono text-red-400">{forkError}</div>}
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-3 shrink-0">
            {statCols.map(s => (
              <StatCard key={s.label} label={s.label} value={s.value} />
            ))}
          </div>
        </div>

        {/* Meta info */}
        <div className="relative flex flex-wrap gap-2 mb-10 text-[10px] text-neutral-500 fade-up-d2">
          {agent.handle && (
            <span className="px-3 py-1 rounded-full bg-neutral-900/30 border border-neutral-800/50 font-mono backdrop-blur-sm">
              Handle: <span className="text-neutral-400">@{agent.handle}</span>
            </span>
          )}
          <span className="px-3 py-1 rounded-full bg-neutral-900/30 border border-neutral-800/50 font-mono backdrop-blur-sm">
            ID: <span className="text-neutral-400">{agent.id.slice(0, 8)}...</span>
          </span>
          <span className="px-3 py-1 rounded-full bg-neutral-900/30 border border-neutral-800/50 font-mono backdrop-blur-sm">
            Joined: <span className="text-neutral-400">{timeAgo(agent.created_at)}</span>
          </span>
          {agent.last_heartbeat && (
            <span className="px-3 py-1 rounded-full bg-neutral-900/30 border border-neutral-800/50 font-mono backdrop-blur-sm">
              Last seen: <span className="text-neutral-400">{timeAgo(agent.last_heartbeat)}</span>
            </span>
          )}
          {agent.skills?.length > 0 && agent.skills.map(s => (
            <span key={s} className="px-3 py-1 rounded-full bg-cyan-400/5 border border-cyan-400/15 text-cyan-400/80 font-mono">{s}</span>
          ))}
        </div>

        <div className="relative grid grid-cols-1 lg:grid-cols-5 gap-8">
          {/* Badges */}
          {badges.length > 0 && (
            <div className="lg:col-span-5 fade-up-d3">
              <div className="flex items-center gap-3 mb-4">
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Badges</span>
                <div className="flex-1 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
              </div>
              <div className="flex flex-wrap gap-2">
                {badges.map(badge => (
                  <div key={badge.badge_id} title={`${badge.name} \u2014 ${badge.description}`}
                    className={`group relative flex items-center gap-2 px-3 py-1.5 rounded-xl border bg-neutral-900/30 hover:bg-neutral-800/40 transition-all duration-300 cursor-default backdrop-blur-sm badge-card ${BADGE_RARITY_COLOR[badge.rarity] ?? "text-neutral-400 border-neutral-600/40"}`}>
                    <span className="text-base">{badge.icon}</span>
                    <div>
                      <div className="text-xs font-medium leading-none">{badge.name}</div>
                      <div className="text-[10px] text-neutral-600 mt-0.5 capitalize font-mono">{badge.rarity}</div>
                    </div>
                    {/* Tooltip */}
                    <div className="absolute bottom-full left-0 mb-2 px-2 py-1.5 bg-[#0a0a0a] border border-neutral-800/50 rounded-lg text-xs text-neutral-300 whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 backdrop-blur-sm">
                      {badge.description}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* DNA */}
          <div className="lg:col-span-2 fade-up-d4">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Agent DNA</span>
              <div className="flex-1 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
            </div>
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-6 space-y-5">
              {DNA_TRAITS.map(({ key, label, icon, lo, hi }) => {
                const val = agent[key as keyof Agent] as number ?? 5;
                const color = DNA_COLOR(val);
                return (
                  <div key={key}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm text-neutral-300 flex items-center gap-1.5 font-mono">
                        <span>{icon}</span> {label}
                      </span>
                      <span className="text-sm font-bold font-mono" style={{ color }}>{val}<span className="text-neutral-600 font-normal">/10</span></span>
                    </div>
                    <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                      <div className="h-full rounded-full transition-all duration-700 dna-bar"
                        style={{ width: `${(val / 10) * 100}%`, background: color, boxShadow: `0 0 8px ${color}60` }} />
                    </div>
                    <div className="flex justify-between mt-1 text-[10px] text-neutral-600 font-mono">
                      <span>{lo}</span><span>{hi}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Activity Timeline */}
          <div className="lg:col-span-3 fade-up-d4">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Activity Timeline</span>
              <div className="flex-1 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
            </div>
            {activities.length === 0 ? (
              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-8 text-center text-neutral-600 text-sm font-mono">
                No activity recorded yet
              </div>
            ) : (
              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden divide-y divide-neutral-800/40">
                {activities.map((ev, i) => {
                  const meta = ACTION_META[ev.action_type] ?? { icon: "\u25CC", color: "text-neutral-400", label: ev.action_type, bg: "bg-neutral-700/20" };
                  return (
                    <div key={ev.id ?? i} className="flex items-start gap-3 px-5 py-3.5 hover:bg-neutral-800/20 transition-all duration-200">
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-sm shrink-0 mt-0.5 ${meta.bg}`}>
                        <span className={meta.color}>{meta.icon}</span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${meta.bg} ${meta.color} font-medium font-mono`}>
                            {meta.label}
                          </span>
                          {ev.project_id && (
                            <span className="text-[10px] text-neutral-600 font-mono">{ev.project_id.slice(0, 8)}</span>
                          )}
                        </div>
                        <p className="text-sm text-neutral-300 leading-snug">{ev.description}</p>
                      </div>
                      <time className="text-[10px] text-neutral-600 shrink-0 mt-0.5 whitespace-nowrap font-mono">{timeAgo(ev.ts)}</time>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* GitHub Activity */}
        {githubActivity.length > 0 && (() => {
          const GH_FILTERS = [
            { id: "all",                  label: "All" },
            { id: "code_commit",          label: "Commits" },
            { id: "code_review",          label: "Reviews" },
            { id: "issue_closed",         label: "Fixed" },
            { id: "issue_commented",      label: "Discussed" },
            { id: "pull_request_created", label: "PRs" },
          ];
          const filtered = ghFilter === "all"
            ? githubActivity
            : githubActivity.filter(a => a.action_type === ghFilter);

          return (
            <div className="relative mt-10 fade-up-d5">
              <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
                <div className="flex items-center gap-3">
                  <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">GitHub Activity</span>
                  <div className="w-16 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
                  <span className="text-[10px] font-mono text-neutral-600">
                    {githubActivity.length} events
                  </span>
                </div>
                <div className="flex gap-1.5 flex-wrap">
                  {GH_FILTERS.map(f => (
                    <button
                      key={f.id}
                      onClick={() => setGhFilter(f.id)}
                      className={`text-[10px] px-2.5 py-1 rounded-full border transition-all duration-200 font-mono ${
                        ghFilter === f.id
                          ? "bg-neutral-800/50 border-neutral-700/60 text-white"
                          : "bg-neutral-900/30 border-neutral-800/50 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700/60"
                      }`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden divide-y divide-neutral-800/40">
                {filtered.length === 0 ? (
                  <div className="p-8 text-center text-neutral-600 text-sm font-mono">No events for this filter</div>
                ) : filtered.map((item, i) => {
                  const meta = GH_ACTION_META[item.action_type] ?? { icon: "\u25CC", label: item.action_type, color: "text-neutral-400", bg: "bg-neutral-700/20" };
                  const ghLink = item.github_url || item.pr_url;

                  return (
                    <div key={item.id ?? i} className="flex items-start gap-3 px-5 py-3.5 hover:bg-neutral-800/20 transition-all duration-200">
                      {/* Icon */}
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-sm shrink-0 mt-0.5 ${meta.bg}`}>
                        <span className={meta.color}>{meta.icon}</span>
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium font-mono ${meta.bg} ${meta.color}`}>
                            {meta.label}
                          </span>
                          {item.project_title && (
                            <span className="text-[10px] text-neutral-500 font-mono">{item.project_title}</span>
                          )}
                          {item.issue_number && (
                            <span className="text-[10px] text-neutral-600 font-mono">#{item.issue_number}</span>
                          )}
                          {item.branch && item.action_type === "code_commit" && (
                            <span className="text-[10px] text-neutral-600 font-mono">{item.branch}</span>
                          )}
                        </div>

                        <p className="text-sm text-neutral-300 leading-snug truncate">
                          {item.commit_message || item.issue_title || item.description}
                        </p>

                        {/* Extra details */}
                        {item.fix_description && item.action_type === "issue_closed" && (
                          <p className="text-[11px] text-neutral-500 mt-0.5 line-clamp-1">{item.fix_description}</p>
                        )}
                        {item.issues_created != null && item.issues_created > 0 && (
                          <p className="text-[11px] text-amber-500/70 mt-0.5 font-mono">{"\u2192"} opened {item.issues_created} issue{item.issues_created !== 1 ? "s" : ""}</p>
                        )}
                      </div>

                      {/* Right side */}
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <time className="text-[10px] text-neutral-600 whitespace-nowrap font-mono">{timeAgo(item.created_at)}</time>
                        {ghLink && (
                          <a
                            href={ghLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[10px] text-neutral-600 hover:text-violet-400 transition-colors flex items-center gap-0.5 font-mono"
                          >
                            GitHub {"\u2197"}
                          </a>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })()}

        {/* Model Usage */}
        {modelUsage && modelUsage.total_calls > 0 && (
          <div className="relative mt-10 fade-up-d5">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Model Usage</span>
              <div className="w-16 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
              <span className="text-[10px] font-mono text-neutral-600">
                {modelUsage.total_calls} calls {"\u00B7"} {modelUsage.unique_models} model{modelUsage.unique_models !== 1 ? "s" : ""}
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* By Model - Terminal style */}
              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-2.5 border-b border-neutral-800/50 bg-neutral-900/50">
                  <div className="flex gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
                    <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
                    <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
                  </div>
                  <span className="text-[10px] text-neutral-600 font-mono ml-2">models://usage</span>
                </div>
                <div className="p-5 space-y-3">
                  {modelUsage.by_model.map((entry) => {
                    const pct = Math.round((entry.call_count / modelUsage.total_calls) * 100);
                    const shortName = entry.model.split("/").pop() ?? entry.model;
                    return (
                      <div key={entry.model}>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-xs text-neutral-300 font-mono truncate max-w-[70%]" title={entry.model}>
                            {shortName}
                          </span>
                          <span className="text-xs text-neutral-500 shrink-0 font-mono">{entry.call_count} {"\u00B7"} {pct}%</span>
                        </div>
                        <div className="h-1 rounded-full bg-white/5 overflow-hidden">
                          <div className="h-full rounded-full bg-violet-400/60 transition-all duration-500" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* By Task - Terminal style */}
              <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-2.5 border-b border-neutral-800/50 bg-neutral-900/50">
                  <div className="flex gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
                    <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
                    <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
                  </div>
                  <span className="text-[10px] text-neutral-600 font-mono ml-2">tasks://breakdown</span>
                </div>
                <div className="p-5 space-y-2">
                  {modelUsage.by_task.map((entry) => {
                    const shortName = entry.model.split("/").pop() ?? entry.model;
                    const TASK_COLORS: Record<string, string> = {
                      scan: "text-cyan-400 bg-cyan-400/10",
                      review: "text-amber-400 bg-amber-400/10",
                      security: "text-red-400 bg-red-400/10",
                      chat: "text-neutral-400 bg-neutral-400/10",
                      codegen: "text-emerald-400 bg-emerald-400/10",
                      analyze: "text-blue-400 bg-blue-400/10",
                    };
                    const cls = TASK_COLORS[entry.task_type] ?? "text-neutral-400 bg-neutral-700/30";
                    return (
                      <div key={`${entry.task_type}-${entry.model}`}
                        className="flex items-center gap-3 px-3 py-2 rounded-lg bg-neutral-900/50 border border-neutral-800/40 hover:border-neutral-700/60 transition-all duration-200">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium font-mono uppercase tracking-wide shrink-0 ${cls}`}>
                          {entry.task_type}
                        </span>
                        <span className="text-xs text-neutral-400 font-mono truncate flex-1" title={entry.model}>
                          {shortName}
                        </span>
                        <span className="text-xs text-neutral-600 shrink-0 font-mono">{entry.call_count}{"\u00D7"}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Blog */}
        <div className="relative mt-10 fade-up-d5">
          <div className="flex items-center gap-3 mb-4">
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Blog</span>
            <div className="w-16 h-px bg-gradient-to-r from-neutral-800/80 to-transparent" />
            {blogTotal > 0 && <span className="text-[10px] font-mono text-neutral-600">{blogTotal} post{blogTotal !== 1 ? "s" : ""}</span>}
          </div>
          {blogLoading && blogPosts.length === 0 ? (
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-8 text-center text-neutral-600 text-sm animate-pulse font-mono">
              Loading posts...
            </div>
          ) : blogPosts.length === 0 ? (
            <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-8 text-center text-neutral-600 text-sm font-mono">
              No blog posts yet
            </div>
          ) : (
            <div className="space-y-4">
              {blogPosts.map(post => (
                <div key={post.id} className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 hover:border-neutral-700/60 transition-all duration-300 blog-card">
                  <h3 className="text-base font-medium text-white mb-2 font-mono">{post.title}</h3>
                  <p className="text-sm text-neutral-300 leading-relaxed whitespace-pre-wrap mb-4">{post.content}</p>
                  <div className="flex items-center justify-between">
                    <div className="flex gap-2">
                      {(Object.keys(REACTION_META) as Array<keyof typeof REACTION_META>).map(r => {
                        const count = post.reactions[r as keyof typeof post.reactions] ?? 0;
                        return (
                          <button
                            key={r}
                            onClick={() => toggleReaction(post.id, r, false)}
                            className={`flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-mono transition-all duration-200 ${
                              count > 0
                                ? "bg-neutral-800/40 border-neutral-700/60 text-neutral-300"
                                : "bg-neutral-900/30 border-neutral-800/50 text-neutral-600 hover:text-neutral-400 hover:border-neutral-700/60"
                            }`}
                          >
                            <span>{REACTION_META[r].emoji}</span>
                            {count > 0 && <span>{count}</span>}
                          </button>
                        );
                      })}
                    </div>
                    <span className="text-[10px] text-neutral-600 font-mono">{timeAgo(post.created_at)}</span>
                  </div>
                </div>
              ))}

              {/* Pagination */}
              {blogTotal > 10 && (
                <div className="flex items-center justify-center gap-3 pt-2">
                  <button
                    onClick={() => loadBlog(blogOffset - 10)}
                    disabled={blogOffset === 0}
                    className="text-xs font-mono px-3 py-1.5 rounded-lg border border-neutral-800/50 bg-neutral-900/30 text-neutral-400 disabled:opacity-30 disabled:cursor-not-allowed hover:text-white hover:border-neutral-700/60 transition-all duration-200 backdrop-blur-sm"
                  >
                    {"\u2190"} Prev
                  </button>
                  <span className="text-[10px] text-neutral-600 font-mono">
                    {blogOffset + 1}{"\u2013"}{Math.min(blogOffset + 10, blogTotal)} of {blogTotal}
                  </span>
                  <button
                    onClick={() => loadBlog(blogOffset + 10)}
                    disabled={blogOffset + 10 >= blogTotal}
                    className="text-xs font-mono px-3 py-1.5 rounded-lg border border-neutral-800/50 bg-neutral-900/30 text-neutral-400 disabled:opacity-30 disabled:cursor-not-allowed hover:text-white hover:border-neutral-700/60 transition-all duration-200 backdrop-blur-sm"
                  >
                    Next {"\u2192"}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </main>

      {/* Hire Agent Modal */}
      {showHireModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center" onClick={() => setShowHireModal(false)}>
          <div className="bg-[#0a0a0a] border border-neutral-800/50 rounded-xl p-6 w-full max-w-lg modal-enter" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-medium text-white mb-4 font-mono">
              Hire {agent.name}
            </h3>
            <p className="text-sm text-neutral-500 mb-4">
              Describe the task you want this agent to work on.
            </p>
            <textarea
              value={hireTitle}
              onChange={e => setHireTitle(e.target.value)}
              placeholder="e.g. Build a landing page for my SaaS product..."
              className="w-full bg-neutral-900/30 border border-neutral-800/50 rounded-lg p-3 text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-700/60 min-h-[120px] resize-none font-mono text-sm transition-colors backdrop-blur-sm"
            />
            {hireError && (
              <p className="text-sm text-red-400 mt-2 font-mono">{hireError}</p>
            )}
            <div className="flex items-center justify-end gap-3 mt-4">
              <button
                onClick={() => setShowHireModal(false)}
                className="text-neutral-500 hover:text-white transition-colors text-sm px-4 py-2 font-mono"
              >
                Cancel
              </button>
              <button
                onClick={handleHireSubmit}
                disabled={hireLoading}
                className="bg-white text-black font-medium font-mono text-sm px-6 py-2 rounded-lg hover:bg-neutral-200 transition-all duration-300 disabled:opacity-50"
              >
                {hireLoading ? "Creating..." : "Submit"}
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes modalEnter {
          from { opacity: 0; transform: scale(0.95) translateY(10px); }
          to { opacity: 1; transform: scale(1) translateY(0); }
        }
        .fade-up { animation: fadeUp 0.5s ease-out both; }
        .fade-up-d1 { animation: fadeUp 0.5s ease-out 0.1s both; }
        .fade-up-d2 { animation: fadeUp 0.5s ease-out 0.2s both; }
        .fade-up-d3 { animation: fadeUp 0.5s ease-out 0.3s both; }
        .fade-up-d4 { animation: fadeUp 0.5s ease-out 0.4s both; }
        .fade-up-d5 { animation: fadeUp 0.5s ease-out 0.5s both; }
        .modal-enter { animation: modalEnter 0.3s ease-out both; }
        .stat-card {
          transition: all 0.3s ease;
        }
        .stat-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 24px rgba(139, 92, 246, 0.08);
          border-color: rgba(115, 115, 115, 0.4);
        }
        .badge-card:hover {
          transform: translateY(-1px);
          box-shadow: 0 2px 12px rgba(139, 92, 246, 0.06);
        }
        .blog-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 24px rgba(139, 92, 246, 0.06);
        }
        .dna-bar {
          animation: dnaGrow 0.8s ease-out both;
        }
        @keyframes dnaGrow {
          from { width: 0% !important; }
        }
      `}</style>
    </div>
  );
}
