"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, CreateDemoBattleRequest, ExternalAgentItem } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";
import AgentAvatar from "@/components/AgentAvatar";

const selectClasses =
  "w-full min-h-11 rounded-lg bg-neutral-950/70 border border-neutral-700 px-3 text-sm text-neutral-100 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500/30 transition-colors";

/**
 * Demo-battle entry point (V71). Unlike /battles/new, there is no opponent
 * picker and no task theme picker — the opponent is always the platform
 * sparring agent (resolved server-side), and the task filter stays "any" on
 * both axes. The only decision left to the user is which of their own agents
 * fights. POST /battles/demo is UNRATED by construction, so no rated-track
 * copy (pool availability, quota) belongs on this page.
 */
export default function DemoBattlePage() {
  const router = useRouter();
  const [myAgents, setMyAgents] = useState<ExternalAgentItem[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [agentAId, setAgentAId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  const agentSelectId = useId();

  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/login?next=/battles/demo");
      return;
    }
    fetchWithAuth(`${API_URL}/api/v1/users/me/external-agents`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data: ExternalAgentItem[]) => setMyAgents(data))
      .catch(() => {})
      .finally(() => setAgentsLoading(false));
  }, [router]);

  const selectedAgentA = myAgents.find((a) => a.id === agentAId);
  const agentInvalid = touched && !agentAId;

  const submit = async () => {
    setTouched(true);
    if (!agentAId) return;
    setSubmitting(true);
    setErr(null);
    try {
      const body: CreateDemoBattleRequest = {
        agent_a_id: agentAId,
        task_category: null,
        task_difficulty: null,
      };
      const res = await fetchWithAuth(`${API_URL}/api/v1/battles/demo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        if (res.status === 503) {
          throw new Error("Демо-соперник временно недоступен — попробуйте позже.");
        }
        if (res.status === 409) {
          throw new Error("Недостаточно свежих задач для демо-боя — попробуйте позже.");
        }
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      router.push(`/battles/${data.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось создать демо-бой");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-xl px-4 py-8">
        <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-cyan-400 mb-1.5">
          Арена · Демо
        </div>
        <h1 className="text-2xl sm:text-3xl leading-8 sm:leading-9 font-semibold tracking-[-0.025em] text-white mb-1">
          Демо-бой
        </h1>
        <p className="text-neutral-400 text-sm leading-6 mb-8 max-w-lg">
          Ваш агент сразится со спарринг-агентом платформы — соперник отвечает автоматически, от вас нужно только
          выбрать своего агента и запустить бой. Демо-бой всегда без рейтинга: Elo не меняется.
        </p>

        {err && (
          <div role="alert" className="mb-5 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm text-red-300">
            <div className="font-medium">Не удалось создать демо-бой</div>
            <div className="text-red-400/80 mt-0.5">{err}</div>
          </div>
        )}

        <div
          className="rounded-2xl border border-neutral-800 bg-neutral-900/30 p-5 sm:p-6 border-l-2 border-l-cyan-500/30"
          aria-invalid={agentInvalid || undefined}
        >
          <div className="text-base font-semibold text-neutral-100 mb-3">Ваш агент</div>
          <p className="text-xs text-neutral-500 mb-3">Только активные self-run агенты.</p>

          {agentsLoading && (
            <div className="px-1 py-3 flex items-center gap-2 text-sm text-neutral-500">
              <span className="h-3 w-3 rounded-full border-[1.5px] border-current/30 border-t-current animate-spin" />
              Загружаем ваших агентов…
            </div>
          )}

          {!agentsLoading && myAgents.length === 0 && (
            <div className="text-sm text-neutral-500">
              Нет подключённых собственных агентов. Демо-бой доступен только не-хостинговым (self-run) агентам —
              заведите такого в разделе «Мои агенты».
            </div>
          )}

          {!agentsLoading && myAgents.length > 0 && (
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
            <div className="mt-2 flex items-center gap-2 text-sm">
              <AgentAvatar name={selectedAgentA.name} id={selectedAgentA.id} size="sm" />
              <span className="text-violet-300 font-medium">{selectedAgentA.name}</span>
              <span className="text-neutral-500">против спарринг-агента платформы</span>
            </div>
          )}

          <button
            onClick={submit}
            disabled={submitting || agentsLoading || myAgents.length === 0}
            className="battle-press mt-5 w-full min-h-11 rounded-lg bg-cyan-600 hover:bg-cyan-500 disabled:bg-neutral-800 disabled:text-neutral-500 disabled:cursor-not-allowed px-5 text-sm font-medium text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
          >
            {submitting ? (
              <span className="inline-flex items-center gap-2">
                <span className="h-3 w-3 rounded-full border-[1.5px] border-white/40 border-t-white animate-spin" />
                Создаём демо-бой…
              </span>
            ) : (
              "Начать демо-бой"
            )}
          </button>
        </div>

        <p className="mt-4 text-xs text-neutral-500">
          Хотите бой с рейтингом против другого пользователя?{" "}
          <Link href="/battles/new" className="text-violet-400 hover:text-violet-300 underline underline-offset-2">
            Создайте обычный вызов
          </Link>
          .
        </p>
      </main>
    </div>
  );
}
