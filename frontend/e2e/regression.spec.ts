import { test, expect } from "@playwright/test";

const BASE = "http://localhost:3000";

test.describe("UI Regression", () => {
  test("homepage loads with header and nav links", async ({ page }) => {
    await page.goto(BASE);
    await expect(page.locator("header")).toBeVisible();
    await expect(page.locator('header a[href="/agents"]')).toBeVisible();
    await expect(page.locator('header a[href="/flows"]')).toBeVisible();
    await expect(page.locator('header a[href="/projects"]')).toBeVisible();
  });

  test("agents page loads", async ({ page }) => {
    await page.goto(`${BASE}/agents`);
    await expect(page.locator("header")).toBeVisible();
    await expect(page.locator("text=Agents").first()).toBeVisible();
  });

  test("projects page loads", async ({ page }) => {
    await page.goto(`${BASE}/projects`);
    await expect(page.locator("header")).toBeVisible();
    await expect(page.locator("text=Projects").first()).toBeVisible();
  });

  test("flows page loads", async ({ page }) => {
    await page.goto(`${BASE}/flows`);
    await expect(page.locator("header")).toBeVisible();
  });

  test("login page loads with form", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await expect(page.locator('input[type="email"], input[name="email"]')).toBeVisible();
    await expect(page.locator('input[type="password"], input[name="password"]')).toBeVisible();
  });

  test("new flow page loads with builder", async ({ page }) => {
    await page.goto(`${BASE}/flows/new`);
    await expect(page.locator("text=New Flow").first()).toBeVisible();
    await expect(page.locator("text=Step 1")).toBeVisible();
  });

  test("teams page loads", async ({ page }) => {
    await page.goto(`${BASE}/teams`);
    await expect(page.locator("header")).toBeVisible();
  });

  test("login and access profile", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[type="email"], input[name="email"]', "***REMOVED***");
    await page.fill('input[type="password"], input[name="password"]', "***REMOVED***");
    await page.click('button[type="submit"]');
    await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 10000 }).catch(() => {});
    await page.goto(`${BASE}/profile`);
    await expect(page.locator("header")).toBeVisible();
  });

  test("authenticated: flows page shows content", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[type="email"], input[name="email"]', "***REMOVED***");
    await page.fill('input[type="password"], input[name="password"]', "***REMOVED***");
    await page.click('button[type="submit"]');
    await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 10000 }).catch(() => {});

    await page.goto(`${BASE}/flows`);
    await expect(page.locator("header")).toBeVisible();
  });
});
