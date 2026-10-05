// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LocaleProvider, useLocale } from "@/lib/i18n/LocaleProvider";
import FlowsPage from "./page";
import MixerListPage from "../mixer/page";
import FlowDetailPage from "./[id]/page";
import StepChatPage from "./[id]/steps/[stepId]/page";
import MixerDetailPage from "../mixer/[id]/page";
import CreateHostedAgentPage from "../hosted-agents/new/page";
import NewCouncilPage from "../councils/new/page";
import RentalChatPage from "../rentals/[id]/page";
import AgentChatPage from "../agents/[id]/chat/page";
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router, useParams: () => ({ id: "fixture", stepId: "step" }) }));
function LanguageSwitch() {
  const { setLocale } = useLocale();
  return <><button onClick={() => setLocale("ru")}>Русский</button><button onClick={() => setLocale("en")}>English</button></>;
}
vi.mock("@/components/Header", () => ({ Header: LanguageSwitch }));
vi.mock("@/components/FreshnessBadge", () => ({ FreshnessBadge: () => null }));
const step = { id: "step", title: "Ready", status: "ready", depends_on: [], step_type: "task", position: 0, agent_handle: "fixture", auto_approve: false };
const flow = { id: "fixture", title: "Active", status: "running", created_at: "2026-01-01T00:00:00Z", steps: [step] };
const mixer = { ...flow, chunks: [], fragment_count: 0, fragment_ttl_hours: 24, original_task: "Original fixture task" };
function fixtures(data: unknown, failedPost = false) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response(JSON.stringify(failedPost ? {} : { id: "created" }), { status: failedPost ? 500 : 200 });
    if (url.endsWith("/auth/me")) return new Response(JSON.stringify({ name: "Fixture User" }));
    if (url.includes("/councils/")) return new Response(JSON.stringify(url.endsWith("models") ? [{ id: "model1", name: "Fixture Model", provider: "fixture", preferred: true, context_length: 1000 }] : []));
    if (url.endsWith("/models")) return new Response(JSON.stringify({ models: [{ id: "fixture-model", name: "Fixture Model", provider: "openrouter" }] }));
    if (url.endsWith("/agents/fixture")) return new Response(JSON.stringify({ id: "fixture", name: "Active", handle: "fixture", specialization: "programmer" }));
    if (url.includes("messages") || url.includes("leaderboard") || url.includes("audit")) return new Response("[]");
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
beforeEach(() => {
  vi.stubGlobal("localStorage", { getItem: () => "synthetic-test-session" });
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
it.each([FlowsPage, MixerListPage])("keeps all-filter machine IDs and user titles in Russian", async (Page) => {
  fixtures([{ ...mixer, steps: undefined }]);
  render(<LocaleProvider initialLocale="ru"><Page /></LocaleProvider>);
  await screen.findByText("Active");
  fireEvent.click(screen.getByRole("button", { name: "Все" }));
  expect(screen.getByText("Active")).toBeTruthy();
});
it("keeps ready-step skip actions in Russian and posts the raw step URL", async () => {
  const fetcher = fixtures(flow);
  render(<LocaleProvider initialLocale="ru"><FlowDetailPage /></LocaleProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Пропустить шаг" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url, init]) => url.endsWith("/steps/step/skip") && init?.method === "POST")).toBe(true));
});
it("keeps ready-step chat input and raw message text in Russian", async () => {
  const fetcher = fixtures(flow);
  render(<LocaleProvider initialLocale="ru"><StepChatPage /></LocaleProvider>);
  fireEvent.change(await screen.findByPlaceholderText("Напишите сообщение..."), { target: { value: "Active" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST" && String(init.body).includes("Active"))).toBe(true));
});
it("returns from audit to the chunks panel in Russian", async () => {
  fixtures({ ...mixer, chunks: [{ id: "chunk", title: "Raw chunk fixture", status: "pending", instructions: "Unchanged task", position: 0 }] });
  render(<LocaleProvider initialLocale="ru"><MixerDetailPage /></LocaleProvider>);
  await screen.findByText("Active");
  fireEvent.click(screen.getAllByRole("button", { name: "Журнал действий" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "Части (1)" }));
  expect(screen.getByText("Raw chunk fixture")).toBeTruthy();
});
it("clears a template without translating the specialization in the API payload", async () => {
  const fetcher = fixtures([]);
  render(<LocaleProvider initialLocale="ru"><CreateHostedAgentPage /></LocaleProvider>);
  fireEvent.click(await screen.findByText("Обзор подкастов"));
  fireEvent.click(screen.getByRole("button", { name: "сбросить ×" }));
  for (const [value, label] of [
    ["programmer", "программист"], ["devops", "инженер инфраструктуры"],
    ["researcher", "исследователь"], ["analyst", "аналитик"],
    ["designer", "дизайнер"], ["writer", "автор"],
    ["tester", "тестировщик"], ["security", "безопасность"],
  ]) {
    expect(screen.getByRole("option", { name: label }).getAttribute("value")).toBe(value);
  }
  const inputs = screen.getAllByRole("textbox");
  fireEvent.change(inputs[0], { target: { value: "Active" } });
  fireEvent.change(inputs[2], { target: { value: "Keep original input unchanged" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать агента" }));
  await waitFor(() => {
    const post = fetcher.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post).toBeTruthy();
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ specialization: "programmer", name: "Active" });
  });
});
it("retranslates failed-send fallback without repeating POST or changing the draft", async () => {
  const fetcher = fixtures([], true);
  render(<LocaleProvider initialLocale="ru"><AgentChatPage /></LocaleProvider>);
  const input = await screen.findByRole("textbox");
  fireEvent.change(input, { target: { value: "Active" } });
  fireEvent.submit(input.closest("form")!);
  await screen.findByText("Не удалось отправить сообщение");
  fireEvent.click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByText("Failed to send")).toBeTruthy();
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Active");
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});

it("keeps a council's preselected model role raw through locale changes", async () => {
  fixtures([]);
  render(<LocaleProvider initialLocale="ru"><NewCouncilPage /></LocaleProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Выбрать модели" }));
  const role = await screen.findByRole("combobox");
  expect((role as HTMLSelectElement).value).toBe("panelist");
  fireEvent.change(role, { target: { value: "critic" } });
  fireEvent.click(screen.getByRole("button", { name: "English" }));
  expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("critic");
});
it("switches rental language without losing the draft or posting any action", async () => {
  const fetcher = fixtures({ ...flow, status: "active", agent_id: "fixture", agent_handle: "fixture", agent_name: "Fixture", specialization: "programmer" });
  render(<LocaleProvider initialLocale="en"><RentalChatPage /></LocaleProvider>);
  const input = await screen.findByRole("textbox");
  fireEvent.change(input, { target: { value: "Active" } });
  fireEvent.click(screen.getByRole("button", { name: "Русский" }));
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Active");
  expect(screen.getByRole("button", { name: "Отправить" })).toBeTruthy();
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
});
it("retranslates hosted HTTP fallback codes without creating another agent", async () => {
  const fetcher = fixtures([], true);
  render(<LocaleProvider initialLocale="ru"><CreateHostedAgentPage /></LocaleProvider>);
  fireEvent.click(await screen.findByText("Обзор подкастов"));
  fireEvent.click(screen.getByRole("button", { name: "Создать агента" }));
  await screen.findByText("Ошибка 500");
  fireEvent.click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByText("Error 500")).toBeTruthy();
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});
it("translates the closed rental state as a label while retaining its machine status", async () => {
  fixtures({ ...flow, status: "completed", rating: null, agent_id: "fixture", agent_handle: "fixture", agent_name: "Fixture", specialization: "programmer" });
  render(<LocaleProvider initialLocale="ru"><RentalChatPage /></LocaleProvider>);
  await screen.findByText("Active");
  expect(screen.getByText(/отправка сообщений недоступна/).textContent).not.toContain("completed");
});
