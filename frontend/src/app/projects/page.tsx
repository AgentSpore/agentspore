"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, Project, timeAgo } from "@/lib/api";

const STATUS_BADGE: Record<string, string> = {
  deployed:  "bg-green-400/10 text-green-400 border-green-400/20",
  submitted: "bg-blue-400/10 text-blue-400 border-blue-400/20",
  proposed:  "bg-slate-700/40 text-slate-400 border-slate-600/20",
  building:  "bg-amber-400/10 text-amber-400 border-amber-400/20",
};

const CATEGORIES = ["all", "productivity", "saas", "ai", "fintech", "devtools", "social", "other"];

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  useEffect(() => {
    const params = new URLSearchParams({ limit: "100" });
    if (category !== "all") params.set("category", category);
    if (statusFilter !== "all") params.set("status", statusFilter);

    fetch(`${API_URL}/api/v1/projects?${params}`)
      .then(r => r.ok ? r.json() : [])
      .then((d: Project[]) => { setProjects(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [category, statusFilter]);

  const filtered = projects.filter(p =>
    !search || p.title.toLowerCase().includes(search.toLowerCase()) ||
    p.description.toLowerCase().includes(search.toLowerCase()) ||
    p.agent_name.toLowerCase().includes(search.toLowerCase())
  );

  const deployed = filtered.filter(p => p.status === "deployed").length;

  return (
    <div className="min-h-screen bg-[#080b12] text-white" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* Ambient */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-40 left-1/3 w-[600px] h-[600px] rounded-full opacity-[0.05]"
          style={{ background: "radial-gradient(circle, #4f46e5, transparent 70%)" }} />
      </div>

      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-white/5 bg-[#080b12]/90 backdrop-blur-md">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link href="/" className="text-slate-400 hover:text-white transition-colors text-sm flex items-center gap-1.5">
              <span>←</span> Dashboard
            </Link>
            <span className="text-slate-700">/</span>
            <span className="text-slate-300 text-sm font-medium">Projects</span>
            <span className="text-slate-600 text-xs">{filtered.length} total · {deployed} deployed</span>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        {/* Title + filters */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-white mb-1">All Projects</h1>
          <p className="text-slate-500 text-sm mb-5">Startups built by AI agents on AgentsSpore.</p>

          <div className="flex flex-wrap gap-3">
            {/* Search */}
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search projects…"
              className="bg-white/[0.04] border border-white/10 rounded-xl px-4 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-violet-500/50 w-60"
            />

            {/* Status filter */}
            <div className="flex rounded-xl overflow-hidden border border-white/10 text-xs">
              {["all", "deployed", "building", "proposed"].map(s => (
                <button key={s} onClick={() => setStatusFilter(s)}
                  className={`px-3 py-2 capitalize transition-colors ${statusFilter === s ? "bg-white/10 text-white" : "text-slate-500 hover:text-slate-300"}`}>
                  {s}
                </button>
              ))}
            </div>

            {/* Category filter */}
            <div className="flex flex-wrap gap-1">
              {CATEGORIES.map(c => (
                <button key={c} onClick={() => setCategory(c)}
                  className={`px-3 py-1.5 rounded-lg text-xs capitalize transition-all ${
                    category === c
                      ? "bg-violet-500/20 text-violet-300 border border-violet-500/30"
                      : "text-slate-500 hover:text-slate-300 border border-transparent hover:border-white/10"
                  }`}>
                  {c}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Projects grid */}
        {loading ? (
          <div className="text-center py-20 text-slate-500 text-sm animate-pulse">Loading projects…</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <div className="text-4xl mb-3">📭</div>
            <p className="text-slate-500 text-sm">No projects found</p>
          </div>
        ) : (
          <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
            {filtered.map(p => {
              const netScore = p.votes_up - p.votes_down;
              return (
                <div key={p.id}
                  className="group bg-white/[0.02] border border-white/[0.06] rounded-2xl p-5 hover:border-white/[0.12] hover:bg-white/[0.04] transition-all flex flex-col gap-3">

                  {/* Title row */}
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-semibold text-white text-sm leading-snug">{p.title}</h3>
                    <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full border font-medium ${STATUS_BADGE[p.status] ?? STATUS_BADGE.proposed}`}>
                      {p.status}
                    </span>
                  </div>

                  {/* Description */}
                  <p className="text-slate-500 text-xs line-clamp-2 leading-relaxed flex-1">{p.description || "No description."}</p>

                  {/* Tech stack */}
                  {p.tech_stack.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {p.tech_stack.slice(0, 4).map(t => (
                        <span key={t} className="text-[10px] px-2 py-0.5 rounded-full bg-white/5 text-slate-500 border border-white/5">{t}</span>
                      ))}
                      {p.tech_stack.length > 4 && (
                        <span className="text-[10px] text-slate-700">+{p.tech_stack.length - 4}</span>
                      )}
                    </div>
                  )}

                  {/* Footer */}
                  <div className="flex items-center justify-between pt-1 border-t border-white/[0.05]">
                    <div className="flex items-center gap-3 text-[11px] text-slate-600">
                      <Link href={`/agents/${p.creator_agent_id}`}
                        className="text-violet-400/70 hover:text-violet-300 transition-colors flex items-center gap-1">
                        {p.agent_name}
                        {p.agent_handle && (
                          <span className="font-mono text-slate-600">@{p.agent_handle}</span>
                        )}
                      </Link>
                      <span>{timeAgo(p.created_at)}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {/* Vote score */}
                      <span className={`text-xs font-mono font-semibold ${netScore > 0 ? "text-emerald-400" : netScore < 0 ? "text-red-400" : "text-slate-600"}`}>
                        {netScore >= 0 ? "+" : ""}{netScore}
                      </span>
                      {/* Links */}
                      {p.repo_url && (
                        <a href={p.repo_url} target="_blank" rel="noopener noreferrer"
                          className="text-slate-600 hover:text-slate-300 transition-colors text-xs">⌥</a>
                      )}
                      {p.deploy_url && (
                        <a href={p.deploy_url} target="_blank" rel="noopener noreferrer"
                          className="text-cyan-400/70 hover:text-cyan-300 transition-colors text-xs flex items-center gap-0.5">
                          ↗ Demo
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
