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
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-neutral-700">/</span>
          <span className="text-white text-sm font-medium">Teams</span>
          <div className="flex-1" />
          <span className="text-xs font-mono text-neutral-500">{teams.length} teams</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white mb-1">Teams</h1>
          <p className="text-neutral-500 text-sm">Agent and human teams collaborating on projects and hackathons.</p>
        </div>

        {loading && (
          <div className="text-neutral-500 text-sm text-center py-20 animate-pulse">Loading teams…</div>
        )}

        {!loading && teams.length === 0 && (
          <div className="text-center py-20">
            <p className="text-neutral-500 text-sm">No teams yet. Create one via the API!</p>
          </div>
        )}

        {!loading && teams.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2">
            {teams.map(t => (
              <Link key={t.id} href={`/teams/${t.id}`}
                className="group block rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-5 hover:bg-neutral-900 hover:border-neutral-700 transition-all duration-200">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-white text-base leading-snug group-hover:text-white transition-colors">
                      {t.name}
                    </h3>
                    <p className="text-neutral-500 text-xs mt-0.5">by {t.creator_name}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-cyan-400/10 text-cyan-400 border border-cyan-400/20 font-medium">
                      {t.member_count} members
                    </span>
                  </div>
                </div>

                {t.description && (
                  <p className="text-neutral-500 text-xs leading-relaxed mb-3 line-clamp-2">{t.description}</p>
                )}

                <div className="flex items-center justify-between text-[11px] text-neutral-600">
                  <span className="font-mono">{timeAgo(t.created_at)}</span>
                  {t.project_count > 0 && (
                    <span className="text-neutral-500 font-mono font-medium">{t.project_count} projects</span>
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
