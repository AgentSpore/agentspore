// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import ChatPage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";
import { communicationMessages } from "@/lib/i18n/communication";
import { sharedMessages } from "@/lib/i18n/shared";
import { API_URL } from "@/lib/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/chat", useRouter: () => ({ refresh: vi.fn() }) }));


const MESSAGE = {
  id: "m1", agent_id: "a1", agent_name: "Synthetic agent", specialization: "programmer",
  content: "hello there", message_type: "text", sender_type: "agent", ts: "2026-01-01T00:00:00Z",
};

function stubChat(rows: unknown[]) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => rows }));
  vi.stubGlobal("EventSource", class { onmessage = null; close() {} });
  Element.prototype.scrollIntoView = vi.fn();
}

beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each(["en", "ru"] as const)("teaches only when no message was loaded in %s", async (locale) => {
  stubChat([]);
  render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);
  expect(await screen.findByText(docsMessages[locale].eChatTitle)).toBeTruthy();
  expect(screen.queryByText(docsMessages[locale].eChatFiltered)).toBeNull();
});

it.each(["en", "ru"] as const)("shows a plain line, not the teaching state, when only the type filter is empty in %s", async (locale) => {
  stubChat([MESSAGE]);
  render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);
  await screen.findByText("hello there");
  const idea = communicationMessages[locale]["Idea"] ?? "Idea";
  fireEvent.click(screen.getByRole("button", { name: new RegExp(idea) }));
  await waitFor(() => expect(screen.getByText(docsMessages[locale].eChatFiltered)).toBeTruthy());
  expect(screen.queryByText(docsMessages[locale].eChatTitle)).toBeNull();
  expect(screen.queryByRole("link", { name: docsMessages[locale].eAction })).toBeNull();
});

const READ_ERROR = {
  en: "Could not load messages.",
  ru: "Не удалось загрузить сообщения.",
};
const LIVE_MESSAGE = { ...MESSAGE, id: "m2", content: "An idea arriving live" };

function pendingRead() {
  let resolve!: (response: Response) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Response>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function stubInitialRead(read: () => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    if (method === "GET" && url.pathname === "/api/v1/chat/messages"
      && url.searchParams.get("limit") === "50" && !url.searchParams.has("before")) return read();
    return Promise.reject(new Error(`Unexpected request: ${method} ${url.pathname}`));
  }));
  const streams: SyntheticEventSource[] = [];
  class SyntheticEventSource {
    onmessage: ((event: MessageEvent<string>) => void) | null = null;
    constructor() { streams.push(this); }
    close() {}
  }
  vi.stubGlobal("EventSource", SyntheticEventSource);
  Element.prototype.scrollIntoView = vi.fn();
  return {
    emit(message: typeof MESSAGE | { type: "edit"; id: string; content: string } | { type: "delete"; id: string }) {
      const stream = streams.at(-1);
      if (!stream?.onmessage) throw new Error("The chat stream is not subscribed");
      stream.onmessage(new MessageEvent("message", { data: JSON.stringify(message) }));
    },
  };
}

it.each([
  { locale: "en", failure: "503" }, { locale: "ru", failure: "503" },
  { locale: "en", failure: "network" }, { locale: "ru", failure: "network" },
] as const)("shows a localized read error instead of empty teaching after $failure in $locale", async ({ locale, failure }) => {
  stubInitialRead(async () => {
    if (failure === "network") throw new TypeError("Synthetic network failure");
    return new Response("Service unavailable", { status: 503 });
  });
  render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);

  expect(await screen.findByText(READ_ERROR[locale])).toBeTruthy();
  expect(screen.getByRole("button", { name: sharedMessages[locale].retry })).toBeTruthy();
  expect(screen.queryByText(docsMessages[locale].eChatTitle)).toBeNull();
  expect(screen.queryByRole("link", { name: docsMessages[locale].eAction })).toBeNull();
});

it.each(["en", "ru"] as const)("retries once while pending and preserves live messages when history succeeds in %s", async (locale) => {
  const retry = pendingRead();
  const read = vi.fn<() => Promise<Response>>()
    .mockResolvedValueOnce(new Response("Service unavailable", { status: 503 }))
    .mockImplementation(() => retry.promise);
  const stream = stubInitialRead(read);
  render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);
  await screen.findByText(READ_ERROR[locale]);
  await act(async () => stream.emit(LIVE_MESSAGE));
  expect(screen.getByText(LIVE_MESSAGE.content)).toBeTruthy();

  const retryButton = screen.getByRole("button", { name: sharedMessages[locale].retry });
  fireEvent.click(retryButton);
  fireEvent.click(retryButton);
  await act(async () => {});
  expect(read).toHaveBeenCalledTimes(2);
  expect(screen.getByText(LIVE_MESSAGE.content)).toBeTruthy();
  expect(screen.queryByText(docsMessages[locale].eChatTitle)).toBeNull();

  await act(async () => retry.resolve(Response.json([MESSAGE])));
  expect(await screen.findByText(MESSAGE.content)).toBeTruthy();
  expect(screen.getAllByText(LIVE_MESSAGE.content)).toHaveLength(1);
  expect(screen.queryByText(READ_ERROR[locale])).toBeNull();
  expect(screen.queryByRole("button", { name: sharedMessages[locale].retry })).toBeNull();
});

it.each(["en", "ru"] as const)("keeps an event received before the initial history response in %s", async (locale) => {
  const initial = pendingRead();
  const stream = stubInitialRead(() => initial.promise);
  render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);

  await act(async () => stream.emit(LIVE_MESSAGE));
  await act(async () => initial.resolve(Response.json([MESSAGE])));

  expect(await screen.findByText(MESSAGE.content)).toBeTruthy();
  expect(screen.getAllByText(LIVE_MESSAGE.content)).toHaveLength(1);
  expect(screen.queryByText(docsMessages[locale].eChatTitle)).toBeNull();
});

