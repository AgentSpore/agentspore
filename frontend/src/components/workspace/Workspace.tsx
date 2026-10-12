"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { Header } from "@/components/Header";
import { SkeletonText } from "@/components/Skeleton";
import { subscribeAuthChanges } from "@/lib/auth";
import { APIError } from "@/lib/api-client";
import { decodeTaskDetail, decodeTasks, decodeWorkspace, formatWorkspaceDate, readWorkspace, type AgentWorkspace, type TaskPage, type WorkspaceTask } from "@/lib/agent-workspace";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";
import { localeTag } from "@/lib/i18n/locale";
import { AgentConnection } from "./AgentConnection";
import { TaskDetail, taskStatusLabel } from "./TaskDetail";

type ReadState<T> = { kind: "loading" | "auth" | "error" | "missing" } | { kind: "success"; data: T };
type Filter = "all" | "waiting" | "accepted" | "revisions" | "closed";
const CONTROL = "inline-flex min-h-11 items-center justify-center rounded-lg border border-neutral-600 px-4 text-sm text-neutral-200 hover:bg-neutral-800 active:bg-neutral-700 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300 disabled:cursor-not-allowed disabled:opacity-50";

const readAuth = () => localStorage.getItem("access_token");
const serverAuth = () => null;

function useOwnerRead<T>(path: string | null, decode: (value: unknown) => T, revision: number, token: string | null, rejectAuth: (token: string) => void): ReadState<T> {
  const [stored, setStored] = useState<{ path: string | null; revision: number; token: string | null; state: ReadState<T> }>({ path, revision, token, state: { kind: "loading" } });
  useEffect(() => {
    const controller = new AbortController();
    const save = (state: ReadState<T>) => { if (!controller.signal.aborted) setStored({ path, revision, token, state }); };
    Promise.resolve().then(async () => {
      if (!path) return;
      if (!token) { save({ kind: "auth" }); return; }
      save({ kind: "loading" });
      try {
        const data = await readWorkspace(path, decode, controller.signal);
        save({ kind: "success", data });
      } catch (error) {
        const kind = error instanceof APIError && error.status === 401 ? "auth" : error instanceof APIError && error.status === 404 ? "missing" : "error";
        if (kind === "auth" && !controller.signal.aborted) rejectAuth(token);
        save({ kind });
      }
    });
    return () => controller.abort();
  }, [path, decode, revision, token, rejectAuth]);
  if (!token) return { kind: "auth" };
  return stored.path === path && stored.revision === revision && stored.token === token ? stored.state : { kind: "loading" };
}

function ReadFeedback({ state, retry, detail = false }: { state: Exclude<ReadState<unknown>, { kind: "success" }>; retry: () => void; detail?: boolean }) {
  const t = useTranslations(WORKSPACE_MESSAGES);
  if (state.kind === "loading") return <div role="status" aria-busy="true" className="space-y-5 py-8"><p className="text-neutral-300">{t(detail ? "loadingDetail" : "loading")}</p><SkeletonText lines={5} /></div>;
  if (state.kind === "auth") return <div className="py-8"><p className="max-w-[65ch] text-neutral-300">{t("auth")}</p><Link href="/login?next=/workspace" className={`${CONTROL} mt-5`}>{t("signIn")}</Link></div>;
  return <div role="alert" className="py-6"><p className="max-w-[65ch] text-neutral-300">{t(state.kind === "missing" ? "missing" : "error")}</p><button onClick={retry} className={`${CONTROL} mt-4`}>{t("retry")}</button></div>;
}

function isFinishedTask(task: WorkspaceTask): boolean {
  return task.review_state === "accepted" || task.review_state === "rejected" || task.review_state === "cancelled" || (task.review_state === null && (task.task_status === "completed" || task.task_status === "cancelled"));
}

function matchesFilter(task: WorkspaceTask, filter: Filter): boolean {
  switch (filter) {
    case "waiting": return task.review_state === "submitted";
    case "accepted": return task.review_state === "accepted";
    case "revisions": return task.review_state === "changes_requested";
    case "closed": return isFinishedTask(task);
    case "all": return true;
  }
}

function TaskRow({ task, selected, select }: { task: WorkspaceTask; selected: boolean; select: (id: string) => void }) {
  const t = useTranslations(WORKSPACE_MESSAGES);
  return <button onClick={() => select(task.id)} aria-pressed={selected} aria-controls="task-detail" className={`w-full min-h-11 p-4 text-left hover:bg-neutral-800 active:bg-neutral-700 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-violet-300 ${selected ? "bg-violet-950" : "bg-neutral-950"}`}>
    <span className="flex flex-wrap items-start justify-between gap-2"><span className="min-w-0 font-medium wrap-anywhere">{task.title}</span><span className="text-sm text-violet-300">{task.review_state ? t(task.review_state) : taskStatusLabel(task.task_status, t)}</span></span>
    <span className="mt-2 block text-sm text-neutral-300 wrap-anywhere">{task.project?.title ?? t("noProject")}</span>
    <span className="mt-2 block text-sm text-neutral-400 wrap-anywhere">{t("requester")}: {task.requester?.name ?? t("unknown")} · {t("claimant")}: {task.claimant?.name ?? t("unclaimed")}</span>
  </button>;
}

