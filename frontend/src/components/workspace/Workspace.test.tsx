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
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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

it.each(["en", "ru"] as const)("signed out shows the sign-in card first and no refresh button in %s", async locale => {
  localStorage.removeItem("access_token");
  render(<LocaleProvider initialLocale={locale}><Workspace /></LocaleProvider>);
  const signIn = await screen.findByRole("link", { name: locale === "en" ? "Sign in to see your agents" : "Войти и посмотреть агентов" });
  expect(screen.queryByRole("button", { name: locale === "en" ? "Refresh workspace" : "Обновить рабочую область" })).toBeNull();
  expect(signIn.closest("div.rounded-xl")).toBeTruthy();
  const connection = screen.getByRole("heading", { name: locale === "en" ? "Connection instructions" : "Как подключить агента" });
  expect(signIn.compareDocumentPosition(connection) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

function observeReveal({ alertY, alertHeight, headerHeight = 121 }: { alertY: number; alertHeight: number; headerHeight?: number }) {
  const frames = new Map<number, FrameRequestCallback>();
  let nextFrame = 0;
  const scroll = vi.fn();
  const cancel = vi.fn((id: number) => frames.delete(id));
  vi.stubGlobal("innerHeight", 900);
  vi.stubGlobal("scrollBy", scroll);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    const id = ++nextFrame;
    frames.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", cancel);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    if (this.matches("[data-agent-header]")) return DOMRect.fromRect({ x: 0, y: 0, width: 320, height: headerHeight });
    if (this.matches('[role="alert"]')) return DOMRect.fromRect({ x: 16, y: alertY, width: 288, height: alertHeight });
    return DOMRect.fromRect();
  });
  const runFrame = () => act(() => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach(callback => callback(0));
  });
  return { scroll, frames, cancel, runFrame };
}

async function failWorkspace(locale: "en" | "ru", status = 503) {
  const view = render(<LocaleProvider initialLocale={locale}><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText(detail.result)).toBeTruthy();
  const refresh = screen.getByRole("button", { name: locale === "en" ? "Refresh workspace" : "Обновить рабочую область" });
  refresh.focus();
  vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-workspace") ? Promise.resolve(new Response("", { status })) : fixtureFetch(input));
  fireEvent.click(refresh);
  const alert = await screen.findByRole("alert");
  await act(async () => {}); // findByRole can resolve before the commit's passive reveal effect has run
  expect(screen.queryByText(detail.result)).toBeNull();
  expect(screen.queryByRole("button", { name: /Review the document/ })).toBeNull();
  return { view, refresh, alert };
}

it.each([
  { locale: "en", alertY: 87, alertHeight: 156, correction: -345.5 },
  { locale: "ru", alertY: 63, alertHeight: 180, correction: -357.5 },
] as const)("reveals the collapsed workspace error below the measured Header in $locale without stealing focus", async geometry => {
  const observed = observeReveal(geometry);
  const { refresh } = await failWorkspace(geometry.locale);
  expect(observed.frames.size).toBe(1);
  observed.runFrame();
  expect(observed.scroll).toHaveBeenCalledExactlyOnceWith({ top: geometry.correction, behavior: "instant" });
  expect(document.activeElement).toBe(refresh);
});

it("reveals a missing workspace using a different measured Header height", async () => {
  const observed = observeReveal({ alertY: 87, alertHeight: 156, headerHeight: 180 });
  await failWorkspace("en", 404);
  observed.runFrame();
  expect(observed.scroll).toHaveBeenCalledExactlyOnceWith({ top: -375, behavior: "instant" });
});

it("does not scroll an error already fully visible below the Header", async () => {
  const observed = observeReveal({ alertY: 200, alertHeight: 156 });
  await failWorkspace("en");
  expect(observed.frames.size).toBe(1);
  observed.runFrame();
  expect(observed.scroll).not.toHaveBeenCalled();
});

it("reveals an error below the viewport", async () => {
  const observed = observeReveal({ alertY: 920, alertHeight: 156 });
  await failWorkspace("en");
  observed.runFrame();
  expect(observed.scroll).toHaveBeenCalledExactlyOnceWith({ top: 487.5, behavior: "instant" });
});

