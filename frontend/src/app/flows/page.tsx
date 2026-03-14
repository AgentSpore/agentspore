"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, Flow, FLOW_STATUS, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

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
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Agent Flows</h1>
            <p className="text-neutral-500 text-sm mt-1">
              Build multi-agent pipelines to solve complex tasks
            </p>
          </div>
          <Link
            href="/flows/new"
            className="px-4 py-2 rounded-lg text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all"
          >
            + New Flow
          </Link>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2 flex-wrap">
          {["all", "draft", "running", "paused", "completed", "cancelled"].map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`text-xs px-3 py-1.5 rounded-lg border font-mono transition-all ${
                filter === s
                  ? "border-neutral-600 text-white bg-neutral-800"
                  : "border-neutral-800 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700"
              }`}
            >
              {s === "all" ? "All" : s.charAt(0).toUpperCase() + s.slice(1)}
              {s !== "all" && (
                <span className="ml-1 text-neutral-600">
                  {flows.filter((f) => f.status === s).length}
                </span>
              )}
            </button>
          ))}
        </div>

        {loading && <p className="text-neutral-600 text-sm">Loading flows...</p>}

        {!loading && flows.length === 0 && (
          <div className="rounded-xl border border-neutral-800 bg-neutral-900/50 p-10 text-center space-y-3">
            <div className="text-4xl opacity-20">◇</div>
            <p className="text-neutral-500 text-sm">
              No flows yet. Create your first multi-agent pipeline.
            </p>
            <Link
              href="/flows/new"
              className="inline-block mt-2 px-5 py-2 rounded-lg text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all"
            >
              Create Flow
            </Link>
          </div>
        )}

        {filtered.length > 0 && (
          <div className="space-y-2">
            {filtered.map((f) => {
              const st = FLOW_STATUS[f.status] || FLOW_STATUS.draft;
              const progress = f.step_count
                ? `${f.completed_step_count ?? 0}/${f.step_count}`
                : "0 steps";
              return (
                <Link
                  key={f.id}
                  href={`/flows/${f.id}`}
                  className="block rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4 hover:border-neutral-700 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="text-white font-medium text-sm truncate">{f.title}</div>
                      {f.description && (
                        <div className="text-neutral-600 text-xs mt-1 truncate">{f.description}</div>
                      )}
                      <div className="flex items-center gap-2 mt-2">
                        <span className="text-neutral-500 text-xs font-mono">{progress} steps</span>
                        <span className="text-neutral-700">·</span>
                        <span className="text-neutral-600 text-xs font-mono">{timeAgo(f.created_at)}</span>
                      </div>
                    </div>
                    <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono flex-shrink-0 ${st.classes}`}>
                      {st.label}
                    </span>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
