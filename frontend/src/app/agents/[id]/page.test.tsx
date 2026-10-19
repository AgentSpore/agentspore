// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentPage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "demo-agent" }), useRouter: () => ({ refresh: vi.fn() }),
}));
vi.mock("@/components/Header", () => ({ Header: () => null }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function stubAgentFetch(modelUsage: unknown = null) {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const agent = {
      id: "demo-agent", name: "Demo Agent", handle: "demo", specialization: "programmer",
      karma: 0, projects_created: 0, code_commits: 0, reviews_done: 0,
      created_at: "2026-10-01T00:00:00Z", last_heartbeat: null, is_active: true,
    };
    const body = input.endsWith("/api/v1/agents/demo-agent/model-usage") ? modelUsage
      : input.endsWith("/api/v1/agents/demo-agent") ? agent
      : input.includes("/blog/") ? { posts: [], total: 0, limit: 10, offset: 0 }
      : [];
    return new Response(JSON.stringify(body), { status: 200 });
  }));
}

async function clickCopy() {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(<AgentPage />);
  const button = await screen.findByRole("button", { name: "Copy ID" });
  expect(screen.queryByText("More actions")).toBeNull();
  expect(screen.getByRole("status").textContent).toBe("");
  fireEvent.click(button);
}

describe("Agent page copy-id button", () => {
  afterEach(() => { vi.useRealTimers(); });

  it("announces Copied, then resets after 2s", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    stubAgentFetch();
    await clickCopy();
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copied"));
    expect(writeText).toHaveBeenCalledWith("demo-agent");
    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(screen.getByRole("status").textContent).toBe("");
    expect(screen.getByRole("button", { name: "Copy ID" })).toBeTruthy();
  });

  it("announces an error when writeText rejects", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => { throw new Error("denied"); }) } });
    stubAgentFetch();
    await clickCopy();
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copy failed"));
    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(screen.getByRole("status").textContent).toBe("");
  });

  it("announces an error when the clipboard API is missing", async () => {
    vi.stubGlobal("navigator", {});
    stubAgentFetch();
    await clickCopy();
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copy failed"));
  });
});

it('keeps the skip target through loading and failed requests', async () => {
  const responses: ((response: Response) => void)[] = [];
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { responses.push(resolve); })));
  render(<AgentPage />);
  expect(screen.getByRole('main').id).toBe('main-content');
  expect(screen.getByRole('main').tabIndex).toBe(-1);
  expect(responses.length).toBeGreaterThan(0);
  responses.forEach(respond => respond(new Response('{}', { status: 404 })));
  await screen.findByText('Agent not found');
  expect(screen.getAllByRole('main')).toHaveLength(1);
  expect(screen.getByRole('main').id).toBe('main-content');
  expect(screen.getByRole('main').tabIndex).toBe(-1);
});


// AgentService.get_model_usage aggregates total_calls per model, call_count per task.
const MODEL_USAGE_RESPONSE = {
  agent_id: "demo-agent", total_calls: 4, unique_models: 2,
  by_model: [
    { model: "synthetic/model-alpha", total_calls: 3 },
    { model: "synthetic/model-beta", total_calls: 1 },
  ],
  by_task: [
    { model: "synthetic/model-alpha", task_type: "codegen", call_count: 3,
      last_used: "2026-10-19T00:00:00+00:00" },
    { model: "synthetic/model-beta", task_type: "review", call_count: 1,
      last_used: "2026-10-19T00:00:00+00:00" },
  ],
};

describe.each(["en", "ru"] as const)("Agent model usage in %s", locale => {
  it("renders HTTP model totals and percentages while preserving task counts", async () => {
    stubAgentFetch(MODEL_USAGE_RESPONSE);
    render(<LocaleProvider initialLocale={locale}><AgentPage /></LocaleProvider>);
    const title = locale === "en" ? "Model Usage" : "Использование моделей";
    await screen.findByText(title);
    expect(screen.getByRole("main").textContent).not.toContain(new Intl.NumberFormat(locale).format(Number.NaN));
    expect(screen.getByText("3 · 75%")).toBeTruthy();
    expect(screen.getByText("1 · 25%")).toBeTruthy();
    expect(screen.getByText("3×")).toBeTruthy();
    expect(screen.getByText("1×")).toBeTruthy();
    expect(screen.getAllByTitle("synthetic/model-alpha")).toHaveLength(2);
    expect(screen.getAllByTitle("synthetic/model-beta")).toHaveLength(2);
  });

  it("omits usage for an empty HTTP response without dividing by zero", async () => {
    stubAgentFetch({ agent_id: "demo-agent", total_calls: 0, unique_models: 0,
      by_model: [], by_task: [] });
    render(<LocaleProvider initialLocale={locale}><AgentPage /></LocaleProvider>);
    await screen.findByRole("heading", { name: "Demo Agent" });
    expect(screen.queryByText("models://usage")).toBeNull();
    expect(screen.queryByText("tasks://breakdown")).toBeNull();
    expect(screen.getByRole("main").textContent).not.toContain(new Intl.NumberFormat(locale).format(Number.NaN));
  });
});
