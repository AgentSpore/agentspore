/**
 * Playwright E2E for Councils.
 * Runs against a live backend + frontend. Requires OPENROUTER_API_KEY in the
 * backend env. The council progresses through real OpenRouter calls, so the
 * test has an 8-minute ceiling. Most runs finish in ~2 minutes.
 */
import { expect, test } from "@playwright/test";

const FRONT = process.env.FRONT_URL || "http://localhost:3000";
const API = process.env.API_URL || "http://localhost:18000";

test.describe("Councils E2E", () => {
  test("convene → round → vote → done → resolution", async ({ page }) => {
    test.setTimeout(9 * 60 * 1000);

    const TOPIC = "Playwright council smoke";
    const BRIEF =
      "Minimal end-to-end verification. Panelists reply with a single short sentence approving the trivial proposal.";

    await page.goto(`${FRONT}/councils/new`, { waitUntil: "domcontentloaded" });
    await page.locator("input").first().fill(TOPIC);
    await page.locator("textarea").first().fill(BRIEF);
    await page.locator("select").nth(0).selectOption({ label: "3 agents" });
    await page.locator("select").nth(1).selectOption({ label: "1 round" });
    await page.getByRole("button", { name: /Convene/ }).click();

    await page.waitForURL(/\/councils\/[0-9a-f-]{36}$/, { timeout: 20_000 });
    const councilId = page.url().split("/").pop()!;
    expect(councilId).toMatch(/^[0-9a-f-]{36}$/);

    // Brief card
    await expect(page.getByText(BRIEF.slice(0, 20), { exact: false }).first()).toBeVisible({ timeout: 15_000 });

    // 3 panelists in the sidebar
    await expect.poll(async () => page.locator("aside li").count(), { timeout: 20_000 }).toBeGreaterThanOrEqual(3);

    // Wait for backend state machine to complete.
    await expect
      .poll(
        async () => {
          const r = await page.request.get(`${API}/api/v1/councils/${councilId}`);
          const data = (await r.json()) as { council?: { status?: string } };
          return data?.council?.status ?? "unknown";
        },
        { timeout: 8 * 60 * 1000, intervals: [2000] },
      )
      .toMatch(/^(done|aborted)$/);

    // Reload and verify the resolution block renders
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByText(/Council resolution/).first()).toBeVisible({ timeout: 20_000 });

    const resolution = await page.locator(".prose").first().textContent();
    expect(resolution || "").toContain("Council resolution");
    expect(resolution || "").toMatch(/approve|reject|abstain|errored/);
  });
});
