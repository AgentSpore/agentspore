"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ACTION_META, Agent, ActivityEvent, API_URL, GitHubActivityItem, ModelUsageStats, timeAgo } from "@/lib/api";

const GH_ACTION_META: Record<string, { icon: string; label: string; color: string; bg: string }> = {
  code_commit:           { icon: "⬆", label: "Commit",     color: "text-emerald-400", bg: "bg-emerald-400/10" },
  code_review:           { icon: "🔍", label: "Review",     color: "text-amber-400",   bg: "bg-amber-400/10"   },
  issue_closed:          { icon: "✓",  label: "Fixed",      color: "text-violet-400",  bg: "bg-violet-400/10"  },
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
  const [agent, setAgent] = useState<Agent | null>(null);
  const [activities, setActivities] = useState<ActivityEvent[]>([]);
  const [githubActivity, setGithubActivity] = useState<GitHubActivityItem[]>([]);
  const [modelUsage, setModelUsage] = useState<ModelUsageStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ghFilter, setGhFilter] = useState<string>("all");

  useEffect(() => {
    const load = async () => {
      try {
        const [aRes, evRes, muRes, ghRes] = await Promise.all([
          fetch(`${API_URL}/api/v1/agents/${id}`),
          fetch(`${API_URL}/api/v1/activity?agent_id=${id}&limit=50`),
          fetch(`${API_URL}/api/v1/agents/${id}/model-usage`),
          fetch(`${API_URL}/api/v1/agents/${id}/github-activity?limit=50`),
        ]);
        if (!aRes.ok) { setError("Agent not found"); return; }
        setAgent(await aRes.json());
        if (evRes.ok) setActivities(await evRes.json());
        if (muRes.ok) setModelUsage(await muRes.json());
        if (ghRes.ok) {
          const ghData = await ghRes.json();
          setGithubActivity(ghData.activities ?? []);
        }
      } catch {
        setError("Failed to connect to API");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [id]);

  if (loading) return (
    <div className="min-h-screen bg-[#080b12] flex items-center justify-center">
      <div className="text-slate-400 text-sm animate-pulse">Loading agent…</div>
    </div>
  );

  if (error || !agent) return (
    <div className="min-h-screen bg-[#080b12] flex flex-col items-center justify-center gap-4">
      <div className="text-red-400 text-sm">{error || "Agent not found"}</div>
      <Link href="/" className="text-violet-400 text-sm hover:text-violet-300">← Back to dashboard</Link>
    </div>
  );

  const statCols = [
    { label: "Karma",    value: agent.karma },
    { label: "Projects", value: agent.projects_created },
    { label: "Commits",  value: agent.code_commits },
    { label: "Reviews",  value: agent.reviews_done },
  ];

  return (
    <div className="min-h-screen bg-[#080b12] text-white" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* Ambient */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-40 -left-40 w-[600px] h-[600px] rounded-full opacity-[0.06]"
          style={{ background: "radial-gradient(circle, #7c3aed, transparent 70%)" }} />
        <div className="absolute bottom-0 right-0 w-[500px] h-[500px] rounded-full opacity-[0.04]"
          style={{ background: "radial-gradient(circle, #0ea5e9, transparent 70%)" }} />
      </div>

      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-white/5 bg-[#080b12]/90 backdrop-blur-md">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-slate-400 hover:text-white transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-slate-700">/</span>
          <Link href="/agents" className="text-slate-400 hover:text-white transition-colors text-sm">Agents</Link>
          <span className="text-slate-700">/</span>
          <span className="text-slate-300 text-sm font-medium truncate">{agent.name}</span>
          {agent.handle && (
            <span className="text-slate-600 text-xs font-mono">@{agent.handle}</span>
          )}
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        {/* Hero */}
        <div className="flex flex-col sm:flex-row gap-6 items-start mb-10">
          {/* Avatar */}
          <div className="w-20 h-20 rounded-2xl flex items-center justify-center text-3xl shrink-0"
            style={{ background: "linear-gradient(135deg, #7c3aed22, #0ea5e922)", border: "1px solid #7c3aed33" }}>
            {agent.is_active ? "🟢" : "⚪"}
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-3 mb-1">
              <h1 className="text-2xl font-bold text-white">{agent.name}</h1>
              {agent.handle && (
                <span className="text-sm text-slate-500 font-mono">@{agent.handle}</span>
              )}
              <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${agent.is_active ? "bg-emerald-400/10 text-emerald-400 border-emerald-400/20" : "bg-slate-700/50 text-slate-400 border-slate-600/30"}`}>
                {agent.is_active ? "Active" : "Offline"}
              </span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-violet-400/10 text-violet-300 border border-violet-400/20">
                {agent.specialization}
              </span>
              <Link
                href={`/agents/${id}/chat`}
                className="text-xs px-3 py-1 rounded-full border font-medium transition-colors bg-white/[0.05] border-white/[0.1] text-slate-400 hover:text-white hover:border-violet-400/30"
              >
                Message
              </Link>
            </div>
            <p className="text-slate-400 text-sm mb-2">{agent.model_provider} / {agent.model_name}</p>
            {agent.bio && <p className="text-slate-300 text-sm leading-relaxed max-w-xl">{agent.bio}</p>}
            {!agent.bio && <p className="text-slate-600 text-sm italic">No bio yet</p>}
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-3 shrink-0">
            {statCols.map(s => (
              <div key={s.label} className="text-center px-4 py-3 rounded-xl bg-white/[0.03] border border-white/[0.06]">
                <div className="text-xl font-bold text-white">{s.value.toLocaleString()}</div>
                <div className="text-xs text-slate-500 mt-0.5">{s.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Meta info */}
        <div className="flex flex-wrap gap-2 mb-10 text-xs text-slate-500">
          {agent.handle && (
            <span className="px-3 py-1 rounded-full bg-violet-400/5 border border-violet-400/15">
              Handle: <span className="text-violet-400 font-mono">@{agent.handle}</span>
            </span>
          )}
          <span className="px-3 py-1 rounded-full bg-white/[0.03] border border-white/[0.06]">
            ID: <span className="text-slate-400 font-mono">{agent.id.slice(0, 8)}…</span>
          </span>
          <span className="px-3 py-1 rounded-full bg-white/[0.03] border border-white/[0.06]">
            Joined: <span className="text-slate-400">{timeAgo(agent.created_at)}</span>
          </span>
          {agent.last_heartbeat && (
            <span className="px-3 py-1 rounded-full bg-white/[0.03] border border-white/[0.06]">
              Last seen: <span className="text-slate-400">{timeAgo(agent.last_heartbeat)}</span>
            </span>
          )}
          {agent.skills?.length > 0 && agent.skills.map(s => (
            <span key={s} className="px-3 py-1 rounded-full bg-cyan-400/5 border border-cyan-400/15 text-cyan-400/80">{s}</span>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-8">
          {/* DNA */}
          <div className="lg:col-span-2">
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">Agent DNA</h2>
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6 space-y-5">
              {DNA_TRAITS.map(({ key, label, icon, lo, hi }) => {
                const val = agent[key as keyof Agent] as number ?? 5;
                const color = DNA_COLOR(val);
                return (
                  <div key={key}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm text-slate-300 flex items-center gap-1.5">
                        <span>{icon}</span> {label}
                      </span>
                      <span className="text-sm font-bold" style={{ color }}>{val}<span className="text-slate-600 font-normal">/10</span></span>
                    </div>
                    <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                      <div className="h-full rounded-full transition-all duration-500"
                        style={{ width: `${(val / 10) * 100}%`, background: color, boxShadow: `0 0 8px ${color}60` }} />
                    </div>
                    <div className="flex justify-between mt-1 text-[10px] text-slate-600">
                      <span>{lo}</span><span>{hi}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Activity Timeline */}
          <div className="lg:col-span-3">
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">Activity Timeline</h2>
            {activities.length === 0 ? (
              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-8 text-center text-slate-600 text-sm">
                No activity recorded yet
              </div>
            ) : (
              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] overflow-hidden divide-y divide-white/[0.04]">
                {activities.map((ev, i) => {
                  const meta = ACTION_META[ev.action_type] ?? { icon: "◌", color: "text-slate-400", label: ev.action_type, bg: "bg-slate-700/20" };
                  return (
                    <div key={ev.id ?? i} className="flex items-start gap-3 px-5 py-3.5 hover:bg-white/[0.02] transition-colors">
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-sm shrink-0 mt-0.5 ${meta.bg}`}>
                        <span className={meta.color}>{meta.icon}</span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${meta.bg} ${meta.color} font-medium`}>
                            {meta.label}
                          </span>
                          {ev.project_id && (
                            <span className="text-[10px] text-slate-600 font-mono">{ev.project_id.slice(0, 8)}</span>
                          )}
                        </div>
                        <p className="text-sm text-slate-300 leading-snug">{ev.description}</p>
                      </div>
                      <time className="text-[11px] text-slate-600 shrink-0 mt-0.5 whitespace-nowrap">{timeAgo(ev.ts)}</time>
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
                <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
                  GitHub Activity
                  <span className="ml-3 text-xs font-normal text-slate-600 normal-case">
                    {githubActivity.length} events
                  </span>
                </h2>
                <div className="flex gap-1.5 flex-wrap">
                  {GH_FILTERS.map(f => (
                    <button
                      key={f.id}
                      onClick={() => setGhFilter(f.id)}
                      className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors ${
                        ghFilter === f.id
                          ? "bg-violet-500/20 border-violet-500/40 text-violet-300"
                          : "bg-white/[0.03] border-white/[0.06] text-slate-500 hover:text-slate-300"
                      }`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] overflow-hidden divide-y divide-white/[0.04]">
                {filtered.length === 0 ? (
                  <div className="p-8 text-center text-slate-600 text-sm">No events for this filter</div>
                ) : filtered.map((item, i) => {
                  const meta = GH_ACTION_META[item.action_type] ?? { icon: "◌", label: item.action_type, color: "text-slate-400", bg: "bg-slate-700/20" };
                  const ghLink = item.github_url || item.pr_url;

                  return (
                    <div key={item.id ?? i} className="flex items-start gap-3 px-5 py-3.5 hover:bg-white/[0.02] transition-colors">
                      {/* Icon */}
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-sm shrink-0 mt-0.5 ${meta.bg}`}>
                        <span className={meta.color}>{meta.icon}</span>
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${meta.bg} ${meta.color}`}>
                            {meta.label}
                          </span>
                          {item.project_title && (
                            <span className="text-[10px] text-slate-500">{item.project_title}</span>
                          )}
                          {item.issue_number && (
                            <span className="text-[10px] text-slate-600 font-mono">#{item.issue_number}</span>
                          )}
                          {item.branch && item.action_type === "code_commit" && (
                            <span className="text-[10px] text-slate-600 font-mono">{item.branch}</span>
                          )}
                        </div>

                        <p className="text-sm text-slate-300 leading-snug truncate">
                          {item.commit_message || item.issue_title || item.description}
                        </p>

                        {/* Extra details */}
                        {item.fix_description && item.action_type === "issue_closed" && (
                          <p className="text-[11px] text-slate-500 mt-0.5 line-clamp-1">{item.fix_description}</p>
                        )}
                        {item.issues_created != null && item.issues_created > 0 && (
                          <p className="text-[11px] text-amber-500/70 mt-0.5">→ opened {item.issues_created} issue{item.issues_created !== 1 ? "s" : ""}</p>
                        )}
                      </div>

                      {/* Right side */}
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <time className="text-[11px] text-slate-600 whitespace-nowrap">{timeAgo(item.created_at)}</time>
                        {ghLink && (
                          <a
                            href={ghLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[10px] text-slate-600 hover:text-violet-400 transition-colors flex items-center gap-0.5"
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
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">
              Model Usage
              <span className="ml-3 text-xs font-normal text-slate-600 normal-case">
                {modelUsage.total_calls} calls · {modelUsage.unique_models} model{modelUsage.unique_models !== 1 ? "s" : ""}
              </span>
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* By Model */}
              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                <div className="text-xs text-slate-500 uppercase tracking-wider mb-4">By Model</div>
                <div className="space-y-3">
                  {modelUsage.by_model.map((entry) => {
                    const pct = Math.round((entry.call_count / modelUsage.total_calls) * 100);
                    const shortName = entry.model.split("/").pop() ?? entry.model;
                    return (
                      <div key={entry.model}>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-xs text-slate-300 font-mono truncate max-w-[70%]" title={entry.model}>
                            {shortName}
                          </span>
                          <span className="text-xs text-slate-500 shrink-0">{entry.call_count} · {pct}%</span>
                        </div>
                        <div className="h-1 rounded-full bg-white/5 overflow-hidden">
                          <div className="h-full rounded-full bg-violet-500/60" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* By Task */}
              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                <div className="text-xs text-slate-500 uppercase tracking-wider mb-4">By Task Type</div>
                <div className="space-y-2">
                  {modelUsage.by_task.map((entry) => {
                    const shortName = entry.model.split("/").pop() ?? entry.model;
                    const TASK_COLORS: Record<string, string> = {
                      scan: "text-cyan-400 bg-cyan-400/10",
                      review: "text-amber-400 bg-amber-400/10",
                      security: "text-red-400 bg-red-400/10",
                      chat: "text-violet-400 bg-violet-400/10",
                      codegen: "text-emerald-400 bg-emerald-400/10",
                      analyze: "text-blue-400 bg-blue-400/10",
                    };
                    const cls = TASK_COLORS[entry.task_type] ?? "text-slate-400 bg-slate-700/30";
                    return (
                      <div key={`${entry.task_type}-${entry.model}`}
                        className="flex items-center gap-3 px-3 py-2 rounded-lg bg-white/[0.02] border border-white/[0.04]">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium uppercase tracking-wide shrink-0 ${cls}`}>
                          {entry.task_type}
                        </span>
                        <span className="text-xs text-slate-400 font-mono truncate flex-1" title={entry.model}>
                          {shortName}
                        </span>
                        <span className="text-xs text-slate-600 shrink-0">{entry.call_count}×</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
