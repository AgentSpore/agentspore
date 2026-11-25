// @vitest-environment jsdom
import { act } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ActivityEvent } from "@/lib/api";
import { LiveTicker } from "./ActivityStrips";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => vi.useRealTimers());

const T0 = Date.parse("2026-11-24T12:00:00Z");
const event = { agent_id: "a1", agent_name: "Ada", action_type: "code_commit", project_id: null,
  description: null, metadata: {}, ts: new Date(T0 - 5000).toISOString() } as unknown as ActivityEvent;

describe("LiveTicker hydration", () => {
  it("hydrates without a recoverable error when the clock moved since SSR", async () => {
    vi.useFakeTimers({ now: T0, toFake: ["Date"] });
    const html = renderToString(<LiveTicker events={[event]} />);
    const container = document.createElement("div");
    container.innerHTML = html;
    vi.setSystemTime(T0 + 30_000);
    const recoverable: string[] = [];
    await act(async () => {
      hydrateRoot(container, <LiveTicker events={[event]} />, {
        onRecoverableError: (e) => recoverable.push(String(e instanceof Error ? e.message : e)),
      });
    });
    expect(recoverable).toEqual([]);
    expect(container.textContent).toContain("ago");
  });
});
