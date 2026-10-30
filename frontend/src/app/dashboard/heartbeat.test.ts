import { describe, expect, it } from "vitest";
import { publicMessages } from "@/lib/i18n/public";
import { parseHeartbeat } from "./heartbeat";

describe("parseHeartbeat", () => {
  it("extracts state and counts from the backend summary", () => {
    expect(parseHeartbeat("Heartbeat: idle, 0 tasks, 0 notifications, 0 DMs"))
      .toEqual({ state: "idle", tasks: "0", notifications: "0", dms: "0" });
  });

  it("returns null for any other activity text", () => {
    expect(parseHeartbeat("Pushed 3 commits to repo")).toBeNull();
    expect(parseHeartbeat("Heartbeat: idle, many tasks")).toBeNull();
  });

  it("has a localized template with every placeholder in both languages", () => {
    for (const locale of ["en", "ru"] as const) {
      const template = publicMessages[locale].heartbeatSummary;
      for (const key of ["state", "tasks", "notifications", "dms"]) expect(template).toContain(`{${key}}`);
    }
    expect(publicMessages.ru.heartbeatSummary).not.toMatch(/Heartbeat|DMs/);
  });
});
