"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, Flow, FLOW_STATUS, timeAgo } from "@/lib/api";
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

const statusColor: Record<string, string> = {
  draft: "bg-neutral-500/20 text-neutral-400 border-neutral-700/50",
  running: "bg-violet-500/10 text-violet-400 border-violet-500/30",
  paused: "bg-orange-500/10 text-orange-400 border-orange-500/30",
  completed: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  cancelled: "bg-red-500/10 text-red-400 border-red-500/30",
};

export default function FlowsPage() {
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("all");

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) { setLoading(false); return; }

    fetch(`${API_URL}/api/v1/flows`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((d: Flow[]) => { setFlows(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const filtered = filter === "all" ? flows : flows.filter((f) => f.status === filter);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <main className="relative z-10 max-w-3xl mx-auto px-6 py-10 space-y-8">
        {/* Page header */}
        <div className="fade-up" style={{ animationDelay: "0ms" }}>
          <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">
            Pipelines
          </span>
          <div className="flex items-center justify-between mt-3">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">Agent Flows</h1>
              <p className="text-neutral-500 text-sm mt-1">
                Build multi-agent pipelines to solve complex tasks
              </p>
            </div>
            <Link
              href="/flows/new"
              className="px-5 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all"
            >
              + New Flow
            </Link>
          </div>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2 flex-wrap fade-up" style={{ animationDelay: "60ms" }}>
          {["all", "draft", "running", "paused", "completed", "cancelled"].map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`text-xs px-3 py-1.5 rounded-lg border font-mono transition-all ${
                filter === s
                  ? "border-neutral-600 text-white bg-neutral-800/60"
                  : "border-neutral-800/50 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700/60"
              }`}
            >
              {s === "all" ? "All" : s.charAt(0).toUpperCase() + s.slice(1)}
              {s !== "all" && (
                <span className="ml-1.5 text-neutral-600">
                  {flows.filter((f) => f.status === s).length}
                </span>
              )}
            </button>
          ))}
        </div>

        {loading && (
          <p className="text-neutral-600 text-sm font-mono fade-up">Loading flows...</p>
        )}

        {!loading && flows.length === 0 && (
          <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-12 text-center space-y-4 fade-up" style={{ animationDelay: "120ms" }}>
            <div className="w-12 h-12 rounded-full bg-neutral-800/50 border border-neutral-700/50 flex items-center justify-center mx-auto">
              <span className="text-neutral-600 text-lg">+</span>
            </div>
            <div>
              <p className="text-neutral-400 text-sm">
                No flows yet
              </p>
              <p className="text-neutral-600 text-xs mt-1">
                Create your first multi-agent pipeline
              </p>
            </div>
            <Link
              href="/flows/new"
              className="inline-block mt-2 px-5 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all"
            >
              Create Flow
            </Link>
          </div>
        )}

        {filtered.length > 0 && (
          <div className="space-y-2">
            {filtered.map((f, i) => {
              const st = FLOW_STATUS[f.status] || FLOW_STATUS.draft;
              const progress = f.step_count
                ? `${f.completed_step_count ?? 0}/${f.step_count}`
                : "0 steps";
              const colorCls = statusColor[f.status] || statusColor.draft;
              return (
                <Link
                  key={f.id}
                  href={`/flows/${f.id}`}
                  className="block rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-5 hover:border-neutral-700/60 transition-all group fade-up"
                  style={{ animationDelay: `${120 + i * 40}ms` }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="text-white font-medium text-sm truncate group-hover:text-violet-300 transition-colors">
                        {f.title}
                      </div>
                      {f.description && (
                        <div className="text-neutral-600 text-xs mt-1 truncate">{f.description}</div>
                      )}
                      <div className="flex items-center gap-2 mt-2.5">
                        <span className="text-neutral-500 text-[11px] font-mono">{progress} steps</span>
                        <span className="text-neutral-700">·</span>
                        <span className="text-neutral-600 text-[11px] font-mono">{timeAgo(f.created_at)}</span>
                      </div>
                    </div>
                    <span className={`text-[10px] px-2.5 py-0.5 rounded-full border font-mono flex-shrink-0 ${colorCls}`}>
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
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .fade-up {
          opacity: 0;
          animation: fadeUp 0.5s ease-out forwards;
        }
      `}</style>
    </div>
  );
}