it.each([
  { stage: "initial", mutation: "edit" }, { stage: "initial", mutation: "delete" },
  { stage: "retry", mutation: "edit" }, { stage: "retry", mutation: "delete" },
] as const)("preserves an unseen message $mutation while $stage history is pending", async ({ stage, mutation }) => {
  const history = pendingRead();
  const read = vi.fn<() => Promise<Response>>();
  if (stage === "retry") read.mockResolvedValueOnce(new Response("Unavailable", { status: 503 }));
  read.mockImplementation(() => history.promise);
  const stream = stubInitialRead(read);
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);
  if (stage === "retry") {
    await screen.findByText(READ_ERROR.en);
    fireEvent.click(screen.getByRole("button", { name: sharedMessages.en.retry }));
  }

  await act(async () => stream.emit(mutation === "edit"
    ? { type: "edit", id: MESSAGE.id, content: "An updated idea" }
    : { type: "delete", id: MESSAGE.id }));
  await act(async () => history.resolve(Response.json([MESSAGE])));

  expect(await screen.findByText(mutation === "edit" ? "An updated idea" : "[deleted]")).toBeTruthy();
  expect(screen.queryByText(MESSAGE.content)).toBeNull();
  expect(screen.queryByText(docsMessages.en.eChatTitle)).toBeNull();
});

it("replaces the cancelled StrictMode read without leaving chat loading", async () => {
  const pending = pendingRead();
  const read = vi.fn(() => pending.promise.then(response => response.clone()));
  stubInitialRead(read);
  const { unmount } = render(<StrictMode><LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider></StrictMode>);
  expect(read).toHaveBeenCalledTimes(2);
  const requests = vi.mocked(fetch).mock.calls;
  expect(requests[0][1]?.signal?.aborted).toBe(true);
  expect(requests[1][1]?.signal?.aborted).toBe(false);
  await act(async () => pending.resolve(Response.json([MESSAGE])));
  expect(await screen.findByText(MESSAGE.content)).toBeTruthy();
  expect(screen.queryByText(READ_ERROR.en)).toBeNull();
  unmount();
});

const COMPOSER_OWNER = {
  id: "synthetic-owner", name: "Synthetic Person", email: "person@example.invalid",
  avatar_url: null, token_balance: 0, is_admin: false,
};
const OWN_MESSAGES = ["Original first idea", "Original second idea"].map((content, index) => ({
  ...MESSAGE, id: `own-${index}`, agent_id: null, agent_name: COMPOSER_OWNER.name,
  specialization: "user", sender_type: "user", sender_user_id: COMPOSER_OWNER.id, is_deleted: false, content,
}));

async function openComposer(pending: ReturnType<typeof pendingRead>, locale: "en" | "ru" = "en") {
  localStorage.setItem("access_token", "synthetic-composer-access");
  const requests: { method: string; path: string; body: unknown }[] = [];
  const unexpected: string[] = [];
  stubChat([]);
  const readRoutes = new Map([
    ["GET /api/v1/auth/me", () => Response.json(COMPOSER_OWNER)],
    ["GET /api/v1/chat/messages?limit=50", () => Response.json([...OWN_MESSAGES].reverse())],
  ]);
  const writeRoutes = new Set(["POST /api/v1/chat/human-message",
    ...OWN_MESSAGES.map(message => `PATCH /api/v1/chat/human-messages/${message.id}`)]);
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    const url = new URL(input);
    const method = init?.method ?? "GET";
    const route = `${method} ${url.pathname}${url.search}`;
    if (url.origin !== new URL(API_URL).origin) {
      unexpected.push("Unexpected composer origin");
      return Promise.reject(new Error("Unexpected composer origin"));
    }
    const read = readRoutes.get(route);
    if (read) return Promise.resolve(read());
    if (writeRoutes.has(route)) {
      requests.push({ method, path: url.pathname, body: JSON.parse(String(init?.body)) });
      return pending.promise;
    }
    unexpected.push(route);
    return Promise.reject(new Error("Unexpected synthetic composer request"));
  }));
  render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);
  const input = await screen.findByRole<HTMLTextAreaElement>("textbox");
  await screen.findByText(OWN_MESSAGES[0].content);
  return { input, requests, unexpected };
}

function compose(input: HTMLTextAreaElement, content: string) {
  fireEvent.change(input, { target: { value: content } });
}

function submitComposer(input: HTMLTextAreaElement) {
  fireEvent.keyDown(input, { key: "Enter" });
}

it.each(["a newer draft", "submitted text"])("keeps POST draft revision when pending text becomes %s", async (newDraft) => {
  const pending = pendingRead();
  const { input, requests, unexpected } = await openComposer(pending);
  compose(input, "submitted text");
  await act(async () => { submitComposer(input); submitComposer(input); });
  compose(input, "an intermediate revision");
  compose(input, newDraft);
  expect(input.disabled).toBe(false);
  expect(requests).toEqual([{ method: "POST", path: "/api/v1/chat/human-message", body: { content: "submitted text" } }]);

  await act(async () => pending.resolve(Response.json({ status: "ok", message_id: "new-message" })));
  expect(input.value).toBe(newDraft);
  expect(unexpected).toEqual([]);
});

const HISTORY_PAGE = Array.from({ length: 50 }, (_, index) => ({
  ...MESSAGE, id: `history-${index}`, content: `History row ${index}`,
  ts: new Date(Date.UTC(2026, 0, 1, 0, 50 - index)).toISOString(),
}));
const OLDER_MESSAGE = { ...MESSAGE, id: "older-message", content: "An older history row" };

function stubAuthorizedHistory(rows = [MESSAGE], refresh = false) {
  const policy = { token: "synthetic-history-access" };
  const requests: { before: string | null; authorization: string | null; signal?: AbortSignal | null }[] = [];
  const unexpected: string[] = [];
  const refreshRequests: unknown[] = [];
  stubChat([]);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
  const historyResponse = (init: RequestInit | undefined, before: string | null) => {
    const authorization = new Headers(init?.headers).get("Authorization");
    requests.push({ before, authorization, signal: init?.signal });
    if (authorization !== `Bearer ${policy.token}`)
      return Response.json({ detail: "Not authenticated" }, { status: 401 });
    return Response.json(before ? [OLDER_MESSAGE] : rows);
  };
  const oldest = rows[rows.length - 1].id;
  const routes = new Map<string, (init?: RequestInit) => Response>([
    ["GET /api/v1/auth/me", () => Response.json(COMPOSER_OWNER)],
    ["GET /api/v1/chat/messages?limit=50", init => historyResponse(init, null)],
    [`GET /api/v1/chat/messages?limit=50&before=${oldest}`, init => historyResponse(init, oldest)],
  ]);
  if (refresh) routes.set("POST /api/v1/auth/refresh", init => {
    refreshRequests.push(JSON.parse(String(init?.body)));
    return Response.json({ access_token: policy.token, refresh_token: "synthetic-replacement-refresh" });
  });
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    const url = new URL(input), method = init?.method ?? "GET";
    if (url.origin !== new URL(API_URL).origin) {
      unexpected.push("Unexpected history origin");
      return Promise.reject(new Error("Unexpected history request"));
    }
    const route = `${method} ${url.pathname}${url.search}`, respond = routes.get(route);
    if (respond) return Promise.resolve(respond(init));
    unexpected.push(route);
    return Promise.reject(new Error("Unexpected history route"));
  }));
  return { policy, requests, unexpected, refreshRequests };
}

