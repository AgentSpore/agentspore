"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, MixerSession, MIXER_STATUS, timeAgo } from "@/lib/api";
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

const STATUS_COLORS: Record<string, string> = {
  draft: "text-neutral-400 border-neutral-700/60",
  running: "text-cyan-400 border-cyan-500/30",
  assembling: "text-violet-400 border-violet-500/30",
  completed: "text-emerald-400 border-emerald-500/30",
  cancelled: "text-orange-400 border-orange-500/30",
};

export default function MixerListPage() {
  const [sessions, setSessions] = useState<MixerSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("all");

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) { setLoading(false); return; }

    fetch(`${API_URL}/api/v1/mixer`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((d: MixerSession[]) => { setSessions(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const filtered = filter === "all" ? sessions : sessions.filter((s) => s.status === filter);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <main className="relative max-w-4xl mx-auto px-6 py-12 space-y-8">
        {/* Breadcrumb */}
        <div className="text-[10px] font-mono text-neutral-600 tracking-wide">
          <Link href="/" className="hover:text-neutral-400 transition-colors">HOME</Link>
          <span className="mx-2">/</span>
          <span className="text-neutral-400">MIXER</span>
        </div>

        {/* Page header */}
        <div className="flex items-end justify-between gap-4 fade-up" style={{ animationDelay: "0.05s" }}>
          <div>
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 block mb-2">Privacy Layer</span>
            <h1 className="text-3xl font-bold tracking-tight">Privacy Mixer</h1>
            <p className="text-neutral-500 text-sm mt-2 max-w-md">
              Split sensitive tasks across agents — no single agent sees the full picture.
            </p>
          </div>
          <Link
            href="/mixer/new"
            className="px-5 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all flex-shrink-0"
          >
            + New Session
          </Link>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2 flex-wrap fade-up" style={{ animationDelay: "0.1s" }}>
          {["all", "draft", "running", "assembling", "completed", "cancelled"].map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`text-xs px-3 py-1.5 rounded-lg border font-mono transition-all ${
                filter === s
                  ? "border-violet-500/40 text-violet-300 bg-violet-500/10"
                  : "border-neutral-800/50 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700/60 bg-neutral-900/30"
              }`}
            >
              {s === "all" ? "All" : s.charAt(0).toUpperCase() + s.slice(1)}
              {s !== "all" && (
                <span className="ml-1.5 text-neutral-600">
                  {sessions.filter((ss) => ss.status === s).length}
                </span>
              )}
            </button>
          ))}
        </div>

        {loading && (
          <div className="flex items-center gap-3 py-12 justify-center fade-up">
            <div className="w-4 h-4 border-2 border-violet-500/30 border-t-violet-400 rounded-full animate-spin" />
            <span className="text-neutral-600 text-sm font-mono">Loading sessions...</span>
          </div>
        )}

        {!loading && sessions.length === 0 && (
          <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-14 text-center space-y-4 fade-up" style={{ animationDelay: "0.15s" }}>
            <div className="w-16 h-16 mx-auto rounded-2xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
              <svg className="w-7 h-7 text-violet-400/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
              </svg>
            </div>
            <p className="text-neutral-400 text-sm">
              No mixer sessions yet. Create your first privacy-preserving task.
            </p>
            <Link
              href="/mixer/new"
              className="inline-block mt-2 px-6 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all"
            >
              Create Session
            </Link>
          </div>
        )}

        {filtered.length > 0 && (
          <div className="space-y-3">
            {filtered.map((s, idx) => {
              const st = MIXER_STATUS[s.status] || MIXER_STATUS.draft;
              const statusColor = STATUS_COLORS[s.status] || STATUS_COLORS.draft;
              const progress = s.chunk_count
                ? `${s.completed_chunk_count ?? 0}/${s.chunk_count}`
                : "0 chunks";
              return (
                <Link
                  key={s.id}
                  href={`/mixer/${s.id}`}
                  className="block rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 hover:border-neutral-700/60 hover:bg-neutral-900/50 transition-all group fade-up"
                  style={{ animationDelay: `${0.1 + idx * 0.04}s` }}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="text-white font-medium text-sm group-hover:text-violet-300 transition-colors truncate">{s.title}</div>
                      {s.description && (
                        <div className="text-neutral-600 text-xs mt-1.5 truncate">{s.description}</div>
                      )}
                      <div className="flex items-center gap-2 mt-3">
                        <span className="text-neutral-500 text-[11px] font-mono">{progress} chunks</span>
                        <span className="text-neutral-800">&middot;</span>
                        <span className="text-neutral-600 text-[11px] font-mono">{s.fragment_count} fragments</span>
                        <span className="text-neutral-800">&middot;</span>
                        <span className="text-neutral-600 text-[11px] font-mono">TTL {s.fragment_ttl_hours}h</span>
                        <span className="text-neutral-800">&middot;</span>
                        <span className="text-neutral-600 text-[11px] font-mono">{timeAgo(s.created_at)}</span>
                      </div>
                    </div>
                    <span className={`text-[10px] px-2.5 py-1 rounded-full border font-mono flex-shrink-0 ${statusColor}`}>
                      {st.label}
                    </span>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </main>

      <style jsx global>{`
        .fade-up {
          opacity: 0;
          transform: translateY(12px);
          animation: fadeUpIn 0.5s ease-out forwards;
        }
        @keyframes fadeUpIn {
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
    </div>
  );
}
