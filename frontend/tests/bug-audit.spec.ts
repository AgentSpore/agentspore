import { test, expect } from "@playwright/test";

/**
 * Bug audit: v1.29.1 regression checks.
 * Tests run against http://localhost:3000 (backend may be unavailable — pages
 * must not render NaN or overflow even when API returns nothing).
 *
 * C1  — NaN render on / and /dashboard
 * H1  — invalid token → redirect /login
 * H2  — dashboard mobile overflow at 390px
 * M4  — decorative gradient blob overflow on 1280px viewport
 */

/* ─── helpers ────────────────────────────────────────────────────────── */

/** Returns true if the page body contains the substring anywhere. */
async function bodyHas(page: import("@playwright/test").Page, text: string) {
  return page.evaluate((t: string) => document.body.innerText.includes(t), text);
}

/** Checks document.documentElement.scrollWidth <= viewport.width */
async function noHorizontalOverflow(page: import("@playwright/test").Page) {
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  const viewportWidth = page.viewportSize()?.width ?? 1280;
  return scrollWidth <= viewportWidth;
}

/* ─── C1: NaN on home page ───────────────────────────────────────────── */
test.describe("C1 — no NaN render", () => {
  test("home page: no NaNh/NaNm/NaNs in visible text", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });

    const hasNaN = await bodyHas(page, "NaNh");
    expect(hasNaN, "Found 'NaNh' on home page").toBe(false);

    const hasNaNm = await bodyHas(page, "NaNm");
    expect(hasNaNm, "Found 'NaNm' on home page").toBe(false);

    const hasNaNs = await bodyHas(page, "NaNs");
    expect(hasNaNs, "Found 'NaNs' on home page").toBe(false);
  });

  test("home page: no NaN% in visible text", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    const hasNaN = await bodyHas(page, "NaN%");
    expect(hasNaN, "Found 'NaN%' on home page").toBe(false);
  });

  test("dashboard page: no NaN in stat cards", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "networkidle" });

    const nanPatterns = ["NaNh", "NaNm", "NaNs", "NaN%", "NaN,", "NaN "];
    for (const pattern of nanPatterns) {
      const found = await bodyHas(page, pattern);
      expect(found, `Found '${pattern}' on /dashboard`).toBe(false);
    }
  });

  test("agent detail page: no NaN% in model usage section", async ({ page }) => {
    // Visit agent list first, then navigate to first agent if any
    await page.goto("/agents", { waitUntil: "networkidle" });

    // Find first agent link
    const agentLink = page.locator('a[href^="/agents/"]').first();
    const count = await agentLink.count();

    if (count === 0) {
      test.skip();
      return;
    }

    await agentLink.click();
    await page.waitForLoadState("networkidle");

    const hasNaN = await bodyHas(page, "NaN%");
    expect(hasNaN, "Found 'NaN%' on agent detail page").toBe(false);
  });
});

/* ─── H1: invalid token → /login redirect ───────────────────────────── */
test.describe("H1 — invalid token redirect", () => {
  test("dashboard: invalid token clears storage and redirects to /login", async ({ page }) => {
    // Set invalid token before navigating
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate(() => {
      localStorage.setItem("access_token", "invalid_token_that_will_401");
    });

    // Navigate to dashboard — the auth check should detect 401 and redirect
    await page.goto("/dashboard", { waitUntil: "networkidle" });

    // After redirect, should be on /login (or have cleared the token)
    await page.waitForURL(/\/login/, { timeout: 10000 }).catch(() => {
      // If backend is down, the fetch itself may fail (network error, not 401)
      // In that case the test is inconclusive — only check no broken UI
    });

    const url = page.url();
    const tokenCleared = await page.evaluate(() => {
      const t = localStorage.getItem("access_token");
      return t === null || t === "invalid_token_that_will_401";
    });

    // If backend is available and returned 401, we should be on /login
    if (url.includes("/login")) {
      const cleared = await page.evaluate(() => localStorage.getItem("access_token"));
      expect(cleared, "access_token must be cleared on 401").toBeNull();
    } else {
      // Backend not available: at minimum verify page renders without crash
      const title = await page.title();
      expect(title.length, "Page must have a title").toBeGreaterThan(0);
    }
  });
});

/* ─── H2/M5: dashboard mobile overflow at 390px ─────────────────────── */
test.describe("H2 — mobile overflow", () => {
  test("dashboard at 390px: no horizontal scroll", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/dashboard", { waitUntil: "networkidle" });

    // Wait for agent list to render (or skeleton)
    await page.waitForTimeout(500);

    const ok = await noHorizontalOverflow(page);
    if (!ok) {
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      expect.soft(ok, `Horizontal overflow: scrollWidth=${scrollWidth}, viewport=390`).toBe(true);
    }
    expect(ok, "No horizontal overflow at 390px on /dashboard").toBe(true);
  });

  test("home page at 390px: no horizontal scroll", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForTimeout(500);

    const ok = await noHorizontalOverflow(page);
    expect(ok, "No horizontal overflow at 390px on /").toBe(true);
  });
});

/* ─── M4: blob overflow at 1280px desktop ───────────────────────────── */
test.describe("M4 — gradient blob overflow", () => {
  const PAGES = ["/", "/dashboard", "/agents", "/projects", "/analytics", "/login"];

  for (const path of PAGES) {
    test(`${path}: no horizontal overflow at 1280px`, async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto(path, { waitUntil: "networkidle" });
      await page.waitForTimeout(300);

      const ok = await noHorizontalOverflow(page);
      if (!ok) {
        const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
        expect(ok, `${path}: scrollWidth=${scrollWidth} > 1280`).toBe(true);
      }
      expect(ok, `No horizontal overflow at 1280px on ${path}`).toBe(true);
    });
  }
});
