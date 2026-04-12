"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";

type CouncilSummary = {
  id: string;
  topic: string;
  status: string;
  mode: string;
  panel_size: number;
  current_round: number;
  max_rounds: number;
  consensus_score: number | null;
  created_at: string;
  ended_at: string | null;
};

function statusClass(s: string): string {
  switch (s) {
    case "done": return "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
    case "aborted": return "bg-red-500/10 text-red-400 border-red-500/30";
    case "round":
    case "voting":
    case "synthesizing":
    case "briefing": return "bg-violet-500/10 text-violet-300 border-violet-500/30 animate-pulse";
    default: return "bg-neutral-500/10 text-neutral-400 border-neutral-500/30";
  }
}

function scoreLabel(s: number | null): string {
  if (s === null) return "—";
  if (s > 0.5) return `strong approve (${s.toFixed(2)})`;
  if (s > 0) return `lean approve (${s.toFixed(2)})`;
  if (s === 0) return "split (0.00)";
  if (s > -0.5) return `lean reject (${s.toFixed(2)})`;
  return `strong reject (${s.toFixed(2)})`;
}

export default function CouncilsListPage() {
  const router = useRouter();
  const [councils, setCouncils] = useState<CouncilSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/login?next=/councils");
      return;
    }
    let alive = true;
    const load = async () => {
      try {
        const res = await fetchWithAuth(`${API_URL}/api/v1/councils?limit=30`);
        if (res.status === 401) {
          router.replace("/login?next=/councils");
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (alive) setCouncils(data);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : "failed");
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [router]);

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-5xl px-4 py-10">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">My Councils</h1>
            <p className="text-neutral-400 mt-1">Convene an ad-hoc panel of free models to debate a question.</p>
          </div>
          <Link
            href="/councils/new"
            className="rounded-md bg-violet-600 hover:bg-violet-500 px-4 py-2 text-sm font-medium transition"
          >
            Convene council
          </Link>
        </div>

        {loading && <div className="text-neutral-500">Loading...</div>}
        {err && <div className="text-red-400">{err}</div>}
        {!loading && councils.length === 0 && (
          <div className="text-neutral-500">
            No councils yet. <Link href="/councils/new" className="text-violet-400">Convene the first one</Link>.
          </div>
        )}

        <div className="space-y-3">
          {councils.map(c => (
            <Link
              key={c.id}
              href={`/councils/${c.id}`}
              className="block rounded-lg border border-neutral-800 bg-neutral-900/40 hover:border-violet-500/50 p-4 transition"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-neutral-100 truncate">{c.topic}</div>
                  <div className="text-xs text-neutral-500 mt-1">
                    {c.mode} · {c.panel_size} panelists · round {c.current_round}/{c.max_rounds}
                    {c.consensus_score !== null && <> · {scoreLabel(c.consensus_score)}</>}
                  </div>
                </div>
                <span className={`shrink-0 text-xs px-2 py-0.5 rounded border ${statusClass(c.status)}`}>
                  {c.status}
                </span>
              </div>
            </Link>
          ))}
        </div>
      </main>
    </div>
  );
}
