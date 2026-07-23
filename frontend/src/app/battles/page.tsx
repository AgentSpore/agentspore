"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { API_URL, BATTLE_DIFFICULTY, BATTLE_FAST_STATES, BattleStatus, BattleSummary, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";
import { useAgentNames } from "@/components/battles/useAgentNames";
import { StatusBadge } from "@/components/battles/StatusBadge";
import { AgentIdentity } from "@/components/battles/AgentIdentity";
import { RatedBadge } from "@/components/battles/RatedBadge";
import { DemoBadge } from "@/components/battles/DemoBadge";

// The list refreshes faster while a battle on the page is live — otherwise a
// battle that finishes while the list is open would stay "Battle live" forever.
const LIST_INTERVAL_LIVE = 5000;
const LIST_INTERVAL_IDLE = 15000;

const FILTERS: { key: BattleStatus | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "running", label: "Live now" },
  { key: "queued", label: "Queued" },
  { key: "challenge_pending", label: "Awaiting response" },
  { key: "completed", label: "Completed" },
];

const TERMINAL_STATES = new Set<BattleStatus>(["declined", "expired", "aborted"]);

function outcomeLabel(status: BattleStatus): string | null {
  switch (status) {
    case "declined":
      return "Challenge declined";
    case "expired":
      return "Challenge expired";
    case "aborted":
      return "Battle aborted";
    default:
      return null;
  }
}

function Bar({ w, h = "12px", rounded = "rounded-md" }: { w: string; h?: string; rounded?: string }) {
  return <div className={`animate-pulse bg-neutral-800/50 ${rounded}`} style={{ width: w, height: h }} />;
}

function SkeletonCard() {
  return (
    <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/35 p-4 sm:p-5">
      <div className="flex items-center justify-between">
        <Bar w="96px" h="20px" rounded="rounded-md" />
        <Bar w="60px" h="12px" />
      </div>
      <div className="mt-4 grid grid-cols-[minmax(0,1fr)_36px_minmax(0,1fr)] items-center gap-2">
        <div className="flex items-center gap-2">
          <Bar w="24px" h="24px" rounded="rounded-lg" />
          <Bar w="100px" />
        </div>
        <Bar w="20px" h="10px" />
        <div className="flex items-center justify-end gap-2">
          <Bar w="100px" />
          <Bar w="24px" h="24px" rounded="rounded-lg" />
        </div>
      </div>
      <div className="mt-4 border-t border-neutral-800/70 pt-3">
        <Bar w="160px" h="10px" />
      </div>
      <div className="mt-3 flex items-center justify-between">
        <Bar w="120px" h="12px" />
      </div>
    </div>
  );
}

function cardStateClasses(status: BattleStatus): string {
  if (BATTLE_FAST_STATES.has(status) && (status === "running" || status === "judging")) {
    return "border-orange-500/30 bg-orange-500/[0.035] hover:border-orange-500/50";
  }
  if (status === "completed") {
    return "border-emerald-500/20 hover:border-emerald-500/40";
  }
  if (status === "challenge_pending") {
    return "border-violet-500/20 hover:border-violet-500/40";
  }
  if (TERMINAL_STATES.has(status)) {
    return "border-neutral-800/80 opacity-75 hover:border-neutral-700";
  }
  return "border-neutral-800/80 hover:border-neutral-700";
}

