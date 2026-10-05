// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import NewFlowPage from "./page";
import { LocaleProvider, useLocale } from "@/lib/i18n/LocaleProvider";

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
function LanguageSwitch() {
  const { setLocale } = useLocale();
  return <button onClick={() => setLocale("ru")}>Русский</button>;
}
vi.mock("@/components/Header", () => ({ Header: LanguageSwitch }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

function setup() {
  vi.stubGlobal("localStorage", { getItem: () => "synthetic-test-session" });
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    const data = init?.method === "POST" ? { id: url.endsWith("steps") ? "step-id" : "flow-id" }
      : [{ id: "agent-id", name: "Synthetic Agent", handle: "fixture", specialization: "programmer" }];
    return new Response(JSON.stringify(data), { status: 200 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<LocaleProvider initialLocale="en"><NewFlowPage /></LocaleProvider>);
  return fetcher;
}

describe("Flow form localization", () => {
  it("preserves user content and machine payloads across language changes", async () => {
    const fetcher = setup();
    await screen.findByRole("option", { name: /Synthetic Agent/ });
    fireEvent.change(screen.getByPlaceholderText("Flow title"), { target: { value: "Active" } });
    fireEvent.change(screen.getByPlaceholderText("Step title"), { target: { value: "Ready" } });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "agent-id" } });
    fireEvent.click(screen.getByRole("button", { name: "Русский" }));
    expect((screen.getByPlaceholderText("Название процесса") as HTMLInputElement).value).toBe("Active");
    expect((screen.getByPlaceholderText("Название шага") as HTMLInputElement).value).toBe("Ready");
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Создать процесс" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/flows/flow-id"));
    const writes = fetcher.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ title: "Active", description: null });
    expect(JSON.parse(String(writes[1][1]?.body))).toEqual({ agent_id: "agent-id", title: "Ready", instructions: null, depends_on: [], auto_approve: false });
  });

  it("retranslates an existing validation error without submitting", async () => {
    const fetcher = setup();
    await screen.findByRole("option", { name: /Synthetic Agent/ });
    fireEvent.click(screen.getByRole("button", { name: "Create Flow" }));
    expect(screen.getByText("Flow title is required")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Русский" }));
    expect(screen.getByText("Укажите название процесса")).toBeTruthy();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });
});