it("loads authenticated initial history and preserves its AbortSignal", async () => {
  const history = stubAuthorizedHistory();
  localStorage.setItem("access_token", history.policy.token);
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);

  expect(await screen.findByText(MESSAGE.content)).toBeTruthy();
  expect(history.requests).toHaveLength(1);
  expect(history.requests[0].authorization).toBe(`Bearer ${history.policy.token}`);
  expect(history.requests[0].signal).toBeInstanceOf(AbortSignal);
  expect(history.requests[0].signal?.aborted).toBe(false);
  expect(history.unexpected).toEqual([]);
});

it("authenticates cursor history while keeping the loaded fifty messages", async () => {
  const history = stubAuthorizedHistory(HISTORY_PAGE);
  localStorage.setItem("access_token", history.policy.token);
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);
  await screen.findByText(HISTORY_PAGE[0].content);
  history.policy.token = "synthetic-pagination-access";
  localStorage.setItem("access_token", history.policy.token);
  fireEvent.scroll(screen.getByRole("main"));

  expect(await screen.findByText(OLDER_MESSAGE.content)).toBeTruthy();
  expect(screen.getByText(HISTORY_PAGE[0].content)).toBeTruthy();
  expect(screen.getByText(HISTORY_PAGE[49].content)).toBeTruthy();
  expect(history.requests[1].before).toBe(HISTORY_PAGE[49].id);
  expect(history.requests[1].authorization).toBe(`Bearer ${history.policy.token}`);
  expect(history.unexpected).toEqual([]);
});

it("reads a replacement token when retrying an unauthorized history request", async () => {
  const history = stubAuthorizedHistory();
  localStorage.setItem("access_token", "synthetic-expired-access");
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);
  await screen.findByText(READ_ERROR.en);
  localStorage.setItem("access_token", history.policy.token);
  fireEvent.click(screen.getByRole("button", { name: sharedMessages.en.retry }));

  expect(await screen.findByText(MESSAGE.content)).toBeTruthy();
  expect(history.requests.map(request => request.authorization)).toEqual([
    "Bearer synthetic-expired-access", `Bearer ${history.policy.token}`,
  ]);
  expect(screen.queryByText(READ_ERROR.en)).toBeNull();
  expect(history.unexpected).toEqual([]);
});

it("shows the unauthorized read error without sending a malformed Bearer when signed out", async () => {
  const history = stubAuthorizedHistory();
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);

  expect(await screen.findByText(READ_ERROR.en)).toBeTruthy();
  expect(screen.getByRole("button", { name: sharedMessages.en.retry })).toBeTruthy();
  expect(history.requests[0].authorization).toBeNull();
  expect(screen.queryByText(docsMessages.en.eChatTitle)).toBeNull();
  expect(screen.queryByText(MESSAGE.content)).toBeNull();
  expect(history.unexpected).toEqual([]);
});

it("refreshes unauthorized history through the real auth helper with the same AbortSignal", async () => {
  const history = stubAuthorizedHistory([MESSAGE], true);
  localStorage.setItem("access_token", "synthetic-expired-access");
  localStorage.setItem("refresh_token", "synthetic-history-refresh");
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);

  expect(await screen.findByText(MESSAGE.content)).toBeTruthy();
  expect(history.refreshRequests).toEqual([{ refresh_token: "synthetic-history-refresh" }]);
  expect(history.requests.map(request => request.authorization)).toEqual([
    "Bearer synthetic-expired-access", `Bearer ${history.policy.token}`,
  ]);
  expect(history.requests[0].signal).toBeInstanceOf(AbortSignal);
  expect(history.requests[1].signal).toBe(history.requests[0].signal);
  expect(history.requests[1].signal?.aborted).toBe(false);
  expect(history.unexpected).toEqual([]);
});

it.each(["Escape", "Cancel (Esc)"])("keeps a new draft after %s cancels a pending PATCH", async (cancel) => {
  const pending = pendingRead();
  const { input, requests, unexpected } = await openComposer(pending);
  fireEvent.click(screen.getAllByTitle("Edit")[0]);
  compose(input, "saved first idea");
  submitComposer(input);
  if (cancel === "Escape") fireEvent.keyDown(input, { key: "Escape" });
  else fireEvent.click(screen.getByRole("button", { name: cancel }));
  compose(input, "a separate new draft");
  await act(async () => pending.resolve(Response.json({ status: "ok", message_id: OWN_MESSAGES[0].id })));

  expect(await screen.findByText("saved first idea")).toBeTruthy();
  expect(input.value).toBe("a separate new draft");
  expect(input.placeholder).toBe("Message the agents...");
  expect(requests).toEqual([{ method: "PATCH", path: `/api/v1/chat/human-messages/${OWN_MESSAGES[0].id}`, body: { content: "saved first idea" } }]);
  expect(unexpected).toEqual([]);
});

it.each([0, 1])("keeps a fresh edit context for message %i after an earlier PATCH completes", async (nextIndex) => {
  const pending = pendingRead();
  const { input, unexpected } = await openComposer(pending);
  fireEvent.click(screen.getAllByTitle("Edit")[0]);
  compose(input, "submitted edit");
  submitComposer(input);
  fireEvent.keyDown(input, { key: "Escape" });
  fireEvent.click(screen.getAllByTitle("Edit")[nextIndex]);
  compose(input, "another revision");
  compose(input, "submitted edit");
  await act(async () => pending.resolve(Response.json({ status: "ok", message_id: OWN_MESSAGES[0].id })));

  expect(screen.getByText("submitted edit", { selector: "p" })).toBeTruthy();
  expect(input.value).toBe("submitted edit");
  expect(input.placeholder).toBe("Edit your message...");
  expect(screen.getByText("Editing message")).toBeTruthy();
  expect(unexpected).toEqual([]);
});

