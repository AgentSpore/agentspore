// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentPage from "./page";

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "demo-agent" }) }));
vi.mock("@/components/Header", () => ({ Header: () => null }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Agent page actions", () => {
  it("copies the agent id with one direct icon button", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      const agent = {
        id: "demo-agent", name: "Demo Agent", handle: "demo", specialization: "programmer",
        karma: 0, projects_created: 0, code_commits: 0, reviews_done: 0,
        created_at: "2026-10-01T00:00:00Z", last_heartbeat: null, is_active: true,
      };
      const body = input.endsWith("/api/v1/agents/demo-agent") ? agent : [];
      return new Response(JSON.stringify(body), { status: 200 });
    }));
    render(<AgentPage />);
    const button = await screen.findByRole("button", { name: "Copy Agent ID" });
    expect(screen.queryByText("More actions")).toBeNull();
    fireEvent.click(button);
    expect(writeText).toHaveBeenCalledWith("demo-agent");
  });
});
