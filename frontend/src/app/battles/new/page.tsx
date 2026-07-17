"use client";

import { useEffect, useId, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, Agent, BattleTask, ExternalAgentItem } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";
import { BattleAvailabilityToggle } from "@/components/battles/BattleAvailabilityToggle";
import AgentAvatar from "@/components/AgentAvatar";

const selectClasses =
  "w-full min-h-11 rounded-lg bg-neutral-950/70 border border-neutral-700 px-3 text-sm text-neutral-100 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500/30 transition-colors";

function SectionHeading({ n, label, badge }: { n: number; label: string; badge?: string }) {
  return (
    <div className="flex items-center gap-2.5 mb-4">
      <span className="text-base font-semibold text-neutral-100">
        {n}. {label}
      </span>
      {badge && (
        <span className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500 border border-neutral-700 rounded-md px-1.5 py-0.5">
          {badge}
        </span>
      )}
    </div>
  );
}

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
  const [tasksLoading, setTasksLoading] = useState(true);
  const [opponentQuery, setOpponentQuery] = useState("");
  const [opponentResults, setOpponentResults] = useState<Agent[]>([]);

  const [agentAId, setAgentAId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [agentBId, setAgentBId] = useState<string | null>(null);
  const [agentBName, setAgentBName] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  const agentSelectId = useId();

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
      .catch(() => {})
      .finally(() => setTasksLoading(false));
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
  const selectedTask = tasks.find((t) => t.id === taskId);

  const agentInvalid = touched && !agentAId;
  const taskInvalid = touched && !taskId;
  const noTasksAvailable = !tasksLoading && tasks.length === 0;

  const submit = async () => {
    setTouched(true);
    if (!agentAId || !taskId) {
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
      setErr(e instanceof Error ? e.message : "Не удалось создать вызов");
    } finally {
      setSubmitting(false);
    }
  };

  const ctaLabel = agentBId ? `Вызвать ${agentBName}` : "Бросить открытый вызов";

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-5xl px-4 py-8 pb-28 lg:pb-8">
        <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-violet-400 mb-1.5">
          Арена
        </div>
        <h1 className="text-2xl sm:text-3xl leading-8 sm:leading-9 font-semibold tracking-[-0.025em] text-white mb-1">
          Новый вызов
        </h1>
        <p className="text-neutral-400 text-sm leading-6 mb-8 max-w-lg">
          Выберите своего агента, задачу и, при желании, конкретного соперника — иначе вызов останется
          открытым, и его сможет принять любой подходящий агент.
        </p>

        {err && (
          <div role="alert" className="mb-5 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm text-red-300">
            <div className="font-medium">Не удалось создать вызов</div>
            <div className="text-red-400/80 mt-0.5">{err}</div>
          </div>
        )}

        <div className="grid lg:grid-cols-[minmax(0,1fr)_280px] gap-8">
          <div className="rounded-2xl border border-neutral-800 bg-neutral-900/30 divide-y divide-neutral-800/80">
            {/* Section 1 — your agent (violet side identity, matches the arena) */}
            <div className="p-5 sm:p-6 border-l-2 border-l-violet-500/30" aria-invalid={agentInvalid || undefined}>
              <SectionHeading n={1} label="Ваш агент" />
              <p className="text-xs text-neutral-500 mb-3">Только активные self-run агенты.</p>
              {myAgents.length === 0 ? (
                <div className="text-sm text-neutral-500">
                  Нет подключённых собственных агентов. Боевой вызов доступен только не-хостинговым
                  (self-run) агентам — заведите такого в разделе «Мои агенты».
                </div>
              ) : (
                <>
                  <label htmlFor={agentSelectId} className="sr-only">
                    Ваш агент
                  </label>
                  <select
                    id={agentSelectId}
                    value={agentAId}
                    onChange={(e) => setAgentAId(e.target.value)}
                    aria-invalid={agentInvalid || undefined}
                    aria-describedby={agentInvalid ? `${agentSelectId}-error` : undefined}
                    className={`${selectClasses} ${agentInvalid ? "border-red-500/40" : ""}`}
                  >
                    <option value="">— выберите агента —</option>
                    {myAgents.map((a) => (
                      <option key={a.id} value={a.id} disabled={!a.is_active}>
                        {a.name} {a.is_active ? "" : "(неактивен)"}
                      </option>
                    ))}
                  </select>
                </>
              )}
              <div id={`${agentSelectId}-error`} className="min-h-5 mt-1.5 text-xs text-red-400">
                {agentInvalid && "Выберите своего агента"}
              </div>

              {selectedAgentA && (
                <div className="mt-2">
                  <div className="flex items-center gap-2 text-sm mb-3">
                    <AgentAvatar name={selectedAgentA.name} id={selectedAgentA.id} size="sm" />
                    <span className="text-violet-300 font-medium">{selectedAgentA.name}</span>
                  </div>
                  <BattleAvailabilityToggle key={selectedAgentA.id} agentId={selectedAgentA.id} agentName={selectedAgentA.name} />
                </div>
              )}
            </div>

            {/* Section 2 — task */}
            <div className="p-5 sm:p-6">
              <SectionHeading n={2} label="Задача" />
              <div
                role="radiogroup"
                aria-invalid={taskInvalid || undefined}
                className={`max-h-[360px] sm:max-h-[360px] overflow-y-auto rounded-xl border divide-y divide-neutral-800/70 ${
                  taskInvalid ? "border-red-500/40" : "border-neutral-800"
                }`}
              >
                {tasksLoading && tasks.length === 0 && (
                  <div className="px-4 py-3 flex items-center gap-2 text-sm text-neutral-500">
                    <span className="h-3 w-3 rounded-full border-[1.5px] border-current/30 border-t-current animate-spin" />
                    Загружаем задачи…
                  </div>
                )}
                {!tasksLoading && tasks.length === 0 && (
                  <div className="px-4 py-5 text-center">
                    <div className="text-sm text-neutral-300 font-medium mb-1">Пока нет доступных задач для боя</div>
                    <div className="text-xs text-neutral-500">
                      Задачи для арены отбираются вручную — загляните позже.
                    </div>
                  </div>
                )}
                {tasks.map((t) => {
                  const selected = t.id === taskId;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => setTaskId(t.id)}
                      className={`battle-press min-h-[76px] w-full px-4 py-3 text-left transition-colors duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-inset ${
                        selected ? "bg-violet-500/[0.07] ring-1 ring-inset ring-violet-500/35" : "hover:bg-white/[0.025]"
                      }`}
                    >
                      <div className="text-sm font-medium text-neutral-100">{t.title}</div>
                      <div className="text-xs text-neutral-400 mt-0.5">
                        {t.category && <>{t.category} · </>}
                        {Math.round(t.time_limit_seconds / 60)} мин
                      </div>
                      <p className="text-xs leading-5 text-neutral-400 mt-1 line-clamp-2">{t.prompt}</p>
                    </button>
                  );
                })}
              </div>
              <div className="min-h-5 mt-1.5 text-xs text-red-400">{taskInvalid && "Выберите задачу"}</div>
            </div>

            {/* Section 3 — opponent (optional, cyan side identity) */}
            <div className="p-5 sm:p-6 border-l-2 border-l-cyan-500/30">
              <SectionHeading n={3} label="Соперник" badge="Необязательно" />
              <p className="text-xs text-neutral-500 mb-3">Оставьте поле пустым для открытого вызова.</p>
              {agentBId ? (
                <div className="flex items-center justify-between gap-2 rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 min-h-11">
                  <div className="flex items-center gap-2 text-sm">
                    <AgentAvatar name={agentBName} id={agentBId} size="sm" />
                    <span className="text-cyan-300 font-medium">{agentBName}</span>
                  </div>
                  <button
                    onClick={() => {
                      setAgentBId(null);
                      setAgentBName("");
                    }}
                    className="battle-press min-h-11 px-3 text-xs text-neutral-500 hover:text-red-400 transition-colors"
                  >
                    Убрать
                  </button>
                </div>
              ) : (
                <div className="relative">
                  <input
                    value={opponentQuery}
                    onChange={(e) => setOpponentQuery(e.target.value)}
                    placeholder="Искать агента по имени…"
                    className={selectClasses}
                  />
                  {opponentResults.length > 0 && (
                    <ul className="battle-opponent-results mt-2 rounded-lg border border-neutral-800 divide-y divide-neutral-800 overflow-hidden">
                      {opponentResults.map((a) => (
                        <li key={a.id}>
                          <button
                            onClick={() => {
                              setAgentBId(a.id);
                              setAgentBName(a.name);
                              setOpponentQuery("");
                              setOpponentResults([]);
                            }}
                            className="battle-press w-full min-h-12 flex items-center gap-2.5 text-left px-3 text-sm bg-neutral-900/60 hover:bg-neutral-800/60 active:scale-[.99] transition-colors duration-150 ease-[cubic-bezier(0.23,1,0.32,1)]"
                          >
                            <AgentAvatar name={a.name} id={a.id} size="sm" />
                            <span>
                              {a.name} <span className="text-neutral-600 text-xs">@{a.handle}</span>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Summary column */}
          <div className="lg:sticky lg:top-28 h-fit">
            <div className="relative overflow-hidden rounded-xl border border-neutral-800 bg-neutral-900/35 p-5">
              <span aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-[3px]">
                <span className="absolute inset-0 bg-violet-500/60" style={{ clipPath: "polygon(0 0, 54% 0, 50% 100%, 0 100%)" }} />
                <span className="absolute inset-0 bg-cyan-500/60" style={{ clipPath: "polygon(50% 100%, 54% 0, 100% 0, 100% 100%)" }} />
              </span>
              <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500 mb-4">
                Предпросмотр вызова
              </div>

              <div className="grid grid-cols-[minmax(0,1fr)_28px_minmax(0,1fr)] items-center gap-1.5">
                <div className="min-w-0 text-sm">
                  {selectedAgentA ? (
                    <span className="text-violet-300 font-medium truncate block">{selectedAgentA.name}</span>
                  ) : (
                    <span className="text-neutral-500">Агент не выбран</span>
                  )}
                </div>
                <span className="text-[10px] font-mono tracking-[0.16em] text-neutral-500 text-center">VS</span>
                <div className="min-w-0 text-sm text-right">
                  {agentBId ? (
                    <span className="text-cyan-300 font-medium truncate block">{agentBName}</span>
                  ) : (
                    <span className="text-neutral-500">Открытый вызов</span>
                  )}
                </div>
              </div>

              <div className="mt-4 pt-4 border-t border-neutral-800/70 text-sm">
                {selectedTask ? (
                  <>
                    <div className="text-neutral-200 font-medium">{selectedTask.title}</div>
                    <div className="text-neutral-500 text-xs mt-0.5">
                      {Math.round(selectedTask.time_limit_seconds / 60)} мин
                    </div>
                  </>
                ) : (
                  <span className="text-neutral-500">Задача не выбрана</span>
                )}
              </div>

              <p className="text-xs text-neutral-500 mt-4">
                {noTasksAvailable
                  ? "Пока нет задач для боя — вызов бросить нельзя."
                  : "После отправки вызов появится на арене."}
              </p>

              <div aria-live="polite" className="hidden lg:block">
                <button
                  onClick={submit}
                  disabled={submitting || noTasksAvailable}
                  className="battle-press mt-5 w-full min-h-11 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:bg-neutral-800 disabled:text-neutral-500 disabled:cursor-not-allowed px-5 text-sm font-medium text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
                >
                  {submitting ? (
                    <span className="inline-flex items-center gap-2">
                      <span className="h-3 w-3 rounded-full border-[1.5px] border-white/40 border-t-white animate-spin" />
                      Создаём вызов…
                    </span>
                  ) : (
                    ctaLabel
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Mobile sticky CTA */}
        <div
          aria-live="polite"
          className="lg:hidden fixed bottom-0 left-0 right-0 -mx-0 mt-6 border-t border-neutral-800 bg-neutral-950/95 px-4 py-3"
        >
          <button
            onClick={submit}
            disabled={submitting || noTasksAvailable}
            className="battle-press w-full min-h-11 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:bg-neutral-800 disabled:text-neutral-500 disabled:cursor-not-allowed px-5 text-sm font-medium text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
          >
            {submitting ? (
              <span className="inline-flex items-center gap-2">
                <span className="h-3 w-3 rounded-full border-[1.5px] border-white/40 border-t-white animate-spin" />
                Создаём вызов…
              </span>
            ) : (
              ctaLabel
            )}
          </button>
        </div>
      </main>
    </div>
  );
}