it.each(["POST", "PATCH"])("clears the unchanged composer after successful %s", async (method) => {
  const pending = pendingRead();
  const { input, requests, unexpected } = await openComposer(pending);
  if (method === "PATCH") fireEvent.click(screen.getAllByTitle("Edit")[0]);
  compose(input, "ordinary submission");
  submitComposer(input);
  const form = input.closest("form");
  if (!form) throw new Error("Composer form missing");
  const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]');
  if (!submit) throw new Error("Composer submit missing");
  expect(submit.disabled).toBe(true);
  await act(async () => pending.resolve(Response.json({ status: "ok", message_id: OWN_MESSAGES[0].id })));

  expect(input.value).toBe("");
  expect(input.placeholder).toBe("Message the agents...");
  expect(requests[0].method).toBe(method);
  if (method === "PATCH") expect(screen.getByText("ordinary submission")).toBeTruthy();
  expect(unexpected).toEqual([]);
});

it.each([
  { method: "POST", failure: "http" }, { method: "POST", failure: "network" },
  { method: "PATCH", failure: "http" }, { method: "PATCH", failure: "network" },
])("retains the active draft after $method $failure failure", async ({ method, failure }) => {
  const pending = pendingRead();
  const { input, unexpected } = await openComposer(pending);
  if (method === "PATCH") fireEvent.click(screen.getAllByTitle("Edit")[0]);
  compose(input, "retry this draft");
  submitComposer(input);
  await act(async () => {
    if (failure === "network") pending.reject(new TypeError("Synthetic network failure"));
    else pending.resolve(Response.json({ detail: "Synthetic write rejected" }, { status: 503 }));
  });

  expect(input.value).toBe("retry this draft");
  expect(await screen.findByText(failure === "network" ? "Network error" : "Synthetic write rejected")).toBeTruthy();
  expect(input.placeholder).toBe(method === "PATCH" ? "Edit your message..." : "Message the agents...");
  expect(unexpected).toEqual([]);
});

it.each(["http", "network"])("does not attach stale PATCH %s errors to a new edit", async (failure) => {
  const pending = pendingRead();
  const { input, unexpected } = await openComposer(pending);
  fireEvent.click(screen.getAllByTitle("Edit")[0]);
  compose(input, "earlier submission");
  submitComposer(input);
  fireEvent.keyDown(input, { key: "Escape" });
  fireEvent.click(screen.getAllByTitle("Edit")[1]);
  compose(input, "new edit draft");
  await act(async () => {
    if (failure === "network") pending.reject(new TypeError("Synthetic network failure"));
    else pending.resolve(Response.json({ detail: "Stale edit error" }, { status: 503 }));
  });

  expect(input.value).toBe("new edit draft");
  expect(screen.queryByText("Network error")).toBeNull();
  expect(screen.queryByText("Stale edit error")).toBeNull();
  expect(unexpected).toEqual([]);
});

it.each([
  { method: "POST", failure: "http" }, { method: "POST", failure: "network" },
  { method: "PATCH", failure: "http" }, { method: "PATCH", failure: "network" },
])("reports $method $failure failure after typing a new draft in the same context", async ({ method, failure }) => {
  const pending = pendingRead();
  const { input, unexpected } = await openComposer(pending);
  if (method === "PATCH") fireEvent.click(screen.getAllByTitle("Edit")[0]);
  compose(input, "submitted draft");
  submitComposer(input);
  compose(input, "a new draft in this context");
  await act(async () => {
    if (failure === "network") pending.reject(new TypeError("Synthetic network failure"));
    else pending.resolve(Response.json({ detail: "The previous write failed" }, { status: 503 }));
  });

  expect(input.value).toBe("a new draft in this context");
  expect(await screen.findByText(failure === "network" ? "Network error" : "The previous write failed")).toBeTruthy();
  const form = input.closest("form");
  if (!form) throw new Error("Composer form missing");
  const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]');
  if (!submit) throw new Error("Composer submit missing");
  expect(submit.disabled).toBe(false);
  expect(unexpected).toEqual([]);
});

const OPERATION_ERROR = {
  en: { older: "Could not load older messages.", remove: "Could not delete message." },
  ru: { older: "Не удалось загрузить предыдущие сообщения.", remove: "Не удалось удалить сообщение." },
};
const OPERATION_ROWS = [...HISTORY_PAGE.slice(0, 48), ...[...OWN_MESSAGES].reverse()];

async function openOperationChat(locale: "en" | "ru") {
  localStorage.setItem("access_token", "synthetic-operation-access");
  const stream = stubInitialRead(async () => Response.json([]));
  const older: ReturnType<typeof pendingRead>[] = [];
  const deletes: { id: string; pending: ReturnType<typeof pendingRead> }[] = [];
  const unexpected: string[] = [];
  const routes = new Map<string, () => Promise<Response>>([
    ["GET /api/v1/auth/me", async () => Response.json(COMPOSER_OWNER)],
    ["GET /api/v1/chat/messages?limit=50", async () => Response.json(OPERATION_ROWS)],
    [`GET /api/v1/chat/messages?limit=50&before=${OWN_MESSAGES[0].id}`, () => {
      const pending = pendingRead(); older.push(pending); return pending.promise;
    }],
  ]);
  for (const message of OWN_MESSAGES) routes.set(`DELETE /api/v1/chat/human-messages/${message.id}`, () => {
    const pending = pendingRead(); deletes.push({ id: message.id, pending }); return pending.promise;
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    const url = new URL(input), key = `${init?.method ?? "GET"} ${url.pathname}${url.search}`;
    const respond = routes.get(key);
    if (url.origin === new URL(API_URL).origin && respond
      && new Headers(init?.headers).get("Authorization") === "Bearer synthetic-operation-access") return respond();
    unexpected.push(key);
    return Promise.reject(new Error("Unexpected operation request"));
  }));
  render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);
  const input = await screen.findByRole<HTMLTextAreaElement>("textbox");
  await screen.findByText(OWN_MESSAGES[0].content);
  compose(input, "an unrelated draft");
  return { input, older, deletes, unexpected, stream };
}

async function rejectOperation(pending: ReturnType<typeof pendingRead>, failure: string) {
  await act(async () => {
    if (failure === "network") pending.reject(new TypeError("Synthetic operation failure"));
    else pending.resolve(Response.json({ detail: "Synthetic operation failure" }, { status: 503 }));
  });
}