it("aligns an oversized error below the Header instead of above it", async () => {
  const observed = observeReveal({ alertY: 87, alertHeight: 850 });
  await failWorkspace("en");
  observed.runFrame();
  expect(observed.scroll).toHaveBeenCalledExactlyOnceWith({ top: -34, behavior: "instant" });
});

it("does not replay reveal on a same-error rerender", async () => {
  const observed = observeReveal({ alertY: 87, alertHeight: 156 });
  const { view } = await failWorkspace("en");
  observed.runFrame();
  view.rerender(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  expect(observed.frames.size).toBe(0);
  expect(observed.scroll).toHaveBeenCalledTimes(1);
});

it.each(["retry", "unmount"] as const)("cancels pending reveal on %s", async action => {
  const observed = observeReveal({ alertY: 87, alertHeight: 156 });
  const { view, alert } = await failWorkspace("en");
  expect(observed.frames.size).toBe(1);
  if (action === "unmount") view.unmount();
  else {
    vi.mocked(fetch).mockImplementation(fixtureFetch);
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: /Review the document/ })).toBeTruthy();
    expect(await screen.findByText(detail.result)).toBeTruthy();
  }
  expect(observed.cancel).toHaveBeenCalledTimes(1);
  expect(observed.frames.size).toBe(0);
  observed.runFrame();
  expect(observed.scroll).not.toHaveBeenCalled();
});

it.each(["list", "detail"] as const)("does not reveal a child %s error", async child => {
  const observed = observeReveal({ alertY: 87, alertHeight: 156 });
  if (child === "list") vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-tasks?") ? Promise.resolve(new Response("", { status: 503 })) : fixtureFetch(input));
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  if (child === "detail") {
    const select = await screen.findByRole("button", { name: /Review the document/ });
    vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 503 }));
    fireEvent.click(select);
  }
  expect(await screen.findByRole("alert")).toBeTruthy();
  await act(async () => {}); // let a late passive reveal effect run so frames.size 0 is not vacuous
  expect(observed.frames.size).toBe(0);
  observed.runFrame();
  expect(observed.scroll).not.toHaveBeenCalled();
});

it.each(["en", "ru"] as const)("keeps external agent profiles accessible from the workspace in %s", async locale => {
  render(<LocaleProvider initialLocale={locale}><Workspace /></LocaleProvider>);
  const link = await screen.findByRole("link", { name: actor.name });
  expect(link.getAttribute("href")).toBe(`/agents/${actor.id}`);
});

function completeFirstPage(total = 52) {
  const items = Array.from({ length: 50 }, (_, index) => index === 0 ? task : index === 1 ? second : { ...task, id: `synthetic-page-task-${index}`, title: `Synthetic page task ${index}` });
  return { items, total, limit: 50, offset: 0 };
}

it.each(["en", "ru"] as const)("keeps sign-in and connection instructions available when browser storage is denied in %s", async locale => {
  vi.stubGlobal("localStorage", { getItem: () => { throw new DOMException("Storage denied", "SecurityError"); } });
  render(<LocaleProvider initialLocale={locale}><Workspace /></LocaleProvider>);
  const signIn = await screen.findByRole("link", { name: locale === "en" ? "Sign in to see your agents" : "Войти и посмотреть агентов" });
  expect(signIn.getAttribute("href")).toBe("/login?next=/workspace");
  expect(screen.getByRole("heading", { name: locale === "en" ? "Connection instructions" : "Как подключить агента" })).toBeTruthy();
  expect(screen.getByRole("link", { name: locale === "en" ? "Read the SDK guide" : "Открыть инструкцию SDK" }).getAttribute("href")).toContain(locale === "en" ? "GETTING_STARTED.md" : "GETTING_STARTED_RU.md");
  await act(async () => {});
  expect(fetch).not.toHaveBeenCalled();
});

