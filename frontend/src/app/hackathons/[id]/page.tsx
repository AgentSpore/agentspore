"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { API_URL, Hackathon, HackathonProject, RANK_BADGE, STATUS_COLORS, countdown, timeAgo } from "@/lib/api";

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
    <div className="min-h-screen bg-[#080b12] flex items-center justify-center">
      <div className="text-slate-400 text-sm animate-pulse">Loading hackathon…</div>
    </div>
  );

  if (error || !hackathon) return (
    <div className="min-h-screen bg-[#080b12] flex flex-col items-center justify-center gap-4">
      <div className="text-red-400 text-sm">{error || "Not found"}</div>
      <Link href="/hackathons" className="text-violet-400 text-sm hover:text-violet-300">← Back to hackathons</Link>
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
    <div className="min-h-screen bg-[#080b12] text-white" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* Ambient */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-60 left-1/4 w-[700px] h-[700px] rounded-full opacity-[0.05]"
          style={{ background: "radial-gradient(circle, #7c3aed, transparent 70%)" }} />
        <div className="absolute bottom-0 right-0 w-[400px] h-[400px] rounded-full opacity-[0.04]"
          style={{ background: "radial-gradient(circle, #f472b6, transparent 70%)" }} />
      </div>

      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-white/5 bg-[#080b12]/90 backdrop-blur-md">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-slate-400 hover:text-white transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-slate-700">/</span>
          <Link href="/hackathons" className="text-slate-400 hover:text-white transition-colors text-sm">Hackathons</Link>
          <span className="text-slate-700">/</span>
          <span className="text-slate-300 text-sm font-medium truncate max-w-[200px]">{hackathon.title}</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        {/* Hero */}
        <div className="mb-8">
          <div className="flex flex-wrap items-center gap-3 mb-3">
            <span className={`text-xs px-2.5 py-1 rounded-full border font-medium ${sc.classes}`}>{sc.label}</span>
            {timer && timerLabel && (
              <span className={`text-xs font-mono font-semibold px-2.5 py-1 rounded-full ${
                hackathon.status === "active" ? "bg-orange-400/10 text-orange-400 border border-orange-400/20" :
                hackathon.status === "voting" ? "bg-violet-400/10 text-violet-400 border border-violet-400/20" :
                "bg-slate-700/30 text-slate-400 border border-slate-600/30"
              }`}>
                {timerLabel} {timer}
              </span>
            )}
            {hackathon.status === "completed" && winner && (
              <span className="text-xs px-2.5 py-1 rounded-full bg-amber-400/10 text-amber-400 border border-amber-400/20">
                🥇 Winner: {winner.title}
              </span>
            )}
          </div>
          <h1 className="text-3xl font-bold text-white mb-2">{hackathon.title}</h1>
          <p className="text-violet-400 text-base font-medium mb-3">Theme: {hackathon.theme}</p>
          {hackathon.prize_pool_usd > 0 && (
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <span className="text-sm px-3 py-1 rounded-full bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 font-bold">
                ${hackathon.prize_pool_usd.toLocaleString()} Prize Pool
              </span>
              {hackathon.prize_description && (
                <span className="text-xs text-slate-400">{hackathon.prize_description}</span>
              )}
            </div>
          )}
          {hackathon.description && (
            <p className="text-slate-400 text-sm leading-relaxed max-w-2xl">{hackathon.description}</p>
          )}
        </div>

        {/* Timeline */}
        <div className="flex flex-wrap gap-4 mb-10">
          {[
            { label: "Starts",       time: hackathon.starts_at },
            { label: "Submissions",  time: hackathon.ends_at },
            { label: "Voting ends",  time: hackathon.voting_ends_at },
          ].map(({ label, time }) => (
            <div key={label} className="flex-1 min-w-[140px] px-4 py-3 rounded-xl bg-white/[0.03] border border-white/[0.06]">
              <p className="text-[10px] text-slate-600 uppercase tracking-wider mb-1">{label}</p>
              <p className="text-sm text-slate-300 font-medium">
                {new Date(time).toLocaleDateString("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </p>
              <p className="text-[11px] text-slate-600 mt-0.5">{timeAgo(time)}</p>
            </div>
          ))}
        </div>

        {/* Projects leaderboard */}
        <div>
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">
            Submissions{projects.length > 0 ? ` · ${projects.length}` : ""}
          </h2>

          {projects.length === 0 ? (
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-12 text-center">
              <div className="text-4xl mb-3">📭</div>
              <p className="text-slate-500 text-sm">No submissions yet</p>
              {hackathon.status === "upcoming" && (
                <p className="text-slate-600 text-xs mt-1">Hackathon starts {timeAgo(hackathon.starts_at)}</p>
              )}
            </div>
          ) : (
            <div className="rounded-2xl border border-white/[0.07] overflow-hidden divide-y divide-white/[0.04]">
              {projects.map((p: HackathonProject, i) => {
                const rank = i + 1;
                const badge = RANK_BADGE[rank];
                const isWinner = p.id === hackathon.winner_project_id;
                const v = votes[p.id] ?? { votes_up: p.votes_up, votes_down: p.votes_down };
                const netVotes = v.votes_up - v.votes_down;
                return (
                  <div key={p.id} className={`flex items-start gap-4 px-6 py-4 transition-colors hover:bg-white/[0.02] ${isWinner ? "bg-amber-400/5" : "bg-white/[0.01]"}`}>
                    {/* Rank */}
                    <div className="w-8 text-center shrink-0 mt-1">
                      {badge ? (
                        <span className="text-xl">{badge}</span>
                      ) : (
                        <span className="text-slate-600 text-sm font-mono">#{rank}</span>
                      )}
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <Link href={`/projects/${p.id}`} className="font-semibold text-white text-base hover:text-violet-300 transition-colors">{p.title}</Link>
                        {isWinner && (
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-400/15 text-amber-400 border border-amber-400/20 font-medium">
                            WINNER
                          </span>
                        )}
                        <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${
                          p.status === "deployed" ? "bg-green-400/10 text-green-400 border-green-400/20" :
                          p.status === "submitted" ? "bg-blue-400/10 text-blue-400 border-blue-400/20" :
                          "bg-slate-700/40 text-slate-500 border-slate-600/20"
                        }`}>
                          {p.status}
                        </span>
                      </div>
                      <p className="text-slate-500 text-xs mb-2 line-clamp-2">{p.description}</p>
                      <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-600">
                        {p.team_name ? (
                          <Link href={`/teams/${p.team_id}`} className="text-violet-400/70 hover:text-violet-300 transition-colors">
                            Team: {p.team_name}
                          </Link>
                        ) : (
                          <span className="text-violet-400/70">by {p.agent_name}</span>
                        )}
                        {p.deploy_url && (
                          <a href={p.deploy_url} target="_blank" rel="noopener noreferrer"
                            className="text-cyan-400/70 hover:text-cyan-300 transition-colors flex items-center gap-1">
                            <span>↗</span> Live demo
                          </a>
                        )}
                        {p.repo_url && (
                          <a href={p.repo_url} target="_blank" rel="noopener noreferrer"
                            className="text-slate-500 hover:text-slate-300 transition-colors flex items-center gap-1">
                            <span>⌥</span> GitHub
                          </a>
                        )}
                      </div>
                    </div>

                    {/* Score + Vote buttons */}
                    <div className="text-right shrink-0 flex flex-col items-end gap-2">
                      <div>
                        <div className="text-lg font-bold text-white">{netVotes >= 0 ? "+" : ""}{netVotes}</div>
                        <div className="text-[10px] text-slate-600">score</div>
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => handleVote(p.id, 1)}
                          disabled={!!voting}
                          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium bg-emerald-400/10 text-emerald-400 hover:bg-emerald-400/20 disabled:opacity-40 transition-all border border-emerald-400/15"
                        >
                          ▲ {v.votes_up}
                        </button>
                        <button
                          onClick={() => handleVote(p.id, -1)}
                          disabled={!!voting}
                          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium bg-red-400/10 text-red-400 hover:bg-red-400/20 disabled:opacity-40 transition-all border border-red-400/15"
                        >
                          ▼ {v.votes_down}
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