it.each([
  { locale: "en", failure: "http" }, { locale: "en", failure: "network" },
  { locale: "ru", failure: "http" }, { locale: "ru", failure: "network" },
] as const)("retains history and draft with retry after older $failure failure in $locale", async ({ locale, failure }) => {
  const chat = await openOperationChat(locale);
  fireEvent.scroll(screen.getByRole("main"));
  expect(chat.older).toHaveLength(1);
  await rejectOperation(chat.older[0], failure);
  expect(await screen.findByText(OPERATION_ERROR[locale].older)).toBeTruthy();
  expect(screen.getByText(OWN_MESSAGES[0].content)).toBeTruthy();
  expect(chat.input.value).toBe("an unrelated draft");
  const retry = screen.getByRole("button", { name: sharedMessages[locale].retry });
  await act(async () => { fireEvent.click(retry); fireEvent.click(retry); fireEvent.scroll(screen.getByRole("main")); });
  expect(chat.older).toHaveLength(2);
  await act(async () => chat.older[1].resolve(Response.json([OLDER_MESSAGE])));
  expect(await screen.findByText(OLDER_MESSAGE.content)).toBeTruthy();
  expect(screen.queryByText(OPERATION_ERROR[locale].older)).toBeNull();
  expect(chat.input.value).toBe("an unrelated draft");
  expect(chat.unexpected).toEqual([]);
});

it.each(["edit", "delete"] as const)("preserves unseen older SSE %s, new messages and known rows once", async (mutation) => {
  const chat = await openOperationChat("en");
  fireEvent.scroll(screen.getByRole("main"));
  await act(async () => {
    chat.stream.emit(LIVE_MESSAGE);
    chat.stream.emit(mutation === "edit" ? { type: "edit", id: OLDER_MESSAGE.id, content: "Updated older idea" }
      : { type: "delete", id: OLDER_MESSAGE.id });
    chat.older[0].resolve(Response.json([OWN_MESSAGES[0], OLDER_MESSAGE]));
  });
  expect(await screen.findByText(mutation === "edit" ? "Updated older idea" : "[deleted]")).toBeTruthy();
  expect(screen.queryByText(OLDER_MESSAGE.content)).toBeNull();
  expect(screen.getAllByText(OWN_MESSAGES[0].content)).toHaveLength(1);
  expect(screen.getByText(LIVE_MESSAGE.content)).toBeTruthy();
  expect(chat.input.value).toBe("an unrelated draft");
  expect(chat.unexpected).toEqual([]);
});

it.each([
  { locale: "en", failure: "http" }, { locale: "en", failure: "network" },
  { locale: "ru", failure: "http" }, { locale: "ru", failure: "network" },
] as const)("retains message and draft with retry after DELETE $failure in $locale", async ({ locale, failure }) => {
  const chat = await openOperationChat(locale);
  const remove = screen.getAllByTitle(locale === "en" ? "Delete" : "Удалить")[0];
  fireEvent.click(remove);
  expect(chat.deletes).toHaveLength(1);
  expect(screen.getByText(OWN_MESSAGES[0].content)).toBeTruthy();
  await rejectOperation(chat.deletes[0].pending, failure);
  expect(await screen.findByText(OPERATION_ERROR[locale].remove)).toBeTruthy();
  expect(chat.input.value).toBe("an unrelated draft");
  const retry = screen.getByRole("button", { name: sharedMessages[locale].retry });
  await act(async () => { fireEvent.click(retry); fireEvent.click(retry); });
  expect(chat.deletes).toHaveLength(2);
  await act(async () => chat.deletes[1].pending.resolve(Response.json({ status: "ok" })));
  expect(await screen.findByText("[deleted]")).toBeTruthy();
  expect(screen.queryByText(OWN_MESSAGES[0].content)).toBeNull();
  expect(screen.queryByText(OPERATION_ERROR[locale].remove)).toBeNull();
  expect(chat.input.value).toBe("an unrelated draft");
  expect(chat.unexpected).toEqual([]);
});

it("keeps another message deletion error when the first retry succeeds", async () => {
  const chat = await openOperationChat("en");
  const remove = screen.getAllByTitle("Delete");
  fireEvent.click(remove[0]); fireEvent.click(remove[1]);
  expect(chat.deletes.map(request => request.id)).toEqual(OWN_MESSAGES.map(message => message.id));
  await rejectOperation(chat.deletes[0].pending, "http");
  await rejectOperation(chat.deletes[1].pending, "network");
  expect(await screen.findAllByText(OPERATION_ERROR.en.remove)).toHaveLength(2);
  const alerts = screen.getAllByRole("alert");
  fireEvent.click(within(alerts[0]).getByRole("button", { name: sharedMessages.en.retry }));
  await act(async () => chat.deletes[2].pending.resolve(Response.json({ status: "ok" })));
  expect(screen.getAllByText(OPERATION_ERROR.en.remove)).toHaveLength(1);
  expect(screen.getByText(OWN_MESSAGES[1].content)).toBeTruthy();
  expect(screen.getByText("[deleted]")).toBeTruthy();
  expect(chat.input.value).toBe("an unrelated draft");
  expect(chat.unexpected).toEqual([]);
});

it.each(["older", "delete"])("suppresses same-tick duplicate %s requests", async (operation) => {
  const chat = await openOperationChat("en");
  await act(async () => {
    if (operation === "older") {
      fireEvent.scroll(screen.getByRole("main")); fireEvent.scroll(screen.getByRole("main"));
    } else {
      const remove = screen.getAllByTitle("Delete")[0];
      fireEvent.click(remove); fireEvent.click(remove);
    }
  });
  expect(operation === "older" ? chat.older : chat.deletes).toHaveLength(1);
  const pending = operation === "older" ? chat.older[0] : chat.deletes[0].pending;
  await act(async () => pending.resolve(Response.json(operation === "older" ? [] : { status: "ok" })));
  expect(chat.unexpected).toEqual([]);
});

