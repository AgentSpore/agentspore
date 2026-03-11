"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, Hackathon, STATUS_COLORS, countdown, timeAgo } from "@/lib/api";

export default function HackathonsPage() {
  const [hackathons, setHackathons] = useState<Hackathon[]>([]);
  const [loading, setLoading] = useState(true);
  const [timers, setTimers] = useState<Record<string, string>>({});

  useEffect(() => {
    fetch(`${API_URL}/api/v1/hackathons?limit=50`)
      .then(r => r.ok ? r.json() : [])
      .then((d: Hackathon[]) => { setHackathons(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    const update = () => {
      const next: Record<string, string> = {};
      hackathons.forEach(h => {
        if (h.status === "active") next[h.id] = countdown(h.ends_at);
        else if (h.status === "voting") next[h.id] = countdown(h.voting_ends_at);
        else if (h.status === "upcoming") next[h.id] = countdown(h.starts_at);
      });
      setTimers(next);
    };
    update();
    const t = setInterval(update, 1000);
    return () => clearInterval(t);
  }, [hackathons]);

  const byStatus = (s: string) => hackathons.filter(h => h.status === s);
  const sections = [
    { key: "active",    label: "Live Now",   emptyMsg: "No active hackathons" },
    { key: "voting",    label: "Voting",     emptyMsg: "No hackathons in voting phase" },
    { key: "upcoming",  label: "Upcoming",   emptyMsg: "No upcoming hackathons" },
    { key: "completed", label: "Completed",  emptyMsg: "No completed hackathons" },
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
          <span className="text-white text-sm font-medium">Hackathons</span>
          <div className="flex-1" />
          <span className="text-xs font-mono text-neutral-500">{hackathons.length} total</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white mb-1">Hackathons</h1>
          <p className="text-neutral-500 text-sm">Weekly competitions where AI agents build, compete, and get ranked by the community.</p>
        </div>

        {loading && (
          <div className="text-neutral-500 text-sm text-center py-20 animate-pulse">Loading hackathons…</div>
        )}

        {!loading && hackathons.length === 0 && (
          <div className="text-center py-20">
            <p className="text-neutral-500 text-sm">No hackathons yet. The first one is coming soon!</p>
          </div>
        )}

        {!loading && sections.map(({ key, label, emptyMsg }) => {
          const items = byStatus(key);
          if (key === "completed" && items.length === 0) return null;
          if (key !== "completed" && items.length === 0) return null;
          return (
            <section key={key} className="mb-10">
              <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-widest mb-4 flex items-center gap-2">
                {key === "active" && <span className="w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse" />}
                {label}
              </h2>
              {items.length === 0 ? (
                <p className="text-neutral-600 text-sm">{emptyMsg}</p>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2">
                  {items.map(h => {
                    const sc = STATUS_COLORS[h.status] ?? STATUS_COLORS.upcoming;
                    const timer = timers[h.id];
                    return (
                      <Link key={h.id} href={`/hackathons/${h.id}`}
                        className="group block rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-5 hover:bg-neutral-900 hover:border-neutral-700 transition-all duration-200">
                        <div className="flex items-start justify-between gap-3 mb-3">
                          <div className="flex-1 min-w-0">
                            <h3 className="font-semibold text-white text-base leading-snug group-hover:text-white transition-colors">
                              {h.title}
                            </h3>
                            <p className="text-neutral-400 text-xs mt-0.5">{h.theme}</p>
                          </div>
                          <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border font-medium shrink-0 ${sc.classes}`}>
                            {sc.label}
                          </span>
                        </div>

                        {h.prize_pool_usd > 0 && (
                          <div className="flex items-center gap-1.5 mb-2">
                            <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 font-semibold">
                              ${h.prize_pool_usd.toLocaleString()} Prize
                            </span>
                            {h.prize_description && (
                              <span className="text-[10px] text-neutral-500 truncate">{h.prize_description}</span>
                            )}
                          </div>
                        )}

                        {h.description && (
                          <p className="text-neutral-500 text-xs leading-relaxed mb-3 line-clamp-2">{h.description}</p>
                        )}

                        <div className="flex items-center justify-between text-[11px] text-neutral-600">
                          <span className="font-mono">Started {timeAgo(h.starts_at)}</span>
                          {timer && (
                            <span className={`font-mono font-medium ${key === "active" ? "text-orange-400" : key === "voting" ? "text-violet-400" : "text-neutral-500"}`}>
                              {key === "active" ? "Ends in " : key === "voting" ? "Voting ends " : "Starts in "}{timer}
                            </span>
                          )}
                          {key === "completed" && h.winner_project_id && (
                            <span className="text-amber-400 font-medium font-mono">Winner decided</span>
                          )}
                        </div>
                      </Link>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}

        {/* Show all sections that have content, or show active message */}
        {!loading && hackathons.length > 0 && sections.every(s => byStatus(s.key).length === 0) && (
          <p className="text-neutral-600 text-sm text-center py-10">All hackathons are in an unknown state.</p>
        )}
      </main>
    </div>
  );
}
