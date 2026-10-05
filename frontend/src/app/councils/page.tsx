"use client";

import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { communicationMessages } from "@/lib/i18n/communication";

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
  if (s > 0.5) return "strong approve";
  if (s > 0) return "lean approve";
  if (s === 0) return "split";
  if (s > -0.5) return "lean reject";
  return "strong reject";
}

export default function CouncilsListPage() {
  const tr = useTranslations(communicationMessages);
  const { locale } = useLocale();
  const display = (value: string) => Object.hasOwn(communicationMessages.en, value) ? tr(value) : value;

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
            <h1 className="text-3xl font-semibold tracking-tight">{tr("My Councils")}</h1>
            <p className="text-neutral-400 mt-1">{tr("Convene an ad-hoc panel of free models to debate a question.")}</p>
          </div>
          <Link
            href="/councils/new"
            className="rounded-md bg-violet-600 hover:bg-violet-500 px-4 py-2 text-sm font-medium transition"
          >
            {" " + tr("Convene council") + " "}</Link>
        </div>

        {loading && <div className="text-neutral-500">{tr("Loading...")}</div>}
        {err && <div className="text-red-400">{display(err)}</div>}
        {!loading && councils.length === 0 && (
          <div className="text-neutral-500">
            {" " + tr("No councils yet.") + " "}<Link href="/councils/new" className="text-violet-400">{tr("Convene the first one")}</Link>.
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
                    {display(c.mode)} · {c.panel_size.toLocaleString(locale)} {" " + tr("panelists · round") + " "}{c.current_round.toLocaleString(locale)}/{c.max_rounds.toLocaleString(locale)}
                    {c.consensus_score !== null && <> · {display(scoreLabel(c.consensus_score))} ({c.consensus_score.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })})</>}
                  </div>
                </div>
                <span className={`shrink-0 text-xs px-2 py-0.5 rounded border ${statusClass(c.status)}`}>
                  {display(c.status)}
                </span>
              </div>
            </Link>
          ))}
        </div>
      </main>
    </div>
  );
}
