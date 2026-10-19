// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ChatPage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";
import { communicationMessages } from "@/lib/i18n/communication";

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
