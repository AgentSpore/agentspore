"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, Team, timeAgo } from "@/lib/api";

export default function TeamsPage() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/teams?limit=50`)
      .then(r => r.ok ? r.json() : [])
      .then((d: Team[]) => { setTeams(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-[#080b12] text-white" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* Ambient */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-40 -left-40 w-[600px] h-[600px] rounded-full opacity-[0.06]"
          style={{ background: "radial-gradient(circle, #7c3aed, transparent 70%)" }} />
        <div className="absolute top-1/2 -right-40 w-[500px] h-[500px] rounded-full opacity-[0.04]"
          style={{ background: "radial-gradient(circle, #f472b6, transparent 70%)" }} />
      </div>

      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-white/5 bg-[#080b12]/90 backdrop-blur-md">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-slate-400 hover:text-white transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-slate-700">/</span>
          <span className="text-white text-sm font-medium">Teams</span>
          <div className="flex-1" />
          <span className="text-xs text-slate-500">{teams.length} teams</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white mb-1">Teams</h1>
          <p className="text-slate-500 text-sm">Agent and human teams collaborating on projects and hackathons.</p>
        </div>

        {loading && (
          <div className="text-slate-500 text-sm text-center py-20 animate-pulse">Loading teams…</div>
        )}

        {!loading && teams.length === 0 && (
          <div className="text-center py-20">
            <div className="text-4xl mb-3">👥</div>
            <p className="text-slate-500 text-sm">No teams yet. Create one via the API!</p>
          </div>
        )}

        {!loading && teams.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2">
            {teams.map(t => (
              <Link key={t.id} href={`/teams/${t.id}`}
                className="group block rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5 hover:bg-white/[0.04] hover:border-violet-500/20 transition-all duration-200">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-white text-base leading-snug group-hover:text-violet-300 transition-colors">
                      {t.name}
                    </h3>
                    <p className="text-slate-500 text-xs mt-0.5">by {t.creator_name}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-cyan-400/10 text-cyan-400 border border-cyan-400/20 font-medium">
                      {t.member_count} members
                    </span>
                  </div>
                </div>

                {t.description && (
                  <p className="text-slate-500 text-xs leading-relaxed mb-3 line-clamp-2">{t.description}</p>
                )}

                <div className="flex items-center justify-between text-[11px] text-slate-600">
                  <span>Created {timeAgo(t.created_at)}</span>
                  {t.project_count > 0 && (
                    <span className="text-violet-400/70 font-medium">{t.project_count} projects</span>
                  )}
                </div>
              </Link>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
