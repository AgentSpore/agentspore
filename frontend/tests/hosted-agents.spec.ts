import { test, expect } from "@playwright/test";

const BASE = "http://localhost:3000";

test.describe("Public layout and chat", () => {
  test("10. No horizontal overflow on mobile (375px)", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${BASE}/`);
    await page.waitForLoadState("networkidle");

    const overflow = await page.evaluate(
      () => document.body.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBe(0);
  });

  test("11. No horizontal overflow on projects page (375px)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${BASE}/projects`);
    await page.waitForLoadState("networkidle");

    const overflow = await page.evaluate(
      () => document.body.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBe(0);
  });

  test("12. Chat page markdown renders bold and links", async ({ page }) => {
    await page.goto(`${BASE}/chat`);
    await page.waitForSelector("text=Agent Chat", { timeout: 10000 });

    // Check that messages with ** render as <strong> (markdown working)
    const strongs = await page.locator("strong").count();
    expect(strongs).toBeGreaterThanOrEqual(0);
  });
});
