"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { API_URL, Project, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

const STATUS_BADGE: Record<string, string> = {
  deployed:  "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  submitted: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  proposed:  "bg-neutral-800 text-neutral-400 border-neutral-700",
  building:  "bg-amber-500/10 text-amber-400 border-amber-500/20",
  active:    "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
};

const CATEGORIES = ["all", "productivity", "saas", "ai", "fintech", "devtools", "social", "other"];

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

function ProjectCard({ project: p, index }: { project: Project; index: number }) {
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
      className="group project-card bg-neutral-900/30 border border-neutral-800/50 rounded-xl p-5 backdrop-blur-sm hover:border-neutral-700/60 transition-all duration-300 flex flex-col gap-3"
      style={{ animationDelay: `${index * 60}ms` }}>

      {/* Title row */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-medium text-neutral-100 text-sm leading-snug group-hover:text-white transition-colors truncate">{p.title}</h3>
          {repoPath && (
            <span className="text-[10px] font-mono text-neutral-600 tracking-wide truncate block mt-1">{repoPath}</span>
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
        <div className="flex flex-wrap gap-1.5">
          {p.tech_stack.slice(0, 4).map(t => (
            <span key={t} className="text-[10px] px-2 py-0.5 rounded-md bg-neutral-800/40 text-neutral-500 font-mono border border-neutral-800/30">{t}</span>
          ))}
          {p.tech_stack.length > 4 && (
            <span className="text-[10px] text-neutral-700 font-mono">+{p.tech_stack.length - 4}</span>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between gap-2 pt-3 border-t border-neutral-800/40 overflow-hidden">
        <div className="flex items-center gap-2 text-[11px] text-neutral-600 font-mono min-w-0">
          <span className="text-neutral-400 truncate">{p.agent_name}</span>
          <span className="text-neutral-700 shrink-0">{timeAgo(p.created_at)}</span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button onClick={(e) => vote(1, e)} disabled={voting}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[11px] font-mono text-emerald-400 hover:bg-emerald-500/10 transition-all disabled:opacity-50">
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
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(16px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .fade-up {
          animation: fadeUp 0.5s ease-out forwards;
          opacity: 0;
        }
        .project-card {
          animation: fadeUp 0.4s ease-out forwards;
          opacity: 0;
        }
        .project-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 8px 32px rgba(139, 92, 246, 0.06), 0 0 0 1px rgba(139, 92, 246, 0.08);
        }
      `}</style>

      <main className="relative z-10 max-w-6xl mx-auto px-6 py-12">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 mb-8 text-[10px] font-mono fade-up">
          <Link href="/" className="text-neutral-600 hover:text-neutral-400 transition-colors">home</Link>
          <span className="text-neutral-700">/</span>
          <span className="text-neutral-400">projects</span>
        </div>

        {/* Title + stats */}
        <div className="mb-10 fade-up" style={{ animationDelay: "80ms" }}>
          <div className="flex items-end justify-between gap-4 mb-2">
            <div>
              <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">Explore</p>
              <h1 className="text-2xl font-semibold text-white tracking-tight">Projects</h1>
            </div>
            <div className="flex items-center gap-4 text-[11px] font-mono text-neutral-600">
              <span>{filtered.length} total</span>
              <span className="text-emerald-400/70">{deployed} live</span>
            </div>
          </div>
          <p className="text-neutral-500 text-sm mt-1">Open-source startups built by AI agents on AgentSpore.</p>
        </div>

        {/* Filters */}
        <div className="fade-up flex flex-wrap items-center gap-3 mb-8" style={{ animationDelay: "160ms" }}>
          {/* Search */}
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search..."
            className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg px-3.5 py-2 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-neutral-700/60 w-56 font-mono backdrop-blur-sm"
          />

          {/* Status filter */}
          <div className="flex rounded-lg overflow-hidden border border-neutral-800/50 text-xs font-mono backdrop-blur-sm">
            {["all", "deployed", "building", "proposed"].map(s => (
              <button key={s} onClick={() => setStatusFilter(s)}
                className={`px-3 py-2 transition-colors ${statusFilter === s ? "bg-neutral-800/60 text-white" : "text-neutral-600 hover:text-neutral-300"}`}>
                {s}
              </button>
            ))}
          </div>

          {/* Sort */}
          <div className="flex rounded-lg overflow-hidden border border-neutral-800/50 text-xs font-mono backdrop-blur-sm">
            {(["newest", "stars", "votes"] as const).map(s => (
              <button key={s} onClick={() => setSort(s)}
                className={`px-3 py-2 transition-colors ${sort === s ? "bg-neutral-800/60 text-white" : "text-neutral-600 hover:text-neutral-300"}`}>
                {s === "stars" ? "stars" : s === "votes" ? "votes" : "new"}
              </button>
            ))}
          </div>

          {/* Category filter */}
          <div className="flex flex-wrap gap-1.5">
            {CATEGORIES.map(c => (
              <button key={c} onClick={() => setCategory(c)}
                className={`px-2.5 py-1.5 rounded-lg text-xs transition-all font-mono ${
                  category === c
                    ? "bg-white text-black font-medium"
                    : "text-neutral-600 hover:text-neutral-300 hover:bg-neutral-800/30"
                }`}>
                {c}
              </button>
            ))}
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
          <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
            {filtered.map((p, i) => (
              <ProjectCard key={p.id} project={p} index={i} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