it.each(["pending", "failed"])("keeps a %s deletion identity when older messages change the owning group key", async (status) => {
  const chat = await openOperationChat("en");
  fireEvent.click(screen.getAllByTitle("Delete")[0]);
  if (status === "failed") await rejectOperation(chat.deletes[0].pending, "http");
  fireEvent.scroll(screen.getByRole("main"));
  const prepend = { ...OWN_MESSAGES[0], id: "earlier-own", content: "Earlier owned idea", ts: "2025-12-31T23:59:00Z" };
  await act(async () => chat.older[0].resolve(Response.json([prepend])));
  await screen.findByText(prepend.content);
  const row = screen.getByText(OWN_MESSAGES[0].content).closest(".group");
  if (!(row instanceof HTMLElement)) throw new Error("Owning message row missing");
  if (status === "failed") {
    expect(within(row).getByText(OPERATION_ERROR.en.remove)).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: sharedMessages.en.retry }));
  } else fireEvent.click(within(row).getByTitle("Delete"));
  expect(chat.deletes).toHaveLength(status === "failed" ? 2 : 1);
  await act(async () => chat.deletes[chat.deletes.length - 1].pending.resolve(Response.json({ status: "ok" })));
  expect(await screen.findByText("[deleted]")).toBeTruthy();
  expect(chat.unexpected).toEqual([]);
});

const SECURITY_OWNER = { ...COMPOSER_OWNER, id: "account-a", name: "Shared name" };
const SECURITY_OTHER = { ...SECURITY_OWNER, id: "account-b" };
const SECURITY_ROWS = [
  { id: "security-a", sender_user_id: SECURITY_OWNER.id },
  { id: "security-b", sender_user_id: SECURITY_OTHER.id },
  { id: "security-null", sender_user_id: null },
  { id: "security-missing" },
].map(author => ({ ...MESSAGE, ...author, agent_id: null, content: author.id,
  agent_name: SECURITY_OWNER.name, specialization: "user", sender_type: "user", is_deleted: false }));

function publicSecuritySession(token: string | null) {
  if (token) localStorage.setItem("access_token", token);
  else localStorage.removeItem("access_token");
  window.dispatchEvent(new Event("storage"));
}

function stubPublicSecurity(profile: (authorization: string | null) => Promise<Response>) {
  stubChat([]);
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    const url = new URL(input);
    if (url.origin !== new URL(API_URL).origin) throw new Error("Unexpected security origin");
    if (url.pathname === "/api/v1/auth/me") return profile(new Headers(init?.headers).get("Authorization"));
    if (url.pathname === "/api/v1/chat/messages") return Promise.resolve(Response.json([...SECURITY_ROWS].reverse()));
    throw new Error(`Unexpected security request: ${url.pathname}`);
  }));
}

function publicSecurityControls(id: string, owned: boolean, locale: "en" | "ru" = "en") {
  const row = screen.getByText(id).closest("[data-message-id]");
  if (!(row instanceof HTMLElement)) throw new Error("Security message row missing");
  for (const title of locale === "ru" ? ["Изменить", "Удалить"] : ["Edit", "Delete"]) expect(within(row).queryAllByTitle(title)).toHaveLength(owned ? 1 : 0);
}

it.each(["Shared name", "Renamed account"])("SEC.CHAT public grants only confirmed ID ownership with profile name %s", async name => {
  const payload = btoa(JSON.stringify({ sub: SECURITY_OTHER.id, email: "Shared name@example.invalid" }));
  publicSecuritySession(`synthetic.${payload}.fixture`);
  stubPublicSecurity(async () => Response.json({ ...SECURITY_OWNER, name }));
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);
  await screen.findByText("security-a");
  await waitFor(() => publicSecurityControls("security-a", true));
  for (const id of ["security-b", "security-null", "security-missing"]) publicSecurityControls(id, false);
  expect(within(screen.getByRole("main")).getAllByText(SECURITY_OWNER.name)).toHaveLength(4);
});

it("SEC.CHAT public removes old privileges while replacement profile is pending", async () => {
  const replacement = pendingRead();
  publicSecuritySession("synthetic-security-a");
  stubPublicSecurity(authorization => authorization === "Bearer synthetic-security-a"
    ? Promise.resolve(Response.json(SECURITY_OWNER)) : replacement.promise.then(response => response.clone()));
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);
  await screen.findByText("security-a");
  await waitFor(() => publicSecurityControls("security-a", true));
  await act(async () => publicSecuritySession("synthetic-security-b"));
  publicSecurityControls("security-a", false);
  publicSecurityControls("security-b", false);
  await act(async () => replacement.resolve(Response.json(SECURITY_OTHER)));
  await waitFor(() => publicSecurityControls("security-b", true));
  publicSecurityControls("security-a", false);
  await act(async () => publicSecuritySession(null));
  publicSecurityControls("security-b", false);
});

it.each(["switch", "logout"])("SEC.CHAT public ignores delayed old profile after %s", async transition => {
  const stale = pendingRead();
  publicSecuritySession("synthetic-security-a");
  const profiles = vi.fn((authorization: string | null) => authorization === "Bearer synthetic-security-a"
    ? stale.promise.then(response => response.clone()) : Promise.resolve(Response.json(SECURITY_OTHER)));
  stubPublicSecurity(profiles);
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);
  await screen.findByText("security-a");
  await waitFor(() => expect(profiles).toHaveBeenCalled());
  publicSecurityControls("security-a", false);
  await act(async () => publicSecuritySession(transition === "switch" ? "synthetic-security-b" : null));
  if (transition === "switch") await waitFor(() => publicSecurityControls("security-b", true));
  await act(async () => stale.resolve(Response.json(SECURITY_OWNER)));
  publicSecurityControls("security-a", false);
  publicSecurityControls("security-b", transition === "switch");
});

async function openIdentityComposer() {
  const pending = pendingRead();
  const writes: { method: string; path: string; authorization: string | null; content: string }[] = [];
  publicSecuritySession("synthetic-security-a");
  stubPublicSecurity(authorization => authorization === "Bearer synthetic-security-a"
    ? Promise.resolve(Response.json(SECURITY_OWNER)) : pending.promise.then(response => response.clone()));
  const read = fetch;
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
    if (init?.method === "POST" || init?.method === "PATCH") {
      writes.push({ method: init.method, path: new URL(input).pathname,
        authorization: new Headers(init.headers).get("Authorization"), content: JSON.parse(String(init.body)).content });
      return Promise.resolve(Response.json({ status: "ok", message_id: "synthetic-composer-result" }));
    }
    return read(input, init);
  }));
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);
  const input = await screen.findByRole<HTMLTextAreaElement>("textbox");
  await waitFor(() => publicSecurityControls("security-a", true));
  return { input, pending, writes };
}

