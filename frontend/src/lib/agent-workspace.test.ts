// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { decodeTaskDetail, decodeTasks, decodeWorkspace, readWorkspace } from "./agent-workspace";

const ref = { id: "synthetic-agent", name: "Synthetic agent", handle: null };
const task = { id: "synthetic-task", title: "Synthetic task", description: null, project: null, requester: ref, claimant: null, viewer_roles: ["requester"], task_status: null, review_state: null, review_version: null, relationship: null, created_at: null, learning_reservation: null };
const counts = { awaiting_submission: 0, submitted: 0, changes_requested: 0, accepted: 0, rejected: 0, cancelled: 0 };
const agent = { ...ref, specialization: "programmer", is_active: true, created_at: null, last_heartbeat: null, budget: { available: 0, reserved: 0, transferred: 0, received: 0, refunded: 0, initial_grant_applied: false } };
afterEach(() => vi.unstubAllGlobals());

it("preserves unknown legacy fields and strips non-projected transport fields", () => {
  const detail = decodeTaskDetail({ ...task, result: "<img src=x onerror=alert(1)>", history: [], private_field: "synthetic-private-field" });
  expect(detail).toMatchObject({ task_status: null, project: null, relationship: null, result: "<img src=x onerror=alert(1)>" });
  expect(detail).not.toHaveProperty("private_field");
  expect(decodeWorkspace({ agents: [agent], review_counts: counts }).agents[0]?.budget.available).toBe(0);
});

it.each([
  { ...agent, last_heartbeat: "not-a-date" },
  { ...agent, budget: { ...agent.budget, available: "0" } },
  { ...agent, budget: { ...agent.budget, transferred: -1 } },
])("rejects malformed observed status and educational balances", malformed => {
  expect(() => decodeWorkspace({ agents: [malformed], review_counts: counts })).toThrow("Invalid workspace response");
});

it.each([
  { ...task, review_state: "future-state" },
  { ...task, viewer_roles: ["owner"] },
  { ...task, created_at: "bad-date" },
  { ...task, learning_reservation: { amount: 1, status: "paid" } },
])("rejects malformed task contract fields", malformed => {
  expect(() => decodeTasks({ items: [malformed], total: 1, limit: 50, offset: 0 })).toThrow("Invalid workspace response");
});

it("rejects incomplete counts and unsafe task pagination", () => {
  expect(() => decodeWorkspace({ agents: [], review_counts: {} })).toThrow();
  expect(() => decodeTasks({ items: [task], total: 1, limit: 0, offset: 0 })).toThrow();
  expect(() => decodeTasks({ items: [], total: 1, limit: 50, offset: 10001 })).toThrow();
});

it("uses the JWT reader and preserves HTTP errors rather than returning an empty workspace", async () => {
  vi.stubGlobal("localStorage", { getItem: (name: string) => name === "access_token" ? "synthetic-access" : null });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ agents: [], review_counts: counts }))));
  expect(await readWorkspace("agent-workspace", decodeWorkspace, new AbortController().signal)).toEqual({ agents: [], review_counts: counts });
  vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 401 }));
  await expect(readWorkspace("agent-workspace", decodeWorkspace, new AbortController().signal)).rejects.toMatchObject({ status: 401 });
});

it.each(["caller", "deadline"] as const)("settles a stalled real auth refresh when %s cancellation occurs", async source => {
  const store = new Map([["access_token", "synthetic-access"], ["refresh_token", "synthetic-refresh"]]);
  vi.stubGlobal("localStorage", { getItem: (key: string) => store.get(key) ?? null, removeItem: (key: string) => store.delete(key) });
  const caller = new AbortController(), deadline = new AbortController();
  const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
  let releaseRefresh: (value: Response) => void = () => {};
  let refreshStarted: () => void = () => {};
  const started = new Promise<void>(resolve => { refreshStarted = resolve; });
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input).endsWith("/auth/refresh") ? new Promise<Response>(resolve => { releaseRefresh = resolve; refreshStarted(); }) : Promise.resolve(new Response("", { status: 401 }))));
  const pending = readWorkspace("agent-workspace", decodeWorkspace, caller.signal);
  const outcome = pending.then(() => "fulfilled", error => error.name);
  await started;
  (source === "caller" ? caller : deadline).abort();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const actual = await Promise.race([outcome, new Promise<string>(resolve => { timer = setTimeout(() => resolve("still-pending"), 50); })]);
    expect(actual).toBe("AbortError");
  } finally {
    clearTimeout(timer);
    releaseRefresh(new Response("", { status: 401 }));
    await outcome;
    timeout.mockRestore();
  }
});

it("does not issue a request after the caller has already cancelled", async () => {
  const caller = new AbortController(); caller.abort();
  vi.stubGlobal("localStorage", { getItem: () => "synthetic-access" });
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));
  await expect(readWorkspace("agent-workspace", decodeWorkspace, caller.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(fetch).not.toHaveBeenCalled();
});

it("applies the deadline to a response body that never finishes", async () => {
  const deadline = new AbortController();
  const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
  let reading: () => void = () => {};
  const started = new Promise<void>(resolve => { reading = resolve; });
  let body: ReadableStreamDefaultController<Uint8Array> | undefined;
  const stream = new ReadableStream<Uint8Array>({ start(controller) { body = controller; }, pull() { reading(); } }, { highWaterMark: 0 });
  vi.stubGlobal("localStorage", { getItem: () => "synthetic-access" });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(stream)));
  const outcome = readWorkspace("agent-workspace", decodeWorkspace, new AbortController().signal).then(() => "fulfilled", error => error.name);
  await started;
  deadline.abort();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const actual = await Promise.race([outcome, new Promise<string>(resolve => { timer = setTimeout(() => resolve("still-pending"), 50); })]);
    expect(actual).toBe("AbortError");
  } finally {
    clearTimeout(timer);
    body?.enqueue(new TextEncoder().encode(JSON.stringify({ agents: [], review_counts: counts })));
    body?.close();
    await outcome;
    timeout.mockRestore();
  }
});
