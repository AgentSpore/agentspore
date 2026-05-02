import { test, expect } from "@playwright/test";

const BASE = "http://localhost:3000";

test.describe("verify-email page", () => {
  test("missing token shows invalid-link state", async ({ page }) => {
    await page.goto(`${BASE}/verify-email`);
    await expect(page.getByTestId("state-missing")).toBeVisible();
    await expect(page.locator("text=Invalid link")).toBeVisible();
  });

  test("fake token shows error state from API", async ({ page }) => {
    await page.goto(`${BASE}/verify-email?token=fake-token-that-does-not-exist`);
    // Wait for verifying spinner to go away
    await expect(page.getByTestId("state-verifying")).toBeVisible();
    // API returns 400 — error state should appear
    await expect(page.getByTestId("state-error")).toBeVisible({ timeout: 10_000 });
    const msg = page.getByTestId("error-message");
    await expect(msg).not.toBeEmpty();
  });

  test("resend form accepts email and submits", async ({ page }) => {
    await page.goto(`${BASE}/verify-email`);
    await expect(page.getByTestId("state-missing")).toBeVisible();

    // Fill email and click Resend
    await page.fill('input[type="email"]', "test@example.com");
    await page.click("button:has-text('Resend')");

    // Shows resent confirmation state
    await expect(page.getByTestId("state-resent")).toBeVisible({ timeout: 10_000 });
  });
});
