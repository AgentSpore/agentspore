// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LocaleProvider, useLocale } from "@/lib/i18n/LocaleProvider";
import type { BattleDetail } from "@/lib/api";
import { BattleStepper } from "./BattleStepper";
import { StatusBadge } from "./StatusBadge";
import { BattleAvailabilityToggle } from "./BattleAvailabilityToggle";
import { fetchWithAuth } from "@/lib/auth";
const router = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/auth", () => ({ fetchWithAuth: vi.fn() }));
function LanguageSwitch() {
  const { setLocale } = useLocale();
  return <button onClick={() => setLocale("ru")}>Русский</button>;
}
const battle: BattleDetail = {
  id: "synthetic-battle", task_id: null, status: "completed", agent_a_id: "synthetic-agent",
  agent_b_id: null, contender_a_id: null, contender_b_id: null, winner: null, is_demo: false,
  challenged_at: "2026-01-01T00:00:00Z", started_at: null, ended_at: null,
  task_category_filter: null, task_difficulty_filter: null, task_title_snapshot: null,
  task_content_withheld: true, rated_eligible: null, is_rated: null,
  rated_ineligibility_reason: null, judging_stop_reason: null, viewer_can_accept: false,
  agent_b_accepted_at: null, challenge_expires_at: "2026-01-02T00:00:00Z",
  task_prompt_snapshot: null, task_rubric_snapshot: null, time_limit_seconds_snapshot: null,
  verdict_reason: null, elo_a_before: null, elo_b_before: null, elo_a_after: null,
  elo_b_after: null, queued_at: null, deadline_at: null, readiness: null,
};
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });
describe("Battle localization boundaries", () => {
  it("preserves completed stage and current-step semantics across languages", () => {
    const { container, rerender } = render(<LocaleProvider initialLocale="en"><LanguageSwitch /><BattleStepper battle={battle} /></LocaleProvider>);
    expect(container.textContent).toMatch(/Step\s+6\s+of\s+6/);
    fireEvent.click(screen.getByRole("button", { name: "Русский" }));
    expect(container.textContent).toMatch(/Шаг\s+6\s+из\s+6/);
    rerender(<LocaleProvider initialLocale="en"><LanguageSwitch /><BattleStepper battle={{ ...battle, status: "running" }} /><StatusBadge status="running" /></LocaleProvider>);
    expect(container.querySelector('[aria-current="step"]')).not.toBeNull();
    expect(screen.getByText("Бой идёт")).toBeTruthy();
  });
  it("keeps agent names and PATCH booleans raw without refetching on locale switch", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ available_for_battles: false })));
    vi.stubGlobal("fetch", fetcher);
    vi.mocked(fetchWithAuth).mockResolvedValue(new Response(JSON.stringify({ available_for_battles: true })));
    render(<LocaleProvider initialLocale="en"><LanguageSwitch /><BattleAvailabilityToggle agentId="synthetic-agent" agentName="Ready" /></LocaleProvider>);
    await waitFor(() => expect(screen.queryByText("Checking the current state…")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Русский" }));
    expect(screen.getByText(/Ready/)).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetchWithAuth).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Готов к боям" }));
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fetchWithAuth).mock.calls[0][0]).toContain("/agents/synthetic-agent/battle-availability");
    expect(JSON.parse(String(vi.mocked(fetchWithAuth).mock.calls[0][1]?.body))).toEqual({ available_for_battles: true });
  });
  it("localizes known failure and rolls optimistic availability back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ available_for_battles: false }))));
    vi.mocked(fetchWithAuth).mockRejectedValue(new Error("failed to change the setting"));
    const { container } = render(<LocaleProvider initialLocale="ru"><BattleAvailabilityToggle agentId="synthetic-agent" agentName="Ready" /></LocaleProvider>);
    await waitFor(() => expect(container.querySelector(".battle-toggle-thumb")).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Готов к боям" }));
    await screen.findByText("Не удалось изменить настройку");
    expect(container.querySelector<HTMLElement>(".battle-toggle-thumb")?.style.transform).toBe("translateX(calc(100% + 8px))");
  });
});
