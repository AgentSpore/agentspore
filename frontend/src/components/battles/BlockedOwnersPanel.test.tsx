// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { API_URL, type BattleBlockResponse } from "@/lib/api";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { battlesMessages } from "@/lib/i18n/battles";
import { BlockedOwnersPanel } from "./BlockedOwnersPanel";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
const endpoint = `${API_URL}/api/v1/battles/blocks`;
const blocks: BattleBlockResponse[] = [
  { id: "00000000-0000-4000-8000-000000000001", blocked_owner_id: "00000000-0000-4000-8000-000000000011", created_at: "2026-01-02T00:00:00Z" },
  { id: "00000000-0000-4000-8000-000000000002", blocked_owner_id: "00000000-0000-4000-8000-000000000012", created_at: "2026-01-01T00:00:00Z" },
];
const pending = new Map<string, (response: Response) => void>();

beforeEach(() => {
  const storage = new Map<string, string>([["access_token", "synthetic-blocks-session"]]);
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value); },
    removeItem: (key: string) => { storage.delete(key); },
  });
  vi.stubGlobal("fetch", vi.fn((url: RequestInfo | URL, options?: RequestInit) => {
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer synthetic-blocks-session");
    if (String(url) === endpoint && !options?.method) return Promise.resolve(Response.json(blocks));
    const block = blocks.find(row => String(url) === `${endpoint}/${row.id}`);
    if (block && options?.method === "DELETE") {
      expect(options.body).toBeUndefined();
      return new Promise<Response>(resolve => { pending.set(block.id, resolve); });
    }
    throw new Error("Unexpected synthetic block request");
  }));
});
afterEach(() => { cleanup(); pending.clear(); vi.unstubAllGlobals(); });

async function mount(locale: "en" | "ru" = "en") {
  render(<LocaleProvider initialLocale={locale}><BlockedOwnersPanel /></LocaleProvider>);
  await screen.findByText(blocks[0].blocked_owner_id);
  await screen.findByText(blocks[1].blocked_owner_id);
}

function unblock(index: number, locale: "en" | "ru" = "en") {
  const row = screen.getByText(blocks[index].blocked_owner_id).parentElement?.parentElement;
  if (!row) throw new Error("Synthetic block row missing");
  fireEvent.click(within(row).getByRole("button", { name: battlesMessages[locale].unblock }));
  expect(screen.queryByText(blocks[index].blocked_owner_id)).toBeNull();
  expect(pending.has(blocks[index].id)).toBe(true);
}

async function settle(index: number, response: Response) {
  const respond = pending.get(blocks[index].id);
  if (!respond) throw new Error("Synthetic DELETE was not started");
  await act(async () => { respond(response); });
}

it.each(["success-first", "failure-first"] as const)(
  "does not resurrect a successfully removed owner after concurrent rollback (%s)", async order => {
    await mount();
    unblock(0);
    unblock(1);
    expect(pending.size).toBe(2);
    if (order === "success-first") {
      await settle(1, new Response(null, { status: 204 }));
      await settle(0, new Response(null, { status: 503 }));
    } else {
      await settle(0, new Response(null, { status: 503 }));
      await settle(1, new Response(null, { status: 204 }));
    }
    expect(screen.getByText(blocks[0].blocked_owner_id)).toBeTruthy();
    expect(screen.queryByText(blocks[1].blocked_owner_id)).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe(battlesMessages.en["failed to remove the block"]);
  },
);

it("restores only the failed row and translates an existing known failure in Russian", async () => {
  await mount("ru");
  unblock(0, "ru");
  await settle(0, Response.json({ detail: "failed to remove the block" }, { status: 503 }));
  expect(screen.getByText(blocks[0].blocked_owner_id)).toBeTruthy();
  expect(screen.getByText(blocks[1].blocked_owner_id)).toBeTruthy();
  expect(screen.getByText(battlesMessages.ru["failed to remove the block"])).toBeTruthy();
  expect(screen.getAllByText(/^00000000-0000-4000-8000-00000000001[12]$/).map(row => row.textContent))
    .toEqual(blocks.map(row => row.blocked_owner_id));
});

it.each([204, 404])("keeps an optimistically removed row absent after HTTP %s", async status => {
  await mount();
  unblock(0);
  const response = status === 204 ? new Response(null, { status }) : Response.json({ detail: "block not found" }, { status });
  await settle(0, response);
  expect(screen.queryByText(blocks[0].blocked_owner_id)).toBeNull();
  expect(screen.getByText(blocks[1].blocked_owner_id)).toBeTruthy();
  expect(screen.queryByText("block not found")).toBeNull();
});