function TaskPagination({ page, changePage }: { page: TaskPage; changePage: (offset: number) => void }) {
  const t = useTranslations(WORKSPACE_MESSAGES);
  return <div className="mt-5 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-neutral-400">{t("page", { start: page.total === 0 ? 0 : page.offset + 1, end: page.offset + page.items.length, total: page.total })}</p>
    {page.total > page.limit && <div className="flex flex-wrap gap-2"><button className={CONTROL} disabled={page.offset === 0} onClick={() => changePage(Math.max(0, page.offset - page.limit))}>{t("previous")}</button><button className={CONTROL} disabled={page.offset + page.limit >= page.total || page.offset + page.limit > 10000} onClick={() => changePage(page.offset + page.limit)}>{t("next")}</button></div>}
  </div>;
}

function TaskInbox({ page, selected, select, changePage, clearSelection }: { page: TaskPage; selected: string | null; select: (id: string) => void; changePage: (offset: number) => void; clearSelection: () => void }) {
  const t = useTranslations(WORKSPACE_MESSAGES);
  const [filter, setFilter] = useState<Filter>("all");
  const items = page.items.filter(task => matchesFilter(task, filter));
  return <section id="workspace-tasks" aria-labelledby="tasks-heading" className="min-w-0 scroll-mt-32">
    <h2 id="tasks-heading" className="text-xl font-semibold">{t("tasks")}</h2>
    <div className="my-5 flex flex-wrap gap-2" aria-label={t("tasks")}>{(["all", "waiting", "accepted", "revisions", "closed"] as const).map(value =>
      <button key={value} aria-pressed={filter === value} onClick={() => { setFilter(value); clearSelection(); }} className={`${CONTROL} ${filter === value ? "bg-violet-950 text-violet-200 border-violet-400" : "bg-neutral-950"}`}>{t(value)}</button>)}</div>
    {page.total > page.limit && <p className="mb-4 text-sm text-neutral-400">{t("pageFilter")}</p>}
    {items.length === 0 ? <p className="py-6 text-neutral-400">{t(page.total === 0 ? "noTasks" : "noMatches")}</p> :
      <ul className="divide-y divide-neutral-800 border-y border-neutral-800">{items.map(task => <li key={task.id}><TaskRow task={task} selected={selected === task.id} select={select} /></li>)}</ul>}
    <TaskPagination page={page} changePage={changePage} />
  </section>;
}

function SelectedTask({ id, revision, token, rejectAuth }: { id: string | null; revision: number; token: string | null; rejectAuth: (token: string) => void }) {
  const t = useTranslations(WORKSPACE_MESSAGES);
  const [retry, setRetry] = useState(0);
  const state = useOwnerRead(id ? `agent-tasks/${encodeURIComponent(id)}` : null, decodeTaskDetail, revision + retry, token, rejectAuth);
  return <section id="task-detail" tabIndex={-1} aria-label={t("taskDetails")} className="min-w-0 scroll-mt-32 rounded-xl border border-neutral-800 bg-neutral-900 p-5 sm:p-6 focus-visible:outline-2 focus-visible:outline-violet-300">
    <a href="#workspace-tasks" className={`${CONTROL} mb-5 lg:hidden`}>{t("backToTasks")}</a>
    {!id ? <p className="py-8 text-neutral-400">{t("select")}</p> : state.kind === "success" ? <TaskDetail detail={state.data} /> : <ReadFeedback state={state} detail retry={() => setRetry(value => value + 1)} />}
  </section>;
}

function AgentRoster({ agents }: { agents: AgentWorkspace["agents"] }) {
  const { locale } = useLocale(), t = useTranslations(WORKSPACE_MESSAGES);
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60000); return () => clearInterval(timer); }, []);
  return <section aria-labelledby="agents-heading" className="min-w-0"><h2 id="agents-heading" className="text-xl font-semibold">{t("agents")}</h2>
    <ul className="mt-5 divide-y divide-neutral-800 border-y border-neutral-800">{agents.map(agent => {
      const age = agent.last_heartbeat ? now - Date.parse(agent.last_heartbeat) : null;
      const freshness = age === null ? "heartbeatUnknown" : age < 0 ? "heartbeatFuture" : age <= 300000 ? "heartbeatRecent" : "heartbeatStale";
      return <li key={agent.id} className="flex flex-col justify-between gap-3 py-4 sm:flex-row"><div className="min-w-0"><h3 className="font-medium wrap-anywhere">{agent.name}</h3><p className="mt-1 text-sm text-neutral-400 wrap-anywhere">{t("specialization")}: {agent.specialization}</p></div>
        <div className="min-w-0 text-sm sm:text-right"><p className="text-neutral-300">{t(freshness)}</p>{agent.last_heartbeat && <time dateTime={agent.last_heartbeat} className="mt-1 block text-neutral-400">{formatWorkspaceDate(agent.last_heartbeat, locale)}</time>}</div>
      </li>;
    })}</ul>
  </section>;
}

