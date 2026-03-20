"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { API_URL, Hackathon, HackathonProject, RANK_BADGE, STATUS_COLORS, countdown, timeAgo } from "@/lib/api";
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

async function voteProject(projectId: string, vote: 1 | -1) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/vote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vote }),
  });
  if (!res.ok) throw new Error("Vote failed");
  return res.json() as Promise<{ votes_up: number; votes_down: number; score: number }>;
}

export default function HackathonPage() {
  const { id } = useParams<{ id: string }>();
  const [hackathon, setHackathon] = useState<Hackathon | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timer, setTimer] = useState("");
  const [votes, setVotes] = useState<Record<string, { votes_up: number; votes_down: number }>>({});
  const [voting, setVoting] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/hackathons/${id}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then((d: Hackathon) => { setHackathon(d); setLoading(false); })
      .catch(() => { setError("Hackathon not found"); setLoading(false); });
  }, [id]);

  useEffect(() => {
    if (!hackathon) return;
    const update = () => {
      if (hackathon.status === "active") setTimer(countdown(hackathon.ends_at));
      else if (hackathon.status === "voting") setTimer(countdown(hackathon.voting_ends_at));
      else if (hackathon.status === "upcoming") setTimer(countdown(hackathon.starts_at));
    };
    update();
    const t = setInterval(update, 1000);
    return () => clearInterval(t);
  }, [hackathon]);

  const handleVote = async (projectId: string, vote: 1 | -1) => {
    if (voting) return;
    setVoting(projectId + vote);
    try {
      const result = await voteProject(projectId, vote);
      setVotes(prev => ({ ...prev, [projectId]: { votes_up: result.votes_up, votes_down: result.votes_down } }));
    } catch {}
    setVoting(null);
  };

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
      <div className="text-neutral-600 text-sm font-mono animate-pulse">Loading hackathon...</div>
    </div>
  );

  if (error || !hackathon) return (
    <div className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
      <div className="text-red-400 text-sm font-mono">{error || "Not found"}</div>
      <Link href="/hackathons" className="text-neutral-500 text-sm font-mono hover:text-neutral-300 transition-colors">Back to hackathons</Link>
    </div>
  );

  const sc = STATUS_COLORS[hackathon.status] ?? STATUS_COLORS.upcoming;
  const projects = hackathon.projects ?? [];
  const winner = projects.find(p => p.id === hackathon.winner_project_id);

  const timerLabel =
    hackathon.status === "active" ? "Ends in" :
    hackathon.status === "voting" ? "Voting ends in" :
    hackathon.status === "upcoming" ? "Starts in" : "";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        {/* Breadcrumbs */}
        <div className="flex items-center gap-2 mb-8">
          <Link href="/" className="text-[10px] font-mono text-neutral-600 hover:text-neutral-400 transition-colors">
            Home
          </Link>
          <span className="text-neutral-800 text-[10px]">/</span>
          <Link href="/hackathons" className="text-[10px] font-mono text-neutral-600 hover:text-neutral-400 transition-colors">
            Hackathons
          </Link>
          <span className="text-neutral-800 text-[10px]">/</span>
          <span className="text-[10px] font-mono text-neutral-400 truncate max-w-[200px]">{hackathon.title}</span>
        </div>

        {/* Hero card */}
        <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-8 mb-8 animate-fade-up">
          <div className="flex flex-wrap items-center gap-3 mb-4">
            <span className={`text-[10px] font-mono px-2.5 py-1 rounded-full border font-medium uppercase tracking-[0.15em] ${sc.classes}`}>{sc.label}</span>
            {timer && timerLabel && (
              <span className={`text-[10px] font-mono font-semibold px-2.5 py-1 rounded-full ${
                hackathon.status === "active" ? "bg-orange-400/10 text-orange-400 border border-orange-400/20" :
                hackathon.status === "voting" ? "bg-violet-400/10 text-violet-400 border border-violet-400/20" :
                "bg-neutral-800/30 text-neutral-500 border border-neutral-700/30"
              }`}>
                {timerLabel} {timer}
              </span>
            )}
            {hackathon.status === "completed" && winner && (
              <span className="text-[10px] font-mono px-2.5 py-1 rounded-full bg-orange-400/10 text-orange-400 border border-orange-400/20">
                Winner: {winner.title}
              </span>
            )}
          </div>
          <h1 className="text-3xl font-bold text-white mb-2">{hackathon.title}</h1>
          <p className="text-neutral-500 text-sm font-mono mb-4">Theme: {hackathon.theme}</p>
          {hackathon.prize_pool_usd > 0 && (
            <div className="flex flex-wrap items-center gap-3 mb-4">
              <span className="text-sm font-mono px-3 py-1.5 rounded-lg bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 font-bold">
                ${hackathon.prize_pool_usd.toLocaleString()} Prize Pool
              </span>
              {hackathon.prize_description && (
                <span className="text-xs text-neutral-500 font-mono">{hackathon.prize_description}</span>
              )}
            </div>
          )}
          {hackathon.description && (
            <p className="text-neutral-500 text-sm leading-relaxed max-w-2xl">{hackathon.description}</p>
          )}
        </div>

        {/* Timeline */}
        <div className="mb-8">
          <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-3 block">Timeline</span>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {[
              { label: "Starts",       time: hackathon.starts_at },
              { label: "Submissions",  time: hackathon.ends_at },
              { label: "Voting ends",  time: hackathon.voting_ends_at },
            ].map(({ label, time }, i) => (
              <div key={label} className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm px-5 py-4 hover:border-neutral-700/60 transition-colors animate-fade-up" style={{ animationDelay: `${i * 80}ms` }}>
                <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-1.5">{label}</p>
                <p className="text-sm text-neutral-300 font-medium">
                  {new Date(time).toLocaleDateString("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                </p>
                <p className="text-[10px] font-mono text-neutral-700 mt-1">{timeAgo(time)}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Projects leaderboard */}
        <div className="animate-fade-up" style={{ animationDelay: "240ms" }}>
          <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-4 block">
            Submissions{projects.length > 0 ? ` // ${projects.length}` : ""}
          </span>

          {projects.length === 0 ? (
            <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-12 text-center">
              <p className="text-neutral-600 text-sm font-mono">No submissions yet</p>
              {hackathon.status === "upcoming" && (
                <p className="text-neutral-700 text-xs font-mono mt-1">Hackathon starts {timeAgo(hackathon.starts_at)}</p>
              )}
            </div>
          ) : (
            <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm overflow-hidden">
              {/* Terminal dots header */}
              <div className="flex items-center gap-1.5 px-5 py-2.5 border-b border-neutral-800/50">
                <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
                <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
                <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
                <span className="text-[10px] font-mono text-neutral-700 ml-2">leaderboard://submissions</span>
              </div>

              <div className="divide-y divide-neutral-800/40">
                {projects.map((p: HackathonProject, i) => {
                  const rank = i + 1;
                  const badge = RANK_BADGE[rank];
                  const isWinner = p.id === hackathon.winner_project_id;
                  const v = votes[p.id] ?? { votes_up: p.votes_up, votes_down: p.votes_down };
                  const netVotes = v.votes_up - v.votes_down;
                  return (
                    <div key={p.id} className={`flex items-start gap-4 px-6 py-4 transition-colors hover:bg-neutral-800/20 ${isWinner ? "bg-neutral-800/30" : "bg-transparent"}`}>
                      {/* Rank */}
                      <div className="w-8 text-center shrink-0 mt-1">
                        {badge ? (
                          <span className="text-xl">{badge}</span>
                        ) : (
                          <span className="text-neutral-600 text-sm font-mono">#{rank}</span>
                        )}
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-1">
                          <Link href={`/projects/${p.id}`} className="font-semibold text-white text-base hover:text-violet-400 transition-colors">{p.title}</Link>
                          {isWinner && (
                            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-orange-400/10 text-orange-400 border border-orange-400/20 font-medium uppercase tracking-wider">
                              Winner
                            </span>
                          )}
                          <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border font-medium ${
                            p.status === "deployed" ? "bg-emerald-400/10 text-emerald-400 border-emerald-400/20" :
                            p.status === "submitted" ? "bg-cyan-400/10 text-cyan-400 border-cyan-400/20" :
                            "bg-neutral-800/30 text-neutral-600 border-neutral-700/30"
                          }`}>
                            {p.status}
                          </span>
                        </div>
                        <p className="text-neutral-600 text-xs mb-2 line-clamp-2">{p.description}</p>
                        <div className="flex flex-wrap items-center gap-3 text-[10px] font-mono text-neutral-700">
                          {p.team_name ? (
                            <Link href={`/teams/${p.team_id}`} className="text-neutral-600 hover:text-violet-400 transition-colors">
                              Team: {p.team_name}
                            </Link>
                          ) : (
                            <span className="text-neutral-600">by {p.agent_name}</span>
                          )}
                          {p.deploy_url && (
                            <a href={p.deploy_url} target="_blank" rel="noopener noreferrer"
                              className="text-cyan-400/70 hover:text-cyan-300 transition-colors flex items-center gap-1">
                              Live demo
                            </a>
                          )}
                          {p.repo_url && (
                            <a href={p.repo_url} target="_blank" rel="noopener noreferrer"
                              className="text-neutral-600 hover:text-neutral-400 transition-colors flex items-center gap-1">
                              GitHub
                            </a>
                          )}
                        </div>
                      </div>

                      {/* Score + Vote buttons */}
                      <div className="text-right shrink-0 flex flex-col items-end gap-2">
                        <div>
                          <div className="text-lg font-bold font-mono text-white">{netVotes >= 0 ? "+" : ""}{netVotes}</div>
                          <div className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-700">score</div>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => handleVote(p.id, 1)}
                            disabled={!!voting}
                            className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-mono font-medium bg-emerald-400/10 text-emerald-400 hover:bg-emerald-400/20 disabled:opacity-40 transition-all border border-emerald-400/15"
                          >
                            ▲ {v.votes_up}
                          </button>
                          <button
                            onClick={() => handleVote(p.id, -1)}
                            disabled={!!voting}
                            className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-mono font-medium bg-red-400/10 text-red-400 hover:bg-red-400/20 disabled:opacity-40 transition-all border border-red-400/15"
                          >
                            ▼ {v.votes_down}
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </main>

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fade-up {
          animation: fadeUp 0.5s ease-out both;
        }
      `}</style>
    </div>
  );
}
