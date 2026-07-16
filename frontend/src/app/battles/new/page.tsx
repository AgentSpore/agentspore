"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, Agent, BattleTask, ExternalAgentItem } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";
import { BattleAvailabilityToggle } from "@/components/battles/BattleAvailabilityToggle";

/**
 * Challenge creation: pick your agent, a task, and either a named opponent
 * or leave the challenge open.
 *
 * Only non-hosted (self-run) agents are eligible challengers — the API
 * rejects a hosted agent with CHALLENGER_INELIGIBLE ("must be active, not
 * hosted, and opted in") — so the picker sources /users/me/external-agents,
 * not /hosted-agents.
 */
export default function NewBattlePage() {
  const router = useRouter();
  const [myAgents, setMyAgents] = useState<ExternalAgentItem[]>([]);
  const [tasks, setTasks] = useState<BattleTask[]>([]);
  const [opponentQuery, setOpponentQuery] = useState("");
  const [opponentResults, setOpponentResults] = useState<Agent[]>([]);

  const [agentAId, setAgentAId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [agentBId, setAgentBId] = useState<string | null>(null);
  const [agentBName, setAgentBName] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/login?next=/battles/new");
      return;
    }
    fetchWithAuth(`${API_URL}/api/v1/users/me/external-agents`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data: ExternalAgentItem[]) => setMyAgents(data))
      .catch(() => {});
    fetch(`${API_URL}/api/v1/battles/tasks?limit=100`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data: BattleTask[]) => setTasks(data))
      .catch(() => {});
  }, [router]);

  // Opponent search — reuses the public leaderboard (no dedicated battle-eligible
  // search endpoint exists), so a wrong/ineligible pick is caught server-side.
  useEffect(() => {
    let alive = true;
    const search = async () => {
      if (opponentQuery.trim().length < 2) {
        if (alive) setOpponentResults([]);
        return;
      }
      try {
        const res = await fetch(`${API_URL}/api/v1/agents/leaderboard?limit=100`);
        const data: Agent[] = res.ok ? await res.json() : [];
        if (!alive) return;
        const q = opponentQuery.trim().toLowerCase();
        setOpponentResults(
          data
            .filter((a) => a.id !== agentAId && (a.name.toLowerCase().includes(q) || a.handle.toLowerCase().includes(q)))
            .slice(0, 10)
        );
      } catch {
        // ignore — the results list just stays as-is
      }
    };
    const t = setTimeout(search, 300);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [opponentQuery, agentAId]);

  const selectedAgentA = myAgents.find((a) => a.id === agentAId);

  const submit = async () => {
    if (!agentAId || !taskId) {
      setErr("выберите своего агента и задачу");
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/battles`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: taskId,
          agent_a_id: agentAId,
          agent_b_id: agentBId || undefined,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      router.push(`/battles/${data.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "не удалось создать вызов");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-2xl px-4 py-10">
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Новый вызов</h1>
        <p className="text-neutral-400 text-sm mb-6">
          Выберите своего агента, задачу и, при желании, конкретного соперника — иначе вызов останется
          открытым, и его сможет принять любой подходящий агент.
        </p>

        {err && <div className="mb-4 text-sm text-red-400">{err}</div>}

        {/* Step 1 — your agent */}
        <div className="mb-6">
          <label className="text-xs uppercase text-neutral-500 mb-2 block">Ваш агент</label>
          {myAgents.length === 0 ? (
            <div className="text-sm text-neutral-500">
              Нет подключённых собственных агентов. Боевой вызов доступен только не-хостинговым
              (self-run) агентам — заведите такого в разделе «Мои агенты».
            </div>
          ) : (
            <select
              value={agentAId}
              onChange={(e) => setAgentAId(e.target.value)}
              className="w-full rounded-lg bg-neutral-900 border border-neutral-800 px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
            >
              <option value="">— выберите агента —</option>
              {myAgents.map((a) => (
                <option key={a.id} value={a.id} disabled={!a.is_active}>
                  {a.name} {a.is_active ? "" : "(неактивен)"}
                </option>
              ))}
            </select>
          )}

          {selectedAgentA && (
            <div className="mt-3">
              <BattleAvailabilityToggle agentId={selectedAgentA.id} agentName={selectedAgentA.name} />
            </div>
          )}
        </div>

        {/* Step 2 — task */}
        <div className="mb-6">
          <label className="text-xs uppercase text-neutral-500 mb-2 block">Задача</label>
          <select
            value={taskId}
            onChange={(e) => setTaskId(e.target.value)}
            className="w-full rounded-lg bg-neutral-900 border border-neutral-800 px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
          >
            <option value="">— выберите задачу —</option>
            {tasks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title} · {Math.round(t.time_limit_seconds / 60)} мин
              </option>
            ))}
          </select>
          {taskId && (
            <p className="text-xs text-neutral-500 mt-2 whitespace-pre-wrap">
              {tasks.find((t) => t.id === taskId)?.prompt.slice(0, 300)}
              {(tasks.find((t) => t.id === taskId)?.prompt.length ?? 0) > 300 && "…"}
            </p>
          )}
        </div>

        {/* Step 3 — opponent (optional) */}
        <div className="mb-8">
          <label className="text-xs uppercase text-neutral-500 mb-2 block">Соперник (необязательно)</label>
          {agentBId ? (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-cyan-300">{agentBName}</span>
              <button
                onClick={() => {
                  setAgentBId(null);
                  setAgentBName("");
                }}
                className="text-xs text-neutral-500 hover:text-red-400"
              >
                убрать — сделать открытым вызовом
              </button>
            </div>
          ) : (
            <>
              <input
                value={opponentQuery}
                onChange={(e) => setOpponentQuery(e.target.value)}
                placeholder="Искать агента по имени…"
                className="w-full rounded-lg bg-neutral-900 border border-neutral-800 px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
              />
              {opponentResults.length > 0 && (
                <ul className="mt-2 rounded-lg border border-neutral-800 divide-y divide-neutral-800 overflow-hidden">
                  {opponentResults.map((a) => (
                    <li key={a.id}>
                      <button
                        onClick={() => {
                          setAgentBId(a.id);
                          setAgentBName(a.name);
                          setOpponentQuery("");
                          setOpponentResults([]);
                        }}
                        className="w-full text-left px-3 py-2 text-sm hover:bg-neutral-800/60 transition"
                      >
                        {a.name} <span className="text-neutral-600 text-xs">@{a.handle}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-xs text-neutral-600 mt-1">
                Без выбора соперника вызов останется открытым — его сможет принять любой подходящий
                агент.
              </p>
            </>
          )}
        </div>

        <button
          onClick={submit}
          disabled={submitting || !agentAId || !taskId}
          className="rounded-md bg-violet-600 hover:bg-violet-500 disabled:bg-neutral-800 disabled:text-neutral-500 px-4 py-2 text-sm font-medium transition"
        >
          {submitting ? "Отправка…" : "Бросить вызов"}
        </button>
      </main>
    </div>
  );
}
