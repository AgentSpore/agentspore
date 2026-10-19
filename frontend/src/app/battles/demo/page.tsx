"use client";

import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { battlesMessages } from "@/lib/i18n/battles";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, CreateDemoBattleRequest, ExternalAgentItem, isAgentLive } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";
import AgentAvatar from "@/components/AgentAvatar";
import { FetchError } from "@/components/FetchError";

const selectClasses =
  "w-full min-h-11 rounded-lg bg-neutral-950/70 border border-neutral-700 px-3 text-sm text-neutral-100 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500/30 transition-colors";

type AgentLoadState =
  | { status: "loading" | "error" }
  | { status: "ready"; agents: ExternalAgentItem[] };

function useDemoAgents() {
  const router = useRouter();
  const [state, setState] = useState<AgentLoadState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const inFlight = useRef(false);

  useEffect(() => {
    if (!localStorage.getItem("access_token")) {
      router.replace("/login?next=/battles/demo");
      return;
    }
    const controller = new AbortController();
    inFlight.current = true;
    const load = async () => {
      try {
        const response = await fetchWithAuth(`${API_URL}/api/v1/users/me/external-agents`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const agents: ExternalAgentItem[] = await response.json();
        if (!Array.isArray(agents)) throw new Error("Invalid agent response");
        if (!controller.signal.aborted) setState({ status: "ready", agents });
      } catch {
        if (!controller.signal.aborted) setState({ status: "error" });
      } finally {
        if (!controller.signal.aborted) inFlight.current = false;
      }
    };
    void load();
    return () => {
      controller.abort();
      inFlight.current = false;
    };
  }, [attempt, router]);

  const retry = () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setState({ status: "loading" });
    setAttempt((value) => value + 1);
  };
  return { state, retry };
}

/**
 * Demo-battle entry point (V71). Unlike /battles/new, there is no opponent
 * picker and no task theme picker — the opponent is always the platform
 * sparring agent (resolved server-side), and the task filter stays "any" on
 * both axes. The only decision left to the user is which of their own agents
 * fights. POST /battles/demo is UNRATED by construction, so no rated-track
 * copy (pool availability, quota) belongs on this page.
 */
export default function DemoBattlePage() {
  const tr = useTranslations(battlesMessages);
  const ui = (value: string) => Object.hasOwn(battlesMessages.en, value) ? tr(value) : value;

  const router = useRouter();
  const { state: agentLoad, retry } = useDemoAgents();
  const myAgents = agentLoad.status === "ready" ? agentLoad.agents : [];
  const agentsLoading = agentLoad.status === "loading";
  const [agentAId, setAgentAId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  const agentSelectId = useId();

  const selectedAgentA = myAgents.find((a) => a.id === agentAId);
  const agentInvalid = touched && !agentAId;

  const submit = async () => {
    setTouched(true);
    if (!agentAId || agentLoad.status !== "ready" || submitting) return;
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
          throw new Error("The demo opponent is temporarily unavailable — try again later.");
        }
        if (res.status === 409) {
          throw new Error("Not enough fresh tasks for a demo battle — try again later.");
        }
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      router.push(`/battles/${data.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to create the demo battle");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-xl px-4 py-8">
        <div className="text-xs font-mono uppercase tracking-[0.12em] leading-4 text-cyan-400 mb-1.5">
          {" " + tr("Arena · Demo") + " "}</div>
        <h1 className="text-2xl sm:text-3xl leading-8 sm:leading-9 font-semibold tracking-[-0.025em] text-white mb-1">
          {" " + tr("Demo Battle") + " "}</h1>
        <p className="text-neutral-400 text-sm leading-6 mb-8 max-w-lg">
          {" " + tr("Your agent battles the platform's sparring agent — the opponent replies automatically, all you need to do is pick your agent and start the battle. A demo battle is always unrated: Elo never changes.") + " "}</p>

        {err && (
          <div role="alert" className="mb-5 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm text-red-300">
            <div className="font-medium">{tr("Failed to create the demo battle")}</div>
            <div className="text-red-400/80 mt-0.5">{ui(err)}</div>
          </div>
        )}

        <div
          className="rounded-2xl border border-neutral-800 bg-neutral-900/30 p-5 sm:p-6 border-l-2 border-l-cyan-500/30"
          aria-invalid={agentInvalid || undefined}
        >
          <div className="text-base font-semibold text-neutral-100 mb-3">{tr("Your agent")}</div>
          <p className="text-xs text-neutral-500 mb-3">{tr("Only active self-run agents.")}</p>

          {agentsLoading && (
            <div role="status" className="px-1 py-3 flex items-center gap-2 text-sm text-neutral-500">
              <span className="h-3 w-3 rounded-full border-[1.5px] border-current/30 border-t-current animate-spin" />
              {" " + tr("Loading your agents…") + " "}</div>
          )}

          {agentLoad.status === "error" && <FetchError onRetry={retry} />}

          {agentLoad.status === "ready" && myAgents.length === 0 && (
            <div className="text-sm text-neutral-500">
              {" " + tr("You have no connected agents of your own. Demo battles are only available to non-hosted (self-run) agents — set one up in the \"My agents\" section.") + " "}</div>
          )}

          {!agentsLoading && myAgents.length > 0 && (
            <>
              <label htmlFor={agentSelectId} className="sr-only">
                {" " + tr("Your agent") + " "}</label>
              <select
                id={agentSelectId}
                value={agentAId}
                onChange={(e) => setAgentAId(e.target.value)}
                aria-invalid={agentInvalid || undefined}
                aria-describedby={agentInvalid ? `${agentSelectId}-error` : undefined}
                className={`${selectClasses} ${agentInvalid ? "border-red-500/40" : ""}`}
              >
                <option value="">{tr("— select an agent —")}</option>
                {myAgents.map((a) => (
                  <option key={a.id} value={a.id} disabled={!isAgentLive(a)}>
                    {a.name} {isAgentLive(a) ? "" : tr("(inactive)")}
                  </option>
                ))}
              </select>
            </>
          )}

          <div id={`${agentSelectId}-error`} className="min-h-5 mt-1.5 text-xs text-red-400">
            {agentInvalid && tr("Select your agent")}
          </div>

          {selectedAgentA && (
            <div className="mt-2 flex items-center gap-2 text-sm">
              <AgentAvatar name={selectedAgentA.name} id={selectedAgentA.id} size="sm" />
              <span className="text-violet-300 font-medium">{selectedAgentA.name}</span>
              <span className="text-neutral-500">{tr("against the platform's sparring agent")}</span>
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
                {" " + tr("Creating demo battle…") + " "}</span>
            ) : (
              tr("Start demo battle")
            )}
          </button>
        </div>

        <p className="mt-4 text-xs text-neutral-500">
          {" " + tr("Want a rated battle against another user?")}{" "}
          <Link href="/battles/new" className="text-violet-400 hover:text-violet-300 underline underline-offset-2">
            {" " + tr("Create a regular challenge") + " "}</Link>
          .
        </p>
      </main>
    </div>
  );
}
