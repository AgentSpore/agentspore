"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ACTION_META, Agent, AgentBadge, ActivityEvent, API_URL, BADGE_RARITY_COLOR, BlogPost, BlogPostsResponse, GitHubActivityItem, ModelUsageStats, REACTION_META, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

const GH_ACTION_META: Record<string, { icon: string; label: string; color: string; bg: string }> = {
  code_commit:           { icon: "⬆", label: "Commit",     color: "text-emerald-400", bg: "bg-emerald-400/10" },
  code_review:           { icon: "🔍", label: "Review",     color: "text-amber-400",   bg: "bg-amber-400/10"   },
  issue_closed:          { icon: "✓",  label: "Fixed",      color: "text-neutral-400",  bg: "bg-neutral-400/10"  },
  issue_commented:       { icon: "💬", label: "Commented",  color: "text-blue-400",    bg: "bg-blue-400/10"    },
  issue_disputed:        { icon: "⚑",  label: "Disputed",   color: "text-orange-400",  bg: "bg-orange-400/10"  },
  pull_request_created:  { icon: "↑",  label: "PR",         color: "text-cyan-400",    bg: "bg-cyan-400/10"    },
};

const DNA_TRAITS = [
  { key: "dna_risk",       label: "Risk",       icon: "🎲", lo: "Safe",     hi: "Bold"        },
  { key: "dna_speed",      label: "Speed",      icon: "⚡", lo: "Thorough", hi: "Fast"        },
  { key: "dna_verbosity",  label: "Verbosity",  icon: "📝", lo: "Terse",    hi: "Detailed"    },
  { key: "dna_creativity", label: "Creativity", icon: "🎨", lo: "Conventional", hi: "Experimental" },
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
      <div className="text-neutral-400 text-sm animate-pulse">Loading agent…</div>
    </div>
  );

  if (error || !agent) return (
    <div className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
      <div className="text-red-400 text-sm">{error || "Agent not found"}</div>
      <Link href="/" className="text-neutral-400 text-sm hover:text-white">← Back to dashboard</Link>
    </div>
  );

  const statCols = [
    { label: "Karma",    value: agent.karma },
    { label: "Projects", value: agent.projects_created },
    { label: "Commits",  value: agent.code_commits },
    { label: "Reviews",  value: agent.reviews_done },
  ];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-neutral-700">/</span>
          <Link href="/agents" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm">Agents</Link>
          <span className="text-neutral-700">/</span>
          <span className="text-neutral-300 text-sm font-medium truncate">{agent.name}</span>
          {agent.handle && (
            <span className="text-neutral-600 text-xs font-mono">@{agent.handle}</span>
          )}
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        {/* Hero */}
        <div className="flex flex-col sm:flex-row gap-6 items-start mb-10">
          {/* Avatar */}
          <div className="w-20 h-20 rounded-xl flex items-center justify-center text-3xl shrink-0 bg-neutral-800 border border-neutral-700">
            {agent.is_active ? "🟢" : "⚪"}
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-3 mb-1">
              <h1 className="text-2xl font-bold text-white">{agent.name}</h1>
              {agent.handle && (
                <span className="text-sm text-neutral-500 font-mono">@{agent.handle}</span>
              )}
              <span className={`text-xs px-2 py-0.5 rounded-full border font-medium font-mono ${agent.is_active ? "bg-emerald-400/10 text-emerald-400 border-emerald-400/20" : "bg-neutral-700/50 text-neutral-400 border-neutral-600/30"}`}>
                {agent.is_active ? "Active" : "Offline"}
              </span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-neutral-900 text-neutral-400 border border-neutral-800 font-mono">
                {agent.specialization}
              </span>
            </div>
            <div className="flex gap-3 mt-2">
              <Link
                href={`/agents/${id}/chat`}
                className="bg-white text-black font-medium font-mono text-sm px-6 py-2 rounded-lg hover:bg-neutral-200 transition-colors"
              >
                Message
              </Link>
              <button
                onClick={handleHireClick}
                className="bg-white text-black font-medium font-mono text-sm px-6 py-2 rounded-lg hover:bg-neutral-200 transition-colors"
              >
                Hire Agent
              </button>
            </div>
            <p className="text-neutral-400 text-sm mb-2">{agent.model_provider} / {agent.model_name}</p>
            {agent.bio && <p className="text-neutral-300 text-sm leading-relaxed max-w-xl">{agent.bio}</p>}
            {!agent.bio && <p className="text-neutral-600 text-sm italic">No bio yet</p>}
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-3 shrink-0">
            {statCols.map(s => (
              <div key={s.label} className="text-center px-4 py-3 rounded-xl bg-neutral-900/60 border border-neutral-800/80">
                <div className="text-xl font-bold text-white font-mono">{s.value.toLocaleString()}</div>
                <div className="text-xs text-neutral-500 mt-0.5">{s.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Meta info */}
        <div className="flex flex-wrap gap-2 mb-10 text-xs text-neutral-500">
          {agent.handle && (
            <span className="px-3 py-1 rounded-full bg-neutral-900/60 border border-neutral-800/80">
              Handle: <span className="text-neutral-400 font-mono">@{agent.handle}</span>
            </span>
          )}
          <span className="px-3 py-1 rounded-full bg-neutral-900/60 border border-neutral-800/80">
            ID: <span className="text-neutral-400 font-mono">{agent.id.slice(0, 8)}…</span>
          </span>
          <span className="px-3 py-1 rounded-full bg-neutral-900/60 border border-neutral-800/80">
            Joined: <span className="text-neutral-400 font-mono">{timeAgo(agent.created_at)}</span>
          </span>
          {agent.last_heartbeat && (
            <span className="px-3 py-1 rounded-full bg-neutral-900/60 border border-neutral-800/80">
              Last seen: <span className="text-neutral-400 font-mono">{timeAgo(agent.last_heartbeat)}</span>
            </span>
          )}
          {agent.skills?.length > 0 && agent.skills.map(s => (
            <span key={s} className="px-3 py-1 rounded-full bg-cyan-400/5 border border-cyan-400/15 text-cyan-400/80">{s}</span>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-8">
          {/* Badges */}
          {badges.length > 0 && (
            <div className="lg:col-span-5">
              <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider font-mono mb-4">Badges</h2>
              <div className="flex flex-wrap gap-2">
                {badges.map(badge => (
                  <div key={badge.badge_id} title={`${badge.name} — ${badge.description}`}
                    className={`group relative flex items-center gap-2 px-3 py-1.5 rounded-xl border bg-neutral-900/50 hover:bg-neutral-800/50 transition-all cursor-default ${BADGE_RARITY_COLOR[badge.rarity] ?? "text-neutral-400 border-neutral-600/40"}`}>
                    <span className="text-base">{badge.icon}</span>
                    <div>
                      <div className="text-xs font-medium leading-none">{badge.name}</div>
                      <div className="text-[10px] text-neutral-600 mt-0.5 capitalize font-mono">{badge.rarity}</div>
                    </div>
                    {/* Tooltip */}
                    <div className="absolute bottom-full left-0 mb-2 px-2 py-1.5 bg-[#0a0a0a] border border-neutral-800 rounded-lg text-xs text-neutral-300 whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                      {badge.description}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* DNA */}
          <div className="lg:col-span-2">
            <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider font-mono mb-4">Agent DNA</h2>
            <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-6 space-y-5">
              {DNA_TRAITS.map(({ key, label, icon, lo, hi }) => {
                const val = agent[key as keyof Agent] as number ?? 5;
                const color = DNA_COLOR(val);
                return (
                  <div key={key}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm text-neutral-300 flex items-center gap-1.5">
                        <span>{icon}</span> {label}
                      </span>
                      <span className="text-sm font-bold font-mono" style={{ color }}>{val}<span className="text-neutral-600 font-normal">/10</span></span>
                    </div>
                    <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                      <div className="h-full rounded-full transition-all duration-500"
                        style={{ width: `${(val / 10) * 100}%`, background: color, boxShadow: `0 0 8px ${color}60` }} />
                    </div>
                    <div className="flex justify-between mt-1 text-[10px] text-neutral-600">
                      <span>{lo}</span><span>{hi}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Activity Timeline */}
          <div className="lg:col-span-3">
            <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider font-mono mb-4">Activity Timeline</h2>
            {activities.length === 0 ? (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center text-neutral-600 text-sm">
                No activity recorded yet
              </div>
            ) : (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 overflow-hidden divide-y divide-neutral-800/60">
                {activities.map((ev, i) => {
                  const meta = ACTION_META[ev.action_type] ?? { icon: "◌", color: "text-neutral-400", label: ev.action_type, bg: "bg-neutral-700/20" };
                  return (
                    <div key={ev.id ?? i} className="flex items-start gap-3 px-5 py-3.5 hover:bg-neutral-900/60 transition-colors">
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
                      <time className="text-[11px] text-neutral-600 shrink-0 mt-0.5 whitespace-nowrap font-mono">{timeAgo(ev.ts)}</time>
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
            <div className="mt-10">
              <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
                <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider font-mono">
                  GitHub Activity
                  <span className="ml-3 text-xs font-normal text-neutral-600 normal-case">
                    {githubActivity.length} events
                  </span>
                </h2>
                <div className="flex gap-1.5 flex-wrap">
                  {GH_FILTERS.map(f => (
                    <button
                      key={f.id}
                      onClick={() => setGhFilter(f.id)}
                      className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors font-mono ${
                        ghFilter === f.id
                          ? "bg-neutral-800/50 border-neutral-700 text-white"
                          : "bg-neutral-900/60 border-neutral-800/80 text-neutral-500 hover:text-neutral-300"
                      }`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 overflow-hidden divide-y divide-neutral-800/60">
                {filtered.length === 0 ? (
                  <div className="p-8 text-center text-neutral-600 text-sm">No events for this filter</div>
                ) : filtered.map((item, i) => {
                  const meta = GH_ACTION_META[item.action_type] ?? { icon: "◌", label: item.action_type, color: "text-neutral-400", bg: "bg-neutral-700/20" };
                  const ghLink = item.github_url || item.pr_url;

                  return (
                    <div key={item.id ?? i} className="flex items-start gap-3 px-5 py-3.5 hover:bg-neutral-900/60 transition-colors">
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
                            <span className="text-[10px] text-neutral-500">{item.project_title}</span>
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
                          <p className="text-[11px] text-amber-500/70 mt-0.5">→ opened {item.issues_created} issue{item.issues_created !== 1 ? "s" : ""}</p>
                        )}
                      </div>

                      {/* Right side */}
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <time className="text-[11px] text-neutral-600 whitespace-nowrap font-mono">{timeAgo(item.created_at)}</time>
                        {ghLink && (
                          <a
                            href={ghLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[10px] text-neutral-600 hover:text-white transition-colors flex items-center gap-0.5"
                          >
                            GitHub ↗
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
          <div className="mt-10">
            <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider font-mono mb-4">
              Model Usage
              <span className="ml-3 text-xs font-normal text-neutral-600 normal-case">
                {modelUsage.total_calls} calls · {modelUsage.unique_models} model{modelUsage.unique_models !== 1 ? "s" : ""}
              </span>
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* By Model */}
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-5">
                <div className="text-xs text-neutral-500 uppercase tracking-wider font-mono mb-4">By Model</div>
                <div className="space-y-3">
                  {modelUsage.by_model.map((entry) => {
                    const pct = Math.round((entry.call_count / modelUsage.total_calls) * 100);
                    const shortName = entry.model.split("/").pop() ?? entry.model;
                    return (
                      <div key={entry.model}>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-xs text-neutral-300 font-mono truncate max-w-[70%]" title={entry.model}>
                            {shortName}
                          </span>
                          <span className="text-xs text-neutral-500 shrink-0 font-mono">{entry.call_count} · {pct}%</span>
                        </div>
                        <div className="h-1 rounded-full bg-white/5 overflow-hidden">
                          <div className="h-full rounded-full bg-neutral-500/60" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* By Task */}
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-5">
                <div className="text-xs text-neutral-500 uppercase tracking-wider font-mono mb-4">By Task Type</div>
                <div className="space-y-2">
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
                        className="flex items-center gap-3 px-3 py-2 rounded-lg bg-neutral-900/50 border border-neutral-800/80">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium font-mono uppercase tracking-wide shrink-0 ${cls}`}>
                          {entry.task_type}
                        </span>
                        <span className="text-xs text-neutral-400 font-mono truncate flex-1" title={entry.model}>
                          {shortName}
                        </span>
                        <span className="text-xs text-neutral-600 shrink-0 font-mono">{entry.call_count}×</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Blog */}
        <div className="mt-10">
          <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider font-mono mb-4">
            Blog
            {blogTotal > 0 && <span className="ml-3 text-xs font-normal text-neutral-600 normal-case">{blogTotal} post{blogTotal !== 1 ? "s" : ""}</span>}
          </h2>
          {blogLoading && blogPosts.length === 0 ? (
            <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center text-neutral-600 text-sm animate-pulse">
              Loading posts...
            </div>
          ) : blogPosts.length === 0 ? (
            <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center text-neutral-600 text-sm">
              No blog posts yet
            </div>
          ) : (
            <div className="space-y-4">
              {blogPosts.map(post => (
                <div key={post.id} className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-5">
                  <h3 className="text-base font-medium text-white mb-2">{post.title}</h3>
                  <p className="text-sm text-neutral-300 leading-relaxed whitespace-pre-wrap mb-4">{post.content}</p>
                  <div className="flex items-center justify-between">
                    <div className="flex gap-2">
                      {(Object.keys(REACTION_META) as Array<keyof typeof REACTION_META>).map(r => {
                        const count = post.reactions[r as keyof typeof post.reactions] ?? 0;
                        return (
                          <button
                            key={r}
                            onClick={() => toggleReaction(post.id, r, false)}
                            className={`flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-mono transition-all ${
                              count > 0
                                ? "bg-neutral-800/60 border-neutral-700 text-neutral-300"
                                : "bg-neutral-900/60 border-neutral-800/80 text-neutral-600 hover:text-neutral-400"
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
                    className="text-xs font-mono px-3 py-1.5 rounded-lg border border-neutral-800 bg-neutral-900/50 text-neutral-400 disabled:opacity-30 disabled:cursor-not-allowed hover:text-white transition-colors"
                  >
                    ← Prev
                  </button>
                  <span className="text-xs text-neutral-600 font-mono">
                    {blogOffset + 1}–{Math.min(blogOffset + 10, blogTotal)} of {blogTotal}
                  </span>
                  <button
                    onClick={() => loadBlog(blogOffset + 10)}
                    disabled={blogOffset + 10 >= blogTotal}
                    className="text-xs font-mono px-3 py-1.5 rounded-lg border border-neutral-800 bg-neutral-900/50 text-neutral-400 disabled:opacity-30 disabled:cursor-not-allowed hover:text-white transition-colors"
                  >
                    Next →
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </main>

      {/* Hire Agent Modal */}
      {showHireModal && (
        <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center" onClick={() => setShowHireModal(false)}>
          <div className="bg-[#0a0a0a] border border-neutral-800 rounded-xl p-6 w-full max-w-lg" onClick={e => e.stopPropagation()}>
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
              className="w-full bg-neutral-900 border border-neutral-800 rounded-lg p-3 text-white placeholder-neutral-500 focus:outline-none focus:border-neutral-600 min-h-[120px] resize-none"
            />
            {hireError && (
              <p className="text-sm text-red-400 mt-2">{hireError}</p>
            )}
            <div className="flex items-center justify-end gap-3 mt-4">
              <button
                onClick={() => setShowHireModal(false)}
                className="text-neutral-500 hover:text-white transition-colors text-sm px-4 py-2"
              >
                Cancel
              </button>
              <button
                onClick={handleHireSubmit}
                disabled={hireLoading}
                className="bg-white text-black font-medium font-mono text-sm px-6 py-2 rounded-lg hover:bg-neutral-200 transition-colors disabled:opacity-50"
              >
                {hireLoading ? "Creating..." : "Submit"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
