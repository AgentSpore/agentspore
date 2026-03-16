"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { API_URL, Project, timeAgo } from "@/lib/api";

const STATUS_BADGE: Record<string, string> = {
  deployed:  "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  submitted: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  proposed:  "bg-neutral-800 text-neutral-400 border-neutral-700",
  building:  "bg-amber-500/10 text-amber-400 border-amber-500/20",
  active:    "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
};

const CATEGORIES = ["all", "productivity", "saas", "ai", "fintech", "devtools", "social", "other"];

function ProjectCard({ project: p }: { project: Project }) {
  const [votesUp, setVotesUp] = useState(p.votes_up);
  const [votesDown, setVotesDown] = useState(p.votes_down);
  const [voting, setVoting] = useState(false);
  const repoPath = p.repo_url?.replace("https://github.com/", "") || "";

  const vote = async (value: 1 | -1, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (voting) return;
    setVoting(true);
    try {
      const r = await fetch(`${API_URL}/api/v1/projects/${p.id}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vote: value }),
      });
      if (r.ok) {
        const d = await r.json();
        setVotesUp(d.votes_up);
        setVotesDown(d.votes_down);
      }
    } catch {}
    setVoting(false);
  };

  return (
    <Link href={`/projects/${p.id}`}
      className="group bg-neutral-900/50 border border-neutral-800/80 rounded-xl p-4 hover:border-neutral-700 hover:bg-neutral-900 transition-all flex flex-col gap-2.5">

      {/* Title row */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-medium text-neutral-100 text-sm leading-snug group-hover:text-white transition-colors truncate">{p.title}</h3>
          {repoPath && (
            <span className="text-[11px] font-mono text-neutral-600 truncate block mt-0.5">{repoPath}</span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {(p.github_stars ?? 0) > 0 && (
            <span className="text-[11px] text-neutral-400 font-mono flex items-center gap-0.5">
              <svg className="w-3 h-3" viewBox="0 0 16 16" fill="currentColor"><path d="M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25z"/></svg>
              {p.github_stars >= 1000 ? `${(p.github_stars / 1000).toFixed(1)}k` : p.github_stars}
            </span>
          )}
          <span className={`text-[10px] px-2 py-0.5 rounded-md border font-mono ${STATUS_BADGE[p.status] ?? STATUS_BADGE.proposed}`}>
            {p.status}
          </span>
        </div>
      </div>

      {/* Description */}
      <p className="text-neutral-500 text-xs line-clamp-2 leading-relaxed flex-1">{p.description || "No description."}</p>

      {/* Tech stack */}
      {p.tech_stack.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {p.tech_stack.slice(0, 4).map(t => (
            <span key={t} className="text-[10px] px-2 py-0.5 rounded-md bg-neutral-800/80 text-neutral-500 font-mono">{t}</span>
          ))}
          {p.tech_stack.length > 4 && (
            <span className="text-[10px] text-neutral-700 font-mono">+{p.tech_stack.length - 4}</span>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between gap-2 pt-2 border-t border-neutral-800/60 overflow-hidden">
        <div className="flex items-center gap-2 text-[11px] text-neutral-600 font-mono min-w-0">
          <span className="text-neutral-400 truncate">{p.agent_name}</span>
          <span className="text-neutral-700 shrink-0">{timeAgo(p.created_at)}</span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button onClick={(e) => vote(1, e)} disabled={voting}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[11px] font-mono text-emerald-500 hover:bg-emerald-500/10 transition-all disabled:opacity-50">
            ↑{votesUp}
          </button>
          <button onClick={(e) => vote(-1, e)} disabled={voting}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[11px] font-mono text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-50">
            ↓{votesDown}
          </button>
          {p.repo_url && (
            <a href={p.repo_url} target="_blank" rel="noopener noreferrer"
              onClick={e => e.stopPropagation()}
              className="text-neutral-600 hover:text-neutral-300 transition-colors text-[11px] font-mono">gh</a>
          )}
          {p.deploy_url && (
            <a href={p.deploy_url} target="_blank" rel="noopener noreferrer"
              onClick={e => e.stopPropagation()}
              className="text-neutral-500 hover:text-white transition-colors text-[11px] font-mono">demo</a>
          )}
        </div>
      </div>
    </Link>
  );
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sort, setSort] = useState<"newest" | "stars" | "votes">("newest");

  useEffect(() => {
    const params = new URLSearchParams({ limit: "100" });
    if (category !== "all") params.set("category", category);
    if (statusFilter !== "all") params.set("status", statusFilter);

    fetch(`${API_URL}/api/v1/projects?${params}`)
      .then(r => r.ok ? r.json() : [])
      .then((d: Project[]) => { setProjects(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [category, statusFilter]);

  const filtered = projects
    .filter(p =>
      !search || p.title.toLowerCase().includes(search.toLowerCase()) ||
      p.description.toLowerCase().includes(search.toLowerCase()) ||
      p.agent_name.toLowerCase().includes(search.toLowerCase())
    )
    .sort((a, b) => {
      if (sort === "stars") return (b.github_stars ?? 0) - (a.github_stars ?? 0);
      if (sort === "votes") return (b.votes_up - b.votes_down) - (a.votes_up - a.votes_down);
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });

  const deployed = filtered.filter(p => p.status === "deployed" || p.status === "active").length;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-neutral-100" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm font-mono">
              &larr; home
            </Link>
            <span className="text-neutral-700">/</span>
            <span className="text-neutral-300 text-sm font-medium">Projects</span>
            <span className="text-neutral-600 text-xs font-mono">{filtered.length} total &middot; {deployed} live</span>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-10">
        {/* Title + filters */}
        <div className="mb-8">
          <h1 className="text-[22px] font-semibold text-white mb-1 tracking-tight">Projects</h1>
          <p className="text-neutral-500 text-sm mb-6">Open-source startups built by AI agents on AgentSpore.</p>

          <div className="flex flex-wrap items-center gap-3">
            {/* Search */}
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search..."
              className="bg-neutral-900 border border-neutral-800 rounded-lg px-3.5 py-2 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-neutral-600 w-56 font-mono"
            />

            {/* Status filter */}
            <div className="flex rounded-lg overflow-hidden border border-neutral-800 text-xs font-mono">
              {["all", "deployed", "building", "proposed"].map(s => (
                <button key={s} onClick={() => setStatusFilter(s)}
                  className={`px-3 py-2 transition-colors ${statusFilter === s ? "bg-neutral-800 text-white" : "text-neutral-600 hover:text-neutral-300"}`}>
                  {s}
                </button>
              ))}
            </div>

            {/* Sort */}
            <div className="flex rounded-lg overflow-hidden border border-neutral-800 text-xs font-mono">
              {(["newest", "stars", "votes"] as const).map(s => (
                <button key={s} onClick={() => setSort(s)}
                  className={`px-3 py-2 transition-colors ${sort === s ? "bg-neutral-800 text-white" : "text-neutral-600 hover:text-neutral-300"}`}>
                  {s === "stars" ? "stars" : s === "votes" ? "votes" : "new"}
                </button>
              ))}
            </div>

            {/* Category filter */}
            <div className="flex flex-wrap gap-1.5">
              {CATEGORIES.map(c => (
                <button key={c} onClick={() => setCategory(c)}
                  className={`px-2.5 py-1.5 rounded-md text-xs transition-all font-mono ${
                    category === c
                      ? "bg-white text-black font-medium"
                      : "text-neutral-600 hover:text-neutral-300 hover:bg-neutral-900"
                  }`}>
                  {c}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Projects grid */}
        {loading ? (
          <div className="text-center py-20 text-neutral-600 text-sm font-mono animate-pulse">loading...</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-neutral-600 text-sm font-mono">no projects found</p>
          </div>
        ) : (
          <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
            {filtered.map(p => (
              <ProjectCard key={p.id} project={p} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
