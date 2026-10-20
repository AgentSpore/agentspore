// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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
  specialization: "user", sender_type: "user", is_deleted: false, content,
}));

async function openComposer(pending: ReturnType<typeof pendingRead>) {
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
  render(<LocaleProvider initialLocale="en"><ChatPage /></LocaleProvider>);
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
