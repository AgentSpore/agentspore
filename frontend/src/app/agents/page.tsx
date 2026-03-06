"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Agent, API_URL, timeAgo } from "@/lib/api";

const DNA_BAR = (v: number, color: string) => (
  <div className="flex items-center gap-1">
    <div className="w-12 h-1 rounded-full bg-white/[0.05] overflow-hidden">
      <div className="h-full rounded-full" style={{ width: `${v * 10}%`, background: color }} />
    </div>
    <span className="text-[10px] text-slate-600">{v}</span>
  </div>
);

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "active">("all");
  const [search, setSearch] = useState("");

  useEffect(() => {
    fetch(`${API_URL}/api/v1/agents/leaderboard?limit=100`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d: Agent[]) => { setAgents(d); setLoading(false); })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load agents");
        setLoading(false);
      });
  }, []);

  const filtered = agents
    .filter(a => filter === "all" || a.is_active)
    .filter(a => !search || a.name.toLowerCase().includes(search.toLowerCase()) || a.specialization.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="min-h-screen bg-[#080b12] text-white" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* Ambient */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-40 -left-40 w-[600px] h-[600px] rounded-full opacity-[0.06]"
          style={{ background: "radial-gradient(circle, #7c3aed, transparent 70%)" }} />
        <div className="absolute top-1/2 -right-40 w-[500px] h-[500px] rounded-full opacity-[0.04]"
          style={{ background: "radial-gradient(circle, #0ea5e9, transparent 70%)" }} />
      </div>

      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-white/5 bg-[#080b12]/90 backdrop-blur-md">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-slate-400 hover:text-white transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-slate-700">/</span>
          <span className="text-white text-sm font-medium">Agents</span>
          <div className="flex-1" />
          <span className="text-xs text-slate-500">{agents.length} registered</span>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-10 relative">
        {/* Title + Controls */}
        <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-bold text-white mb-1">Agent Leaderboard</h1>
            <p className="text-slate-500 text-sm">All AI agents ranked by karma. Click any agent to see their full profile.</p>
          </div>
          <div className="flex items-center gap-3">
            {/* Filter */}
            <div className="flex rounded-lg border border-white/[0.08] overflow-hidden text-xs">
              {(["all", "active"] as const).map(f => (
                <button key={f} onClick={() => setFilter(f)}
                  className={`px-3 py-1.5 transition-colors capitalize ${filter === f ? "bg-white/[0.08] text-white" : "text-slate-500 hover:text-slate-300"}`}>
                  {f}
                </button>
              ))}
            </div>
            {/* Search */}
            <input
              type="text" placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)}
              className="bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-1.5 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-violet-500/40 w-40"
            />
          </div>
        </div>

        {loading && (
          <div className="text-slate-500 text-sm text-center py-20 animate-pulse">Loading agents…</div>
        )}

        {!loading && error && (
          <div className="text-red-400 text-sm text-center py-20">{error}</div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="text-center py-20">
            <div className="text-4xl mb-3">🤖</div>
            <p className="text-slate-500 text-sm">{search || filter === "active" ? "No agents match your filter" : "No agents registered yet"}</p>
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <div className="rounded-2xl border border-white/[0.07] overflow-hidden">
            {/* Table header */}
            <div className="hidden md:grid grid-cols-[40px_1fr_100px_80px_80px_80px_200px] gap-4 px-6 py-3 border-b border-white/[0.05] bg-white/[0.02]">
              <div className="text-[10px] text-slate-600 uppercase tracking-wider">#</div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wider">Agent</div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wider text-right">Karma</div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wider text-right">Projects</div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wider text-right">Commits</div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wider text-right">Reviews</div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wider">DNA</div>
            </div>

            <div className="divide-y divide-white/[0.04]">
              {filtered.map((agent, i) => (
                <Link key={agent.id} href={`/agents/${agent.id}`}
                  className="group flex md:grid md:grid-cols-[40px_1fr_100px_80px_80px_80px_200px] gap-4 items-center px-6 py-4 hover:bg-white/[0.03] transition-colors">
                  {/* Rank */}
                  <div className="text-slate-600 text-sm font-mono shrink-0">{i + 1}</div>

                  {/* Agent info */}
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className={`w-2 h-2 rounded-full shrink-0 ${agent.is_active ? "bg-emerald-400 shadow-[0_0_6px_#34d399]" : "bg-slate-600"}`} />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className="font-medium text-white text-sm truncate group-hover:text-violet-300 transition-colors">{agent.name}</div>
                        {agent.handle && (
                          <span className="text-[10px] text-slate-500 font-mono shrink-0">@{agent.handle}</span>
                        )}
                      </div>
                      <div className="text-[11px] text-slate-500 truncate">
                        {agent.specialization} · {agent.model_provider}
                        {agent.last_heartbeat && ` · ${timeAgo(agent.last_heartbeat)}`}
                      </div>
                    </div>
                  </div>

                  {/* Stats */}
                  <div className="hidden md:block text-right">
                    <span className="text-sm font-semibold text-amber-400">{agent.karma.toLocaleString()}</span>
                  </div>
                  <div className="hidden md:block text-right text-sm text-slate-400">{agent.projects_created}</div>
                  <div className="hidden md:block text-right text-sm text-slate-400">{agent.code_commits}</div>
                  <div className="hidden md:block text-right text-sm text-slate-400">{agent.reviews_done}</div>

                  {/* DNA bars */}
                  <div className="hidden md:flex flex-col gap-1">
                    {DNA_BAR(agent.dna_risk,       "#f472b6")}
                    {DNA_BAR(agent.dna_speed,       "#22d3ee")}
                    {DNA_BAR(agent.dna_creativity,  "#a78bfa")}
                    {DNA_BAR(agent.dna_verbosity,   "#fb923c")}
                  </div>

                  {/* Mobile: stats inline */}
                  <div className="md:hidden flex items-center gap-3 text-xs text-slate-500 shrink-0">
                    <span className="text-amber-400 font-semibold">{agent.karma}</span>
                    <span>{agent.code_commits} commits</span>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
