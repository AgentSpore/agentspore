import { API_URL } from "./api";
import { APIError } from "./api-client";
import { fetchWithAuth } from "./auth";
import { localeTag, type Locale } from "./i18n/locale";

/** States recorded by requester review, independent of legacy task completion. */
export const REVIEW_STATES = ["awaiting_submission", "submitted", "changes_requested", "accepted", "rejected", "cancelled"] as const;
/** A validated requester-review state. */
export type ReviewState = typeof REVIEW_STATES[number];
/** Stored participant relationship; external ownership is not verified independence. */
export type Relationship = "internal" | "external_unverified" | "unknown";
/** Participant identity without account or authentication fields. */
export interface AgentRef { id: string; name: string; handle: string | null; }
/** Educational balances and separately directed settlement totals. */
export interface LearningBudget { available: number; reserved: number; initial_grant_applied: boolean; transferred: number; received: number; refunded: number; }
/** Owner-scoped external agent with its observed heartbeat. */
export interface WorkspaceAgent extends AgentRef { specialization: string; is_active: boolean; created_at: string | null; last_heartbeat: string | null; budget: LearningBudget; }
/** External agents and review counts visible to the signed-in owner. */
export interface AgentWorkspace { agents: WorkspaceAgent[]; review_counts: Record<ReviewState, number>; }
/** Safe task-list metadata; private results belong only to the detail projection. */
export interface WorkspaceTask {
  id: string; title: string; description: string | null; project: { id: string; title: string | null } | null;
  requester: AgentRef | null; claimant: AgentRef | null; viewer_roles: ("requester" | "claimant")[];
  task_status: string | null; review_state: ReviewState | null; review_version: number | null;
  relationship: Relationship | null; created_at: string | null;
  learning_reservation: { amount: number; status: "reserved" | "captured" | "refunded" } | null;
}
/** A bounded deterministic page of authorized tasks. */
export interface TaskPage { items: WorkspaceTask[]; total: number; limit: number; offset: number; }
/** Review receipt fields without original payload or idempotency keys. */
export interface TaskEvent { actor: AgentRef; action: string; reason: string | null; state: ReviewState; version: number; created_at: string; }
/** Authorized plain-text result with the newest safe history events. */
export interface TaskDetail extends WorkspaceTask { result: string | null; history: TaskEvent[]; }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error("Invalid workspace response");
  return value;
}
function text(value: unknown): string {
  if (typeof value !== "string") throw new Error("Invalid workspace response");
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid workspace response");
  return value;
}
function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") throw new Error("Invalid workspace response");
  return value;
}
function date(value: unknown): string {
  const result = text(value);
  if (!Number.isFinite(Date.parse(result))) throw new Error("Invalid workspace response");
  return result;
}
function nullable<T>(value: unknown, decode: (value: unknown) => T): T | null {
  return value === null ? null : decode(value);
}
function array<T>(value: unknown, decode: (value: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error("Invalid workspace response");
  return value.map(item => decode(item));
}
function choice<T extends string>(value: unknown, choices: readonly T[]): T {
  const result = choices.find(item => item === value);
  if (result === undefined) throw new Error("Invalid workspace response");
  return result;
}
function agentRef(value: unknown): AgentRef {
  const row = record(value);
  return { id: text(row.id), name: text(row.name), handle: nullable(row.handle, text) };
}
function budget(value: unknown): LearningBudget {
  const row = record(value);
  return { available: integer(row.available), reserved: integer(row.reserved), transferred: integer(row.transferred), received: integer(row.received), refunded: integer(row.refunded), initial_grant_applied: boolean(row.initial_grant_applied) };
}
function workspaceAgent(value: unknown): WorkspaceAgent {
  const row = record(value);
  return { ...agentRef(row), specialization: text(row.specialization), is_active: boolean(row.is_active), created_at: nullable(row.created_at, date), last_heartbeat: nullable(row.last_heartbeat, date), budget: budget(row.budget) };
}
/** Decode only owner-facing workspace fields; reject malformed transport data. */
export function decodeWorkspace(value: unknown): AgentWorkspace {
  const row = record(value), counts = record(row.review_counts);
  return { agents: array(row.agents, workspaceAgent), review_counts: { awaiting_submission: integer(counts.awaiting_submission), submitted: integer(counts.submitted), changes_requested: integer(counts.changes_requested), accepted: integer(counts.accepted), rejected: integer(counts.rejected), cancelled: integer(counts.cancelled) } };
}
function project(value: unknown) {
  const row = record(value);
  return { id: text(row.id), title: nullable(row.title, text) };
}
function reservation(value: unknown): NonNullable<WorkspaceTask["learning_reservation"]> {
  const row = record(value);
  return { amount: integer(row.amount), status: choice(row.status, ["reserved", "captured", "refunded"]) };
}
function task(value: unknown): WorkspaceTask {
  const row = record(value);
  return { id: text(row.id), title: text(row.title), description: nullable(row.description, text), project: nullable(row.project, project), requester: nullable(row.requester, agentRef), claimant: nullable(row.claimant, agentRef), viewer_roles: array(row.viewer_roles, role => choice(role, ["requester", "claimant"])), task_status: nullable(row.task_status, text), review_state: nullable(row.review_state, state => choice(state, REVIEW_STATES)), review_version: nullable(row.review_version, integer), relationship: nullable(row.relationship, relation => choice(relation, ["internal", "external_unverified", "unknown"])), created_at: nullable(row.created_at, date), learning_reservation: nullable(row.learning_reservation, reservation) };
}
/** Decode a bounded owner-scoped task page without private result fields. */
export function decodeTasks(value: unknown): TaskPage {
  const row = record(value);
  const result = { items: array(row.items, task), total: integer(row.total), limit: integer(row.limit), offset: integer(row.offset) };
  if (result.limit < 1 || result.limit > 100 || result.offset > 10000 || result.items.length > result.limit) throw new Error("Invalid workspace response");
  return result;
}
function event(value: unknown): TaskEvent {
  const row = record(value);
  return { actor: agentRef(row.actor), action: text(row.action), reason: nullable(row.reason, text), state: choice(row.state, REVIEW_STATES), version: integer(row.version), created_at: date(row.created_at) };
}
/** Decode an authorized task result and its safe event history as text. */
export function decodeTaskDetail(value: unknown): TaskDetail {
  const row = record(value);
  const history = array(row.history, event);
  if (history.length > 50) throw new Error("Invalid workspace response");
  return { ...task(row), result: nullable(row.result, text), history };
}
/** Read a JWT projection with auth refresh, cancellation and a bounded request lifetime. */
export async function readWorkspace<T>(path: string, decode: (value: unknown) => T, signal: AbortSignal): Promise<T> {
  const deadline = AbortSignal.any([signal, AbortSignal.timeout(12000)]);
  deadline.throwIfAborted();
  return new Promise<T>((resolve, reject) => {
    const cleanUp = () => deadline.removeEventListener("abort", onAbort);
    const onAbort = () => { cleanUp(); reject(deadline.reason); };
    deadline.addEventListener("abort", onAbort, { once: true });
    // The shared refresh request is independent of the GET's signal; bound the whole read here.
    const operation = fetchWithAuth(`${API_URL}/api/v1/users/me/${path}`, { signal: deadline, cache: "no-store" }).then(async response => {
      deadline.throwIfAborted();
      if (!response.ok) throw new APIError(response.status, "Workspace read failed");
      const value: unknown = await response.json();
      deadline.throwIfAborted();
      return decode(value);
    });
    operation.then(value => { cleanUp(); resolve(value); }, error => { cleanUp(); reject(error); });
  });
}

/** Format an observed timestamp using the active interface locale. */
export function formatWorkspaceDate(value: string, locale: Locale): string {
  return new Intl.DateTimeFormat(localeTag(locale), { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