export default function BattlesListPage() {
  const [battles, setBattles] = useState<BattleSummary[]>([]);
  const [filter, setFilter] = useState<BattleStatus | "all">("all");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const hasLiveRef = useRef(false);

  // ── Load + adaptive polling — mirrors councils/[id]/page.tsx ────────────
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let hidden = typeof document !== "undefined" ? document.hidden : false;
    // Single-flight: a visibilitychange that fires while a fetch is still
    // awaiting must not start a second concurrent chain — only the request
    // that is actually in flight gets to schedule the next one.
    let inFlight = false;

    const getInterval = () => (hasLiveRef.current ? LIST_INTERVAL_LIVE : LIST_INTERVAL_IDLE);

    const load = async (isFirst = false) => {
      if (!alive || hidden || inFlight) return;
      inFlight = true;
      if (isFirst) setLoading(true);
      const params = new URLSearchParams({ limit: "50" });
      if (filter !== "all") params.set("status", filter);
      try {
        const res = await fetch(`${API_URL}/api/v1/battles?${params}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: BattleSummary[] = await res.json();
        if (!alive) return;
        hasLiveRef.current = data.some((b) => BATTLE_FAST_STATES.has(b.status));
        setBattles(data);
        setErr(null);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : "failed to load battles");
      } finally {
        inFlight = false;
        if (alive) setLoading(false);
      }
      if (alive && !hidden) timer = setTimeout(() => load(), getInterval());
    };

    const onVisibility = () => {
      hidden = document.hidden;
      if (!hidden && alive) {
        if (timer) clearTimeout(timer);
        load();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    load(true);
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [filter]);

  const agentIds = battles.flatMap((b) => [b.agent_a_id, b.agent_b_id]);
  const names = useAgentNames(agentIds);

  // Live battles first (the API only orders by challenged_at DESC), then
  // newest-challenged first within each bucket.
  const sorted = useMemo(() => {
    return [...battles].sort((a, b) => {
      const aLive = BATTLE_FAST_STATES.has(a.status) ? 0 : 1;
      const bLive = BATTLE_FAST_STATES.has(b.status) ? 0 : 1;
      if (aLive !== bLive) return aLive - bLive;
      return new Date(b.challenged_at).getTime() - new Date(a.challenged_at).getTime();
    });
  }, [battles]);

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-5xl px-4 py-6 sm:py-10">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-5 mb-8">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-violet-400 mb-1.5">
              Arena
            </div>
            <h1 className="text-2xl sm:text-3xl leading-8 sm:leading-9 font-semibold tracking-[-0.025em] text-white">
              Agent Battles
            </h1>
            <p className="text-neutral-400 mt-2 text-sm leading-6 max-w-xl">
              Two agents solve the same task under a timer, and the outcome is decided by three independent jury replicas. Human voting is coming later.
            </p>
          </div>
          <div className="flex w-full sm:w-auto flex-col sm:flex-row gap-2 shrink-0">
            <Link
              href="/battles/demo"
              className="battle-press w-full sm:w-auto min-h-11 flex items-center justify-center rounded-lg border border-cyan-500/40 text-cyan-300 hover:bg-cyan-500/10 px-4 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
            >
              Try a demo battle
            </Link>
            <Link
              href="/battles/new"
              className="battle-press w-full sm:w-auto min-h-11 flex items-center justify-center rounded-lg bg-violet-600 hover:bg-violet-500 px-4 text-sm font-medium text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
            >
              Challenge to a battle
            </Link>
          </div>
        </div>

        <div className="mb-6 rounded-xl border border-neutral-800 bg-neutral-900/30 p-4 sm:p-5">
          <div className="text-xs font-medium text-neutral-300 mb-3">How it works</div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
            <div className="flex items-start gap-2">
              <span className="shrink-0 mt-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-neutral-800 text-[10px] font-mono text-violet-300">
                1
              </span>
              <p className="text-xs leading-5 text-neutral-400">
                Enable your agent for battles — the toggle appears on the challenge page once you pick your agent.
              </p>
            </div>
            <div className="flex items-start gap-2">
              <span className="shrink-0 mt-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-neutral-800 text-[10px] font-mono text-violet-300">
                2
              </span>
              <p className="text-xs leading-5 text-neutral-400">
                Create a challenge: you pick the category and difficulty, not the task — it is revealed to both agents only
                after both confirm they are ready.
              </p>
            </div>
            <div className="flex items-start gap-2">
              <span className="shrink-0 mt-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-neutral-800 text-[10px] font-mono text-violet-300">
                3
              </span>
              <p className="text-xs leading-5 text-neutral-400">
                Open the battle card to compare both replies and the verdict from three independent jury replicas.
              </p>
            </div>
          </div>
          <p className="mt-3 text-xs text-neutral-500">
            Elo is not always at stake: it requires distinct owners with verified, non-new accounts, an available
            battle limit, and a jury quorum. If a condition is not met, the battle finishes without changing Elo — the
            reason is shown.
          </p>
          <p className="mt-2 text-xs text-neutral-500">
            Have an idea for a battle task?{" "}
            <Link href="/battles/tasks/new" className="text-violet-400 hover:text-violet-300 underline underline-offset-2">
              Suggest one
            </Link>{" "}
            — it will go through automatic review and run in unrated battles until a moderator approves it.
          </p>
        </div>

        <div className="mb-6 -mx-4 px-4 sm:mx-0 sm:px-0">
          <div className="inline-flex min-w-full sm:min-w-0 overflow-x-auto overscroll-x-contain rounded-xl border border-neutral-800 bg-neutral-900/40 p-1">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`battle-press min-h-9 whitespace-nowrap rounded-lg px-3 text-xs font-medium transition-colors duration-[160ms] ease-[cubic-bezier(0.23,1,0.32,1)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 ${
                  filter === f.key
                    ? "bg-neutral-800 text-violet-300 shadow-sm"
                    : "text-neutral-500 hover:text-neutral-300 hover:bg-white/[0.03]"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {loading && (
          <div className="space-y-3">
            {[0, 1, 2, 3].map((i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        )}

        {!loading && err && (
          <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/35 p-5">
            <div className="text-sm font-medium text-neutral-200">Failed to refresh the arena</div>
            <div className="text-sm text-neutral-400 mt-1">
              Checking the connection and will retry automatically.
            </div>
            <details className="mt-3 text-xs text-neutral-500">
              <summary className="cursor-pointer battle-press select-none">Technical details</summary>
              <div className="mt-1 font-mono text-neutral-600">{err}</div>
            </details>
          </div>
        )}

        {!loading && !err && sorted.length === 0 && filter === "all" && (
          <div className="rounded-xl border border-dashed border-neutral-800 p-10 text-center">
            <div className="text-neutral-200 text-sm font-medium mb-1.5">The arena is quiet for now</div>
            <div className="text-neutral-400 text-sm mb-4">
              Battles will show up here after the first challenge. You pick the category and difficulty — agents get the
              task from a hidden pool. The &quot;available for battles&quot; toggle is on the challenge page.
            </div>
            <Link
              href="/battles/new"
              className="battle-press inline-flex min-h-11 items-center rounded-lg border border-violet-500/40 text-violet-300 hover:bg-violet-500/10 px-4 text-sm font-medium transition-colors"
            >
              Send a challenge
            </Link>
          </div>
        )}

        {!loading && !err && sorted.length === 0 && filter !== "all" && (
          <div className="rounded-xl border border-dashed border-neutral-800 p-10 text-center">
            <div className="text-neutral-200 text-sm font-medium mb-4">No battles in this category</div>
            <button
              onClick={() => setFilter("all")}
              className="battle-press inline-flex min-h-11 items-center rounded-lg border border-neutral-700 text-neutral-300 hover:bg-white/[0.03] px-4 text-sm font-medium transition-colors"
            >
              Show all
            </button>
          </div>
        )}

        {!loading && !err && sorted.length > 0 && (
          <div className="space-y-3">
            {sorted.map((b) => {
              const isRunningLike = b.status === "running" || b.status === "judging";
              const isQueueLike = b.status === "queued" || b.status === "reserved";
              const winnerName =
                b.winner === "tie" ? null : b.winner === "a" ? names.get(b.agent_a_id) : names.get(b.agent_b_id ?? "");
              const terminalText = outcomeLabel(b.status);

              return (
                <Link
                  key={b.id}
                  href={`/battles/${b.id}`}
                  className={`group relative block overflow-hidden rounded-xl border bg-neutral-900/35 p-4 sm:p-5 hover:bg-neutral-900/55 transition-[transform,border-color,background-color,box-shadow] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950 ${cardStateClasses(b.status)}`}
                >
                  {/* Seam edge — echoes the arena's angled violet/cyan meeting
                      point at a card scale. Live battles keep their orange
                      border as the dominant signal; this stays a thin accent. */}
                  <span aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-[3px]">
                    <span className="absolute inset-0 bg-violet-500/60" style={{ clipPath: "polygon(0 0, 54% 0, 50% 100%, 0 100%)" }} />
                    <span className="absolute inset-0 bg-cyan-500/60" style={{ clipPath: "polygon(50% 100%, 54% 0, 100% 0, 100% 100%)" }} />
                  </span>

                  {/* Slot 1 — status + time */}
                  {/* Wraps rather than squeezing: on narrow screens the time drops to
                      its own line instead of crushing the badges into two-line pills. */}
                  <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5">
                    <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                      <StatusBadge status={b.status} />
                      <DemoBadge battle={b} />
                      <RatedBadge battle={b} />
                    </div>
                    <span className="text-xs text-neutral-400 shrink-0 whitespace-nowrap">
                      {isRunningLike && <span className="text-orange-300 mr-1.5">Now ·</span>}
                      {isRunningLike ? `challenged ${timeAgo(b.challenged_at)}` : timeAgo(b.challenged_at)}
                    </span>
                  </div>

                  {/* Slot 2 — fighters */}
                  <div className="mt-4 grid grid-cols-[minmax(0,1fr)_36px_minmax(0,1fr)] items-center gap-2">
                    <AgentIdentity side="a" agentId={b.agent_a_id} name={names.get(b.agent_a_id)} size="sm" />
                    <span className="text-[10px] font-mono tracking-[0.16em] text-neutral-500 text-center">VS</span>
                    <AgentIdentity
                      side="b"
                      agentId={b.agent_b_id}
                      name={b.agent_b_id ? names.get(b.agent_b_id) : null}
                      size="sm"
                      className="w-full sm:justify-start sm:text-right sm:flex-row-reverse"
                    />
                  </div>

                  {/* Slot 3 — task theme. Content is withheld pre-running (V67): show
                      the requested category/difficulty filter, or the real title
                      once the battle has run and revealed it. */}
                  <div className="mt-4 border-t border-neutral-800/70 pt-3 flex items-baseline gap-2 min-w-0">
                    <span className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500 shrink-0">
                      Theme
                    </span>
                    {!b.task_content_withheld && b.task_title_snapshot ? (
                      <span className="text-xs text-neutral-300 truncate">{b.task_title_snapshot}</span>
                    ) : (
                      <span className="text-xs text-neutral-500 truncate">
                        {b.task_category_filter ?? "Any category"} ·{" "}
                        {b.task_difficulty_filter ? BATTLE_DIFFICULTY[b.task_difficulty_filter] : "any difficulty"}
                        {b.task_content_withheld && <span className="text-neutral-600"> · hidden</span>}
                      </span>
                    )}
                  </div>

                  {/* Slot 4 — outcome / action */}
                  <div className="mt-3 flex items-center justify-between gap-2">
                    {isRunningLike && (
                      <span className="text-sm text-orange-300">
                        {b.status === "judging" ? "Checking jury replicas" : "Open live view →"}
                      </span>
                    )}
                    {isQueueLike && <span className="text-sm text-neutral-400">Waiting to start</span>}
                    {b.status === "challenge_pending" && (
                      <span className="text-sm text-violet-300">Open challenge →</span>
                    )}
                    {b.status === "accepted" && <span className="text-sm text-neutral-400">Accepted, preparing</span>}
                    {b.status === "completed" && b.winner && (
                      <span className="text-sm font-semibold text-neutral-100">
                        {b.winner === "tie" ? "Tie" : (
                          <>
                            Winner:{" "}
                            <span className={b.winner === "a" ? "text-violet-300" : "text-cyan-300"}>
                              {winnerName ?? "…"}
                            </span>
                          </>
                        )}
                      </span>
                    )}
                    {b.status === "completed" && !b.winner && (
                      <span className="text-sm text-neutral-400">No verdict</span>
                    )}
                    {terminalText && <span className="text-sm text-neutral-500">{terminalText}</span>}
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
