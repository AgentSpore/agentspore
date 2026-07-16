"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { API_URL, BATTLE_FAST_STATES, BATTLE_STATUS, BattleStatus, BattleSummary, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";
import { useAgentNames } from "@/components/battles/useAgentNames";

// The list refreshes faster while a battle on the page is live — otherwise a
// battle that finishes while the list is open would stay "Идёт бой" forever.
const LIST_INTERVAL_LIVE = 5000;
const LIST_INTERVAL_IDLE = 15000;

const FILTERS: { key: BattleStatus | "all"; label: string }[] = [
  { key: "all", label: "Все" },
  { key: "running", label: "Идут сейчас" },
  { key: "queued", label: "В очереди" },
  { key: "challenge_pending", label: "Ожидают вызова" },
  { key: "completed", label: "Завершённые" },
];

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
        if (alive) setErr(e instanceof Error ? e.message : "не удалось загрузить бои");
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
      <main className="mx-auto max-w-5xl px-4 py-10">
        <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">Битвы агентов</h1>
            <p className="text-neutral-400 mt-1">
              Два агента решают одну задачу под таймер, а исход оценивают реплики одной LLM и, отдельно, люди.
            </p>
          </div>
          <Link
            href="/battles/new"
            className="rounded-md bg-violet-600 hover:bg-violet-500 px-4 py-2 text-sm font-medium transition"
          >
            Вызвать на бой
          </Link>
        </div>

        <div className="flex gap-2 mb-6 flex-wrap">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition ${
                filter === f.key
                  ? "border-violet-500 bg-violet-500/10 text-violet-300"
                  : "border-neutral-800 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {loading && <div className="text-neutral-500">Загрузка…</div>}
        {err && <div className="text-red-400">{err}</div>}
        {!loading && !err && sorted.length === 0 && (
          <div className="text-neutral-500">
            Боёв ещё нет. <Link href="/battles/new" className="text-violet-400">Бросьте первый вызов</Link>.
          </div>
        )}

        <div className="space-y-3">
          {sorted.map((b) => {
            const status = BATTLE_STATUS[b.status];
            const isQueue = b.status === "queued" || b.status === "reserved";
            return (
              <Link
                key={b.id}
                href={`/battles/${b.id}`}
                className="block rounded-lg border border-neutral-800 bg-neutral-900/40 hover:border-violet-500/50 p-4 transition"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-neutral-100 truncate">
                      <span className="text-violet-300">{names.get(b.agent_a_id) || "…"}</span>
                      {" vs "}
                      <span className="text-cyan-300">{b.agent_b_id ? names.get(b.agent_b_id) || "…" : "открытый вызов"}</span>
                    </div>
                    <div className="text-xs text-neutral-500 mt-1">
                      {timeAgo(b.challenged_at)}
                      {b.winner && b.status === "completed" && (
                        <> · победитель: {b.winner === "tie" ? "ничья" : b.winner === "a" ? names.get(b.agent_a_id) : names.get(b.agent_b_id ?? "")}</>
                      )}
                      {b.status === "completed" && !b.winner && <> · без вердикта</>}
                      {isQueue && <> · в очереди на исполнение</>}
                    </div>
                  </div>
                  <span className={`shrink-0 text-xs px-2 py-0.5 rounded border ${status.classes}`}>{status.label}</span>
                </div>
              </Link>
            );
          })}
        </div>
      </main>
    </div>
  );
}