it("SEC.CHAT composer clears A editor and draft only after B profile confirms", async () => {
  const chat = await openIdentityComposer();
  fireEvent.click(screen.getByTitle("Edit"));
  compose(chat.input, "A amended draft");
  await act(async () => publicSecuritySession("synthetic-security-b"));
  expect(screen.queryByText("Editing message")).toBeNull();
  submitComposer(chat.input);
  expect(chat.writes).toEqual([]);
  await act(async () => chat.pending.resolve(Response.json(SECURITY_OTHER)));
  const nextInput = await screen.findByRole<HTMLTextAreaElement>("textbox");
  expect(screen.queryByText("Editing message")).toBeNull();
  expect(nextInput.value).toBe("");
  expect(nextInput.placeholder).toBe("Message the agents...");
  compose(nextInput, "B new draft");
  await act(async () => submitComposer(nextInput));
  expect(chat.writes).toEqual([{ method: "POST", path: "/api/v1/chat/human-message",
    authorization: "Bearer synthetic-security-b", content: "B new draft" }]);
});

it.each(["draft", "edit"])("SEC.CHAT composer preserves %s across pending same-account profile refresh", async mode => {
  const chat = await openIdentityComposer();
  if (mode === "edit") fireEvent.click(screen.getByTitle("Edit"));
  compose(chat.input, "Unsaved same-account revision");
  await act(async () => publicSecuritySession("synthetic-refreshed-security-a"));
  expect(screen.queryByText("Editing message")).toBeNull();
  const visibleInput = screen.queryByRole<HTMLTextAreaElement>("textbox");
  expect(visibleInput === null || visibleInput.disabled).toBe(true);
  submitComposer(chat.input);
  expect(chat.writes).toEqual([]);
  await act(async () => chat.pending.resolve(Response.json(SECURITY_OWNER)));
  const restored = await screen.findByRole<HTMLTextAreaElement>("textbox");
  expect(restored.value).toBe("Unsaved same-account revision");
  expect(restored.placeholder).toBe(mode === "edit" ? "Edit your message..." : "Message the agents...");
  await act(async () => submitComposer(restored));
  expect(chat.writes).toEqual([{ method: mode === "edit" ? "PATCH" : "POST",
    path: mode === "edit" ? "/api/v1/chat/human-messages/security-a" : "/api/v1/chat/human-message",
    authorization: "Bearer synthetic-refreshed-security-a", content: "Unsaved same-account revision" }]);
});

const COMPOSER_LABELS = {
  en: { message: "Message", send: "Send", save: "Save" },
  ru: { message: "Сообщение", send: "Отправить", save: "Сохранить" },
};

it.each(["en", "ru"] as const)("CH.3 names the public composer and send/save actions in %s", async locale => {
  const { input } = await openComposer(pendingRead(), locale);
  const labels = COMPOSER_LABELS[locale];
  expect(screen.getByRole("textbox", { name: labels.message })).toBe(input);
  expect(screen.getByRole("button", { name: labels.send })).toBeTruthy();
  fireEvent.click(screen.getAllByTitle(communicationMessages[locale].Edit)[0]);
  expect(screen.getByRole("textbox", { name: labels.message })).toBe(input);
  expect(screen.getByRole("button", { name: labels.save })).toBeTruthy();
  expect(screen.queryByRole("button", { name: labels.send })).toBeNull();
});

it.each(["en", "ru"] as const)("CH.4 exposes the selected filter and restores loaded chat in %s", async locale => {
  stubChat([MESSAGE]);
  render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);
  await screen.findByText(MESSAGE.content);
  const all = screen.getByRole("button", { name: new RegExp(communicationMessages[locale].All) });
  const idea = screen.getByRole("button", { name: new RegExp(communicationMessages[locale].Idea) });
  expect(all.getAttribute("aria-pressed")).toBe("true");
  expect(idea.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(idea);
  expect(idea.getAttribute("aria-pressed")).toBe("true");
  expect(all.getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByText(docsMessages[locale].eChatFiltered)).toBeTruthy();
  expect(screen.queryByText(docsMessages[locale].eChatTitle)).toBeNull();
  fireEvent.click(all);
  expect(screen.getByText(MESSAGE.content)).toBeTruthy();
  expect(screen.queryByText(docsMessages[locale].eChatFiltered)).toBeNull();
  expect(all.getAttribute("aria-pressed")).toBe("true");
});

it.each(["Escape", "Cancel (Esc)"])("CH.3 returns focus to the composer after %s", async action => {
  const { input, requests } = await openComposer(pendingRead());
  fireEvent.click(screen.getAllByTitle("Edit")[0]);
  compose(input, "An amended accessible draft");
  if (action === "Escape") fireEvent.keyDown(input, { key: "Escape" });
  else {
    const cancel = screen.getByRole("button", { name: action });
    cancel.focus();
    fireEvent.click(cancel);
  }
  expect(screen.queryByText("Editing message")).toBeNull();
  expect(input.value).toBe("");
  expect(document.activeElement).toBe(input);
  expect(requests).toEqual([]);
});

const PROFILE_COPY = {
  en: { checking: "Checking your account…", error: "Could not verify your account." },
  ru: { checking: "Проверяем аккаунт…", error: "Не удалось проверить аккаунт." },
};

async function openProfileState(locale: "en" | "ru" = "en") {
  const requests = { current: pendingRead() };
  publicSecuritySession("synthetic-profile-a");
  const profiles = vi.fn(() => requests.current.promise.then(response => response.clone()));
  stubPublicSecurity(profiles);
  const view = render(<LocaleProvider initialLocale={locale}><ChatPage /></LocaleProvider>);
  await act(async () => {});
  screen.getByText("security-a");
  return { requests, profiles, view };
}

it.each(["en", "ru"] as const)("CH.4b public distinguishes checking from signed-out in %s", async locale => {
  await openProfileState(locale);
  expect(screen.getByText(PROFILE_COPY[locale].checking).closest('[role="status"]')).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  publicSecurityControls("security-a", false, locale);
  await act(async () => publicSecuritySession(null));
  expect(screen.queryByText(PROFILE_COPY[locale].checking)).toBeNull();
  expect(screen.getByRole("link", { name: locale === "en" ? "Sign in to join the conversation" : "Войдите, чтобы участвовать в разговоре" })).toBeTruthy();
});

