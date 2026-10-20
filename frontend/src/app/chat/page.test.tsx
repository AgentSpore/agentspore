// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import ChatPage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";
import { communicationMessages } from "@/lib/i18n/communication";
import { sharedMessages } from "@/lib/i18n/shared";

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
  const promise = new Promise<Response>((complete) => { resolve = complete; });
  return { promise, resolve };
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
