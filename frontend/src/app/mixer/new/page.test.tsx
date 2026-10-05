// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import NewMixerPage from "./page";
import { LocaleProvider, useLocale } from "@/lib/i18n/LocaleProvider";

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
function LanguageSwitch() {
  const { setLocale } = useLocale();
  return <button onClick={() => setLocale("ru")}>Русский</button>;
}
vi.mock("@/components/Header", () => ({ Header: LanguageSwitch }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("preserves task/private markers and a dictionary-like title while translating the form", async () => {
  vi.stubGlobal("localStorage", { getItem: () => "synthetic-test-session" });
  const fetcher = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify([]), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  render(<LocaleProvider initialLocale="en"><NewMixerPage /></LocaleProvider>);
  fireEvent.change(screen.getByPlaceholderText("Session title"), { target: { value: "Active" } });
  const task = screen.getByPlaceholderText(/Enter your task here/);
  fireEvent.change(task, { target: { value: "Keep {{PRIVATE:value}} exactly" } });
  fireEvent.click(screen.getByRole("button", { name: "Русский" }));
  expect((screen.getByPlaceholderText("Название сессии") as HTMLInputElement).value).toBe("Active");
  expect((screen.getByPlaceholderText(/Введите задачу/) as HTMLTextAreaElement).value).toBe("Keep {{PRIVATE:value}} exactly");
  expect(screen.getByText("{{PRIVATE:value}}", { selector: "code" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Отметить как приватное" })).toBeTruthy();
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
});