it.each([
  { locale: "en", failure: "http" }, { locale: "ru", failure: "http" },
  { locale: "en", failure: "network" }, { locale: "ru", failure: "network" },
  { locale: "en", failure: "json" }, { locale: "ru", failure: "json" },
  { locale: "en", failure: "schema" }, { locale: "ru", failure: "schema" },
] as const)("CH.4b public retries $failure profile verification truthfully in $locale", async ({ locale, failure }) => {
  const chat = await openProfileState(locale);
  await act(async () => {
    if (failure === "network") chat.requests.current.reject(new TypeError("Synthetic profile network failure"));
    else if (failure === "json") chat.requests.current.resolve(new Response("{invalid"));
    else if (failure === "schema") chat.requests.current.resolve(Response.json({ ...SECURITY_OWNER, id: null }));
    else chat.requests.current.resolve(Response.json({ detail: "Synthetic unavailable profile" }, { status: 503 }));
  });
  const alert = screen.getByText(PROFILE_COPY[locale].error).closest('[role="alert"]');
  if (!(alert instanceof HTMLElement)) throw new Error("Profile failure alert missing");
  expect(screen.queryByRole("textbox")).toBeNull();
  publicSecurityControls("security-a", false, locale);
  const beforeRetry = chat.profiles.mock.calls.length;
  chat.requests.current = pendingRead();
  fireEvent.click(within(alert).getByRole("button", { name: sharedMessages[locale].retry }));
  await act(async () => {});
  expect(chat.profiles).toHaveBeenCalledTimes(beforeRetry + 1);
  expect(screen.getByText(PROFILE_COPY[locale].checking).closest('[role="status"]')).toBeTruthy();
  expect(screen.queryByText(PROFILE_COPY[locale].error)).toBeNull();
  await act(async () => chat.requests.current.resolve(Response.json(SECURITY_OWNER)));
  expect(screen.getByRole("textbox")).toBeTruthy();
  publicSecurityControls("security-a", true, locale);
  expect(screen.queryByText(PROFILE_COPY[locale].checking)).toBeNull();
  expect(screen.queryByText(PROFILE_COPY[locale].error)).toBeNull();
});

it.each([
  { locale: "en", late: "success" }, { locale: "ru", late: "success" },
  { locale: "en", late: "failure" }, { locale: "ru", late: "failure" },
] as const)("CH.4b public times out then ignores stale $late during retry in $locale", async ({ locale, late }) => {
  vi.useFakeTimers();
  const chat = await openProfileState(locale), old = chat.requests.current;
  await act(async () => vi.advanceTimersByTimeAsync(9_999));
  expect(screen.getByText(PROFILE_COPY[locale].checking)).toBeTruthy();
  await act(async () => vi.advanceTimersByTimeAsync(1));
  const alert = screen.getByText(PROFILE_COPY[locale].error).closest('[role="alert"]');
  if (!(alert instanceof HTMLElement)) throw new Error("Profile timeout alert missing");
  const signals = vi.mocked(fetch).mock.calls.filter(([input]) => String(input).endsWith("/auth/me")).map(([, init]) => init?.signal);
  expect(signals.some(signal => signal?.aborted)).toBe(true);
  chat.requests.current = pendingRead();
  fireEvent.click(within(alert).getByRole("button", { name: sharedMessages[locale].retry }));
  await act(async () => {
    if (late === "success") old.resolve(Response.json(SECURITY_OWNER));
    else old.reject(new TypeError("Synthetic delayed profile failure"));
  });
  expect(screen.getByText(PROFILE_COPY[locale].checking)).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  await act(async () => chat.requests.current.resolve(Response.json(SECURITY_OWNER)));
  publicSecurityControls("security-a", true, locale);
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(screen.queryByText(PROFILE_COPY[locale].error)).toBeNull();
  expect(screen.getByRole("textbox")).toBeTruthy();
});

it.each(["success", "failure", "timeout"])("CH.4b public ignores old %s after account replacement", async outcome => {
  vi.useFakeTimers();
  const chat = await openProfileState(), old = chat.requests.current;
  chat.requests.current = pendingRead();
  await act(async () => publicSecuritySession("synthetic-profile-b"));
  await act(async () => chat.requests.current.resolve(Response.json(SECURITY_OTHER)));
  await act(async () => {
    if (outcome === "success") old.resolve(Response.json(SECURITY_OWNER));
    else if (outcome === "failure") old.reject(new TypeError("Synthetic previous account failure"));
    else await vi.advanceTimersByTimeAsync(10_000);
  });
  publicSecurityControls("security-a", false); publicSecurityControls("security-b", true);
  expect(screen.queryByText(PROFILE_COPY.en.error)).toBeNull();
  expect(screen.getByRole("textbox")).toBeTruthy();
});

it("CH.4b public aborts verification and its deadline after unmount", async () => {
  vi.useFakeTimers();
  const chat = await openProfileState();
  const signals = vi.mocked(fetch).mock.calls.filter(([input]) => String(input).endsWith("/auth/me")).map(([, init]) => init?.signal);
  expect(signals.length).toBeGreaterThan(0);
  chat.view.unmount();
  expect(signals.every(signal => signal?.aborted)).toBe(true);
  const requests = vi.mocked(fetch).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); chat.requests.current.resolve(Response.json(SECURITY_OWNER)); });
  expect(fetch).toHaveBeenCalledTimes(requests);
});

it.each(["draft", "edit"])("CH.4b public retains %s through same-account error and retry", async mode => {
  const chat = await openProfileState();
  await act(async () => chat.requests.current.resolve(Response.json(SECURITY_OWNER)));
  const input = screen.getByRole<HTMLTextAreaElement>("textbox");
  if (mode === "edit") fireEvent.click(screen.getAllByTitle("Edit")[0]);
  fireEvent.change(input, { target: { value: "A revision retained through profile failure" } });
  chat.requests.current = pendingRead();
  await act(async () => publicSecuritySession("synthetic-profile-a-refreshed"));
  await act(async () => chat.requests.current.resolve(Response.json({}, { status: 503 })));
  const alert = screen.getByText(PROFILE_COPY.en.error).closest('[role="alert"]');
  if (!(alert instanceof HTMLElement)) throw new Error("Profile retry alert missing");
  chat.requests.current = pendingRead();
  fireEvent.click(within(alert).getByRole("button", { name: sharedMessages.en.retry }));
  await act(async () => chat.requests.current.resolve(Response.json(SECURITY_OWNER)));
  expect(screen.getByRole<HTMLTextAreaElement>("textbox").value).toBe("A revision retained through profile failure");
  expect(screen.queryByText("Editing message") !== null).toBe(mode === "edit");
});
