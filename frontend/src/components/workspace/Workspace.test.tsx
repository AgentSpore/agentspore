// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Workspace } from "./Workspace";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { LanguageSelector } from "@/components/LanguageSelector";

vi.mock("next/navigation", () => ({ usePathname: () => "/workspace", useRouter: () => ({ refresh: vi.fn() }) }));
const actor = { id: "synthetic-agent", name: "Synthetic agent", handle: null };
const counts = { awaiting_submission: 0, submitted: 1, changes_requested: 0, accepted: 0, rejected: 0, cancelled: 0 };
const workspace = { agents: [{ ...actor, specialization: "programmer", is_active: true, created_at: null, last_heartbeat: null, budget: { available: 70, reserved: 0, transferred: 30, received: 0, refunded: 0, initial_grant_applied: true } }], review_counts: counts };
const task = { id: "synthetic-task", title: "Review the document", description: null, project: { id: "synthetic-project", title: "Synthetic project" }, requester: actor, claimant: actor, viewer_roles: ["requester", "claimant"], task_status: "in_progress", review_state: "submitted", review_version: 1, relationship: "internal", created_at: "2026-10-12T10:15:00Z", learning_reservation: { amount: 30, status: "reserved" } };
const second = { ...task, id: "second-task", title: "Second task", review_state: "accepted" };
const page = { items: [task, second], total: 2, limit: 50, offset: 0 };
const detail = { ...task, result: "<script>synthetic()</script>", history: [{ actor, action: "submit", reason: "Synthetic receipt", state: "submitted", version: 1, created_at: "2026-10-12T10:16:00Z" }] };
function response(value: unknown) { return new Response(JSON.stringify(value)); }
function fixtureFetch(input: RequestInfo | URL) {
  const url = String(input);
  if (url.endsWith("/auth/me")) return Promise.resolve(new Response("", { status: 503 }));
  if (url.includes("agent-workspace")) return Promise.resolve(response(workspace));
  if (url.includes("agent-tasks?")) return Promise.resolve(response(page));
  return Promise.resolve(response(url.endsWith("second-task") ? { ...detail, ...second, result: "Second result" } : detail));
}
beforeEach(() => {
  const store = new Map<string, string>([["access_token", "synthetic-access"]]);
  vi.stubGlobal("localStorage", { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key) });
  vi.stubGlobal("fetch", vi.fn(fixtureFetch));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(["en", "ru"] as const)("reads a selected result as text, distinguishes internal work and preserves selection across locale changes from %s", async locale => {
  render(<LocaleProvider initialLocale={locale}><Workspace /><LanguageSelector /></LocaleProvider>);
  const taskButton = await screen.findByRole("button", { name: /Review the document/ });
  taskButton.focus();
  fireEvent.click(taskButton);
  expect(await screen.findByText(detail.result)).toBeTruthy();
  expect(document.querySelector("#task-detail script")).toBeNull();
  expect(screen.getByText(locale === "en" ? "Internal task" : "Внутреннее задание")).toBeTruthy();
  expect(screen.getByText(locale === "en" ? "Educational units, not money" : "Учебные единицы, не деньги")).toBeTruthy();
  const language = screen.getAllByRole("button", { name: locale === "en" ? "Русский" : "English" })[0];
  if (!language) throw new Error("Language control missing");
  fireEvent.click(language);
  expect(await screen.findByText(detail.result)).toBeTruthy();
  expect(taskButton.getAttribute("aria-pressed")).toBe("true");
});

it("keeps loading distinct from empty and recovers a failed detail without removing the list", async () => {
  let release: (value: Response) => void = () => {};
  vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-workspace") ? new Promise<Response>(resolve => { release = resolve; }) : fixtureFetch(input));
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  expect(await screen.findByText("Reading your workspace…")).toBeTruthy();
  expect(screen.queryByText("No agents linked to this account yet.")).toBeNull();
  await act(async () => release(response(workspace)));
  vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 503 }));
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Second task/ })).toBeTruthy();
  fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Try again" }));
  expect(await screen.findByText(detail.result)).toBeTruthy();
});

it("does not fetch private data anonymously and explains how to return after sign-in", async () => {
  localStorage.removeItem("access_token");
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  expect(await screen.findByRole("link", { name: "Sign in to see your agents" })).toHaveProperty("href", expect.stringContaining("/login?next=/workspace"));
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.getByText(/Its API key stays there/)).toBeTruthy();
});

it("clears prior owner data on observed logout, then reads the new account without restoring a stale result", async () => {
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText(detail.result)).toBeTruthy();
  localStorage.removeItem("access_token");
  fireEvent(window, new Event("storage"));
  expect(await screen.findByRole("link", { name: "Sign in to see your agents" })).toBeTruthy();
  expect(screen.queryByText(detail.result)).toBeNull();
  vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-workspace") ? Promise.resolve(response({ agents: [], review_counts: counts })) : fixtureFetch(input));
  localStorage.setItem("access_token", "synthetic-other-account");
  fireEvent(window, new Event("storage"));
  expect(await screen.findByText("No agents linked to this account yet.")).toBeTruthy();
  expect(screen.queryByText(detail.result)).toBeNull();
});