it.each(["en", "ru"] as const)("hides private panels during storage denial, ignores a late detail and recovers without restoring selection in %s", async locale => {
  let release: (value: Response) => void = () => {};
  vi.mocked(fetch).mockImplementation(input => String(input).endsWith("synthetic-task") ? new Promise<Response>(resolve => { release = resolve; }) : fixtureFetch(input));
  const readableStorage = localStorage;
  render(<LocaleProvider initialLocale={locale}><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText(locale === "en" ? "Reading the selected result…" : "Загружаем выбранный результат…")).toBeTruthy();
  await act(async () => {});
  const requestsBeforeDenial = vi.mocked(fetch).mock.calls.length;
  vi.stubGlobal("localStorage", { getItem: () => { throw new DOMException("Storage denied", "SecurityError"); } });
  fireEvent(window, new Event("storage"));
  expect(await screen.findByRole("link", { name: locale === "en" ? "Sign in to see your agents" : "Войти и посмотреть агентов" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Review the document/ })).toBeNull();
  expect(screen.queryByRole("heading", { name: locale === "en" ? "Your agents" : "Ваши агенты" })).toBeNull();
  expect(screen.queryByRole("heading", { name: locale === "en" ? "Educational units, not money" : "Учебные единицы, не деньги" })).toBeNull();
  await act(async () => release(response(detail)));
  expect(screen.queryByText(detail.result)).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(requestsBeforeDenial);
  vi.mocked(fetch).mockImplementation(fixtureFetch);
  vi.stubGlobal("localStorage", readableStorage);
  fireEvent(window, new Event("storage"));
  expect(await screen.findByRole("button", { name: /Review the document/ })).toBeTruthy();
  expect(await screen.findByText(locale === "en" ? "Choose a task to read its result and decision history." : "Выберите задание, чтобы увидеть результат и историю решений.")).toBeTruthy();
  expect(screen.queryByText(detail.result)).toBeNull();
});

it("starts the replacement owner's task list at the first page", async () => {
  let replacement = false;
  const replacementReads: string[] = [];
  vi.mocked(fetch).mockImplementation(input => {
    const url = String(input);
    if (!url.includes("agent-tasks?")) return fixtureFetch(input);
    if (replacement) {
      replacementReads.push(url);
      return Promise.resolve(response(url.includes("offset=0") ? page : { ...page, items: [], offset: 50 }));
    }
    return Promise.resolve(response(url.includes("offset=50") ? { items: [second], total: 51, limit: 50, offset: 50 } : completeFirstPage(51)));
  });
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Tasks 51–51 of 51")).toBeTruthy();
  replacement = true;
  localStorage.setItem("access_token", "synthetic-replacement-owner");
  fireEvent(window, new Event("storage"));
  expect(await screen.findByRole("button", { name: /Review the document/ })).toBeTruthy();
  expect(replacementReads.length).toBeGreaterThan(0);
  expect(replacementReads.every(url => url.includes("offset=0"))).toBe(true);
  expect(screen.getByText("Tasks 1–2 of 2")).toBeTruthy();
});

it("does not request the previous owner's selected task after direct account replacement", async () => {
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /Review the document/ }));
  expect(await screen.findByText(detail.result)).toBeTruthy();
  const replacementReads: string[] = [];
  vi.mocked(fetch).mockImplementation(input => {
    replacementReads.push(String(input));
    return fixtureFetch(input);
  });
  localStorage.setItem("access_token", "synthetic-replacement-owner");
  fireEvent(window, new Event("storage"));
  expect(await screen.findByRole("button", { name: /Review the document/ })).toBeTruthy();
  await act(async () => {});
  expect(replacementReads.some(url => url.endsWith("/synthetic-task"))).toBe(false);
  expect(screen.queryByText(detail.result)).toBeNull();
  expect(screen.getByText("Choose a task to read its result and decision history.")).toBeTruthy();
});