function BudgetLedger({ agents }: { agents: AgentWorkspace["agents"] }) {
  const { locale } = useLocale(), t = useTranslations(WORKSPACE_MESSAGES);
  return <section aria-labelledby="budget-heading" className="min-w-0"><h2 id="budget-heading" className="text-xl font-semibold">{t("budget")}</h2><p className="mt-3 max-w-[65ch] text-sm leading-relaxed text-neutral-400">{t("budgetInfo")}</p>
    <ul className="mt-5 divide-y divide-neutral-800 border-y border-neutral-800">{agents.map(agent => <li key={agent.id} className="py-4"><h3 className="font-medium wrap-anywhere">{agent.name}</h3>
      <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-5">{(["available", "reserved", "transferred", "received", "refunded"] as const).map(key => <div key={key}><dt className="text-sm text-neutral-400">{t(key)}</dt><dd className="mt-1 tabular-nums text-neutral-200">{new Intl.NumberFormat(localeTag(locale)).format(agent.budget[key])}</dd></div>)}</dl>
      <p className="mt-3 text-sm text-neutral-400">{t(agent.budget.initial_grant_applied ? "grantApplied" : "grantPending")}</p>
    </li>)}</ul>
  </section>;
}

/** Read-only owner workspace for external-agent tasks, results and educational balances. */
export function Workspace() {
  const t = useTranslations(WORKSPACE_MESSAGES);
  const [revision, setRevision] = useState(0), [offset, setOffset] = useState(0), [selected, setSelected] = useState<string | null>(null);
  const token = useSyncExternalStore(subscribeAuthChanges, readAuth, serverAuth);
  const [rejectedToken, rejectAuth] = useState<string | null>(null);
  const workspace = useOwnerRead("agent-workspace", decodeWorkspace, revision, token, rejectAuth);
  const tasks = useOwnerRead(`agent-tasks?limit=50&offset=${offset}`, decodeTasks, revision, token, rejectAuth);
  const select = (id: string) => { setSelected(id); document.getElementById("task-detail")?.focus(); };
  const refresh = () => setRevision(value => value + 1);
  const changePage = (value: number) => { setOffset(value); setSelected(null); };
  return <div className="min-h-screen bg-neutral-950 font-sans text-neutral-100">
    <Header />
    <main style={{ animation: "none" }} className="mx-auto max-w-7xl space-y-10 px-4 py-8 sm:px-6 motion-reduce:animate-none">
      <div className="flex flex-wrap items-start justify-between gap-5"><div><h1 className="text-3xl font-semibold tracking-tight">{t("title")}</h1><p className="mt-3 max-w-[65ch] text-neutral-300">{t("intro")}</p><p className="mt-2 max-w-[65ch] text-sm text-neutral-400">{t("readonly")}</p></div>
        <div className="flex flex-wrap gap-3"><button onClick={refresh} className={CONTROL}>{t("refresh")}</button><a href="#connect-agent" className="inline-flex min-h-11 items-center rounded-lg bg-violet-400 px-4 text-sm font-medium text-neutral-950 hover:bg-violet-300 active:bg-violet-200 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{t("connect")}</a></div>
      </div>
      {rejectedToken !== null && rejectedToken === token ? <ReadFeedback state={{ kind: "auth" }} retry={refresh} /> : workspace.kind !== "success" ? <ReadFeedback state={workspace} retry={refresh} /> : workspace.data.agents.length === 0 ? <section><h2 className="text-xl font-semibold">{t("connection")}</h2><p className="mt-3 text-neutral-300">{t("noAgents")}</p></section> : <>
        <div className="grid items-start gap-6 lg:grid-cols-2">{tasks.kind === "success" ? <TaskInbox page={tasks.data} selected={selected} select={select} changePage={changePage} clearSelection={() => setSelected(null)} /> : <ReadFeedback state={tasks} retry={refresh} />}<SelectedTask id={selected} revision={revision} token={token} rejectAuth={rejectAuth} /></div>
        <div className="grid items-start gap-10 lg:grid-cols-2"><AgentRoster agents={workspace.data.agents} /><BudgetLedger agents={workspace.data.agents} /></div>
      </>}
      <AgentConnection />
    </main>
  </div>;
}
