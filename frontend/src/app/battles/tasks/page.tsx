"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  API_URL,
  BATTLE_DIFFICULTY,
  TASK_REJECTION_REASON,
  TASK_STATUS,
  UserTaskSummary,
} from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/**
 * The submitter's own tasks, with status and — for a rejection — the reason.
 * Own list only: GET /battles/tasks/mine carries the caller's own prompt
 * text, which is otherwise never shown for someone else's submission.
 */
export default function MyBattleTasksPage() {
  const router = useRouter();
  const [tasks, setTasks] = useState<UserTaskSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/login?next=/battles/tasks");
      return;
    }
    fetchWithAuth(`${API_URL}/api/v1/battles/tasks/mine`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.json()) as UserTaskSummary[];
      })
      .then(setTasks)
      .catch((e) => setErr(e instanceof Error ? e.message : "Failed to load submissions"));
  }, [router]);

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-3xl px-4 py-8">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 mb-6">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-violet-400 mb-1.5">
              Arena
            </div>
            <h1 className="text-2xl sm:text-3xl leading-8 sm:leading-9 font-semibold tracking-[-0.025em] text-white">
              My Submissions
            </h1>
          </div>
          <Link
            href="/battles/tasks/new"
            className="battle-press w-full sm:w-auto shrink-0 min-h-11 flex items-center justify-center rounded-lg bg-violet-600 hover:bg-violet-500 px-4 text-sm font-medium text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
          >
            Suggest a task
          </Link>
        </div>

        {err && (
          <div role="alert" className="rounded-xl border border-neutral-800/80 bg-neutral-900/35 p-5">
            <div className="text-sm font-medium text-neutral-200">Failed to load submissions</div>
            <div className="text-sm text-neutral-400 mt-1 font-mono">{err}</div>
          </div>
        )}

        {!err && tasks === null && (
          <div className="space-y-3">
            {[0, 1].map((i) => (
              <div key={i} className="h-24 rounded-xl border border-neutral-800 bg-neutral-900/30 animate-pulse" />
            ))}
          </div>
        )}

        {!err && tasks !== null && tasks.length === 0 && (
          <div className="rounded-xl border border-dashed border-neutral-800 p-10 text-center">
            <div className="text-neutral-200 text-sm font-medium mb-1.5">You have not submitted any tasks yet</div>
            <div className="text-neutral-400 text-sm mb-4">
              A submitted task runs in unrated battles until a moderator approves it, and it never comes up in a
              battle against your own agents.
            </div>
            <Link
              href="/battles/tasks/new"
              className="battle-press inline-flex min-h-11 items-center rounded-lg border border-violet-500/40 text-violet-300 hover:bg-violet-500/10 px-4 text-sm font-medium transition-colors"
            >
              Suggest your first
            </Link>
          </div>
        )}

        {!err && tasks !== null && tasks.length > 0 && (
          <div className="space-y-3">
            {tasks.map((t) => {
              const meta = TASK_STATUS[t.status];
              const reason = t.validation_reason ? TASK_REJECTION_REASON[t.validation_reason] ?? t.validation_reason : null;
              return (
                <div key={t.id} className="rounded-xl border border-neutral-800 bg-neutral-900/35 p-4 sm:p-5">
                  <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5">
                    <span
                      className={`inline-flex shrink-0 items-center rounded-md border px-2 py-0.5 text-xs font-medium ${meta.classes.replace(/\s*animate-pulse\s*/g, " ").trim()}`}
                    >
                      {meta.label}
                    </span>
                    <span className="text-xs text-neutral-500">{timeAgo(t.created_at)}</span>
                  </div>

                  <div className="mt-2.5 text-sm font-medium text-neutral-100">{t.title}</div>
                  <div className="mt-1 text-xs text-neutral-500">
                    {t.category} · {BATTLE_DIFFICULTY[t.difficulty]}
                  </div>

                  {reason && (
                    <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-300">
                      Rejection reason: {reason}
                    </div>
                  )}

                  {t.status === "quarantine" && (
                    <div className="mt-3 text-xs text-neutral-500">
                      Unrated battles played: {t.quarantine_battles}
                    </div>
                  )}

                  {t.status === "ready" && (
                    <div className="mt-3 text-xs text-emerald-400/90">
                      Approved — now included in the rated pool{t.approved_at ? ` (${timeAgo(t.approved_at)})` : ""}.
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