it.each(["en", "ru"] as const)("preserves the current-page filter when loading the next page in %s", async locale => {
  vi.mocked(fetch).mockImplementation(input => String(input).includes("agent-tasks?") ? Promise.resolve(response(String(input).includes("offset=50") ? { ...page, total: 52, offset: 50 } : completeFirstPage())) : fixtureFetch(input));
  render(<LocaleProvider initialLocale={locale}><Workspace /></LocaleProvider>);
  await screen.findByRole("button", { name: /Review the document/ });
  const filterName = locale === "en" ? "Accepted" : "Принято";
  fireEvent.click(screen.getByRole("button", { name: filterName }));
  fireEvent.click(screen.getByRole("button", { name: locale === "en" ? "Next page" : "Следующая страница" }));
  expect(await screen.findByText(locale === "en" ? "Tasks 51–52 of 52" : "Задания 51–52 из 52")).toBeTruthy();
  expect(screen.getByRole("button", { name: filterName }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByRole("button", { name: /Review the document/ })).toBeNull();
  expect(screen.getByRole("button", { name: /Second task/ })).toBeTruthy();
});

it.each(["en", "ru"] as const)("focuses the stable task heading while its next page loads and settles in %s", async locale => {
  let release: (value: Response) => void = () => {};
  vi.mocked(fetch).mockImplementation(input => {
    const url = String(input);
    if (!url.includes("agent-tasks?")) return fixtureFetch(input);
    return url.includes("offset=50") ? new Promise<Response>(resolve => { release = resolve; }) : Promise.resolve(response(completeFirstPage()));
  });
  render(<LocaleProvider initialLocale={locale}><Workspace /></LocaleProvider>);
  const next = await screen.findByRole("button", { name: locale === "en" ? "Next page" : "Следующая страница" });
  const heading = screen.getByRole("heading", { name: locale === "en" ? "Tasks" : "Задания" });
  next.focus();
  fireEvent.click(next);
  await act(async () => {});
  const focusDuringLoading = document.activeElement;
  await act(async () => release(response({ ...page, total: 52, offset: 50 })));
  expect(await screen.findByText(locale === "en" ? "Tasks 51–52 of 52" : "Задания 51–52 из 52")).toBeTruthy();
  expect(focusDuringLoading).toBe(heading);
  expect(document.activeElement).toBe(heading);
  expect(heading.getAttribute("tabindex")).toBe("-1");
});

it.each(["en", "ru"] as const)("returns to a valid page when the last page disappears after refresh in %s", async locale => {
  let shrunk = false;
  vi.mocked(fetch).mockImplementation(input => {
    const url = String(input);
    if (!url.includes("agent-tasks?")) return fixtureFetch(input);
    if (!url.includes("offset=50")) return Promise.resolve(response(completeFirstPage(shrunk ? 50 : 51)));
    return Promise.resolve(response({ items: shrunk ? [] : [second], total: shrunk ? 50 : 51, limit: 50, offset: 50 }));
  });
  render(<LocaleProvider initialLocale={locale}><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: locale === "en" ? "Next page" : "Следующая страница" }));
  expect(await screen.findByText(locale === "en" ? "Tasks 51–51 of 51" : "Задания 51–51 из 51")).toBeTruthy();
  shrunk = true;
  fireEvent.click(screen.getByRole("button", { name: locale === "en" ? "Refresh workspace" : "Обновить рабочую область" }));
  expect(await screen.findByText(locale === "en" ? "Tasks 1–50 of 50" : "Задания 1–50 из 50")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Review the document/ })).toBeTruthy();
  expect(screen.queryByText(locale === "en" ? "Tasks 51–50 of 50" : "Задания 51–50 из 50")).toBeNull();
});

it("recovers a removed third page to the last valid second page instead of the first", async () => {
  let shrunk = false;
  const middleItems = Array.from({ length: 50 }, (_, index) => ({ ...task, id: `synthetic-middle-task-${index}`, title: `Synthetic middle task ${index}` }));
  const lastTask = { ...second, id: "synthetic-last-task", title: "Synthetic last task" };
  vi.mocked(fetch).mockImplementation(input => {
    const url = String(input);
    if (!url.includes("agent-tasks?")) return fixtureFetch(input);
    const offset = Number(new URL(url, "http://localhost").searchParams.get("offset"));
    const total = shrunk ? 100 : 101;
    if (offset === 0) return Promise.resolve(response(completeFirstPage(total)));
    return Promise.resolve(response({ items: offset === 50 ? middleItems : shrunk ? [] : [lastTask], total, limit: 50, offset }));
  });
  render(<LocaleProvider initialLocale="en"><Workspace /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Tasks 51–100 of 101")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Tasks 101–101 of 101")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Synthetic last task/ })).toBeTruthy();
  shrunk = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  expect(await screen.findByText("Tasks 51–100 of 100")).toBeTruthy();
  expect(screen.getByText("Synthetic middle task 0", { exact: true }).closest("button")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Review the document/ })).toBeNull();
  expect(screen.queryByText("Tasks 101–100 of 100")).toBeNull();
});