it("ignores an aborted older selection even when its transport resolves late", async () => {
  let release: (value: Response) => void = () => {};
  vi.mocked(fetch).mockImplementation(input => String(input).endsWith("synthetic-task") ? new Promise<Response>(resolve => { release = resolve; }) : fixtureFetch(input));
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText("Reading the selected result…")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Second task/ }));
  expect(await screen.findByText("Second result")).toBeTruthy();
  await act(async () => release(response(detail)));
  expect(screen.queryByText(detail.result)).toBeNull();
  expect(screen.getByText("Second result")).toBeTruthy();
});

it.each([503, 401])("clears stale data after a workspace refresh fails with HTTP %s", async status => {
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText(detail.result)).toBeTruthy();
  vi.mocked(fetch).mockResolvedValue(new Response("", { status }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  await waitFor(() => expect(screen.queryByText(detail.result)).toBeNull());
  expect(await screen.findByText(status === 401 ? "Your workspace requires sign-in. Return here after signing in." : "Couldn't read this data. Check your connection and try again.")).toBeTruthy();
});


it("shows malformed transport data as an error rather than an empty account", async () => {
  vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-workspace") ? Promise.resolve(response({ agents: [], review_counts: {} })) : fixtureFetch(input));
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByText("No agents linked to this account yet.")).toBeNull();
});

it("keeps unknown heartbeat separate from the active flag and leaves a grant inactive on read", async () => {
  const unactivated = { ...workspace, agents: [{ ...workspace.agents[0], budget: { available: 0, reserved: 0, transferred: 0, received: 0, refunded: 0, initial_grant_applied: false } }] };
  vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-workspace") ? Promise.resolve(response(unactivated)) : fixtureFetch(input));
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  expect(await screen.findByText("Heartbeat time unknown")).toBeTruthy();
  expect(screen.getByText("Initial grant not activated by the agent.")).toBeTruthy();
  expect(screen.queryByText("Heartbeat within 5 minutes")).toBeNull();
});

it("filters the current page and reads a later page without keeping the old selection", async () => {
  vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-tasks?") ? Promise.resolve(response(String(input).includes("offset=50") ? { items: [second], total: 51, limit: 50, offset: 50 } : { ...page, total: 51 })) : fixtureFetch(input));
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText(detail.result)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Accepted" }));
  expect(screen.queryByRole("button", { name: /Review the document/ })).toBeNull();
  expect(screen.getByRole("button", { name: /Second task/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Tasks 51–51 of 51")).toBeTruthy();
  expect(screen.queryByText(detail.result)).toBeNull();
});

it("a detail 401 removes previously readable owner panels", async () => {
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  const select = await screen.findByRole("button", { name: /Review the document/ });
  vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 401 }));
  fireEvent.click(select);
  expect(await screen.findByRole("link", { name: "Sign in to see your agents" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Your agents" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Educational units, not money" })).toBeNull();
});


it("lists completed legacy tasks without calling them requester acceptance", async () => {
  const legacy = { ...task, review_state: null, review_version: null, relationship: null, task_status: "completed", learning_reservation: null };
  vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-tasks?") ? Promise.resolve(response({ ...page, items: [legacy], total: 1 })) : String(input).endsWith("synthetic-task") ? Promise.resolve(response({ ...detail, ...legacy })) : fixtureFetch(input));
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  await screen.findByRole("button", { name: /Review the document/ });
  fireEvent.click(screen.getByRole("button", { name: "Finished" }));
  fireEvent.click(screen.getByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText("Legacy task: completion is not requester acceptance.")).toBeTruthy();
  expect(within(screen.getByRole("region", { name: "Task details" })).getByText("Completed")).toBeTruthy();
});


it.each(["en", "ru"] as const)("expands received history, collapses it and resets on a different task in %s", async locale => {
  const history = Array.from({ length: 8 }, (_, index) => ({ ...detail.history[0], version: 8 - index, reason: `Synthetic history event ${8 - index}` }));
  vi.mocked(fetch).mockImplementation(input => String(input).endsWith("synthetic-task") ? Promise.resolve(response({ ...detail, history })) : fixtureFetch(input));
  render(<LocaleProvider initialLocale={locale}><Workspace /><LanguageSelector /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText("Synthetic history event 8")).toBeTruthy();
  expect(screen.getByText("Synthetic history event 4")).toBeTruthy();
  expect(screen.queryByText("Synthetic history event 3")).toBeNull();
  const expand = screen.getByRole("button", { name: locale === "en" ? "Show all 8 received events" : "Показать все полученные события: 8" });
  expand.focus(); fireEvent.click(expand);
  expect(await screen.findByText("Synthetic history event 1")).toBeTruthy();
  expect(expand.getAttribute("aria-expanded")).toBe("true");
  const language = screen.getAllByRole("button", { name: locale === "en" ? "Русский" : "English" })[0];
  if (!language) throw new Error("Language control missing");
  fireEvent.click(language);
  const collapse = screen.getByRole("button", { name: locale === "en" ? "Показать 5 последних событий" : "Show latest 5 events" });
  fireEvent.click(collapse);
  expect(screen.queryByText("Synthetic history event 1")).toBeNull();
  expect(collapse.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(collapse);
  fireEvent.click(screen.getByRole("button", { name: /Second task/ }));
  expect(await screen.findByText("Second result")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText("Synthetic history event 8")).toBeTruthy();
  expect(screen.queryByText("Synthetic history event 1")).toBeNull();
});
