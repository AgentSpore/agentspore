import { test, expect, Page, APIRequestContext } from "@playwright/test";

const BASE = "http://localhost:3000";
const API = "http://localhost:18000";
const EMAIL = "test@test.com";
const PASSWORD = "TestPass123";

let token = "";
let agentId = "";

async function loginViaAPI(request: APIRequestContext) {
  const res = await request.post(`${API}/api/v1/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  });
  const body = await res.json();
  token = body.access_token;
}

async function injectToken(page: Page) {
  await page.evaluate((t) => localStorage.setItem("access_token", t), token);
}

test.describe.serial("Cron UX — desktop (1280px)", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test.beforeAll(async ({ request }) => {
    await loginViaAPI(request);
    // Create a hosted agent for cron tests
    const res = await request.post(`${API}/api/v1/hosted-agents`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { agent_name: `CronTest-${Date.now().toString(36)}`, model: "nvidia/nemotron-3-super-120b-a12b:free" },
    });
    const body = await res.json();
    agentId = body.id;
  });

  test.afterAll(async ({ request }) => {
    if (!agentId) return;
    await request.delete(`${API}/api/v1/hosted-agents/${agentId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  });

  test("cron tab loads with empty state and template link", async ({ page }) => {
    await page.goto(`${BASE}/hosted-agents/${agentId}`);
    await injectToken(page);
    await page.reload();
    await page.getByRole("tab", { name: /schedule/i }).click().catch(async () => {
      // Tab may be a button without explicit role
      await page.locator("button", { hasText: "Schedule" }).first().click();
    });
    // Wait for cron tab to show
    await expect(page.getByText("Scheduled Tasks")).toBeVisible({ timeout: 8000 });
    await expect(page.getByText("No scheduled tasks yet")).toBeVisible();
    await expect(page.getByText("Fill in a daily summary template")).toBeVisible();
  });

  test("preset chips render and selecting one shows human preview", async ({ page }) => {
    await page.goto(`${BASE}/hosted-agents/${agentId}`);
    await injectToken(page);
    await page.reload();
    await page.locator("button", { hasText: "Schedule" }).first().click();
    await expect(page.getByText("Scheduled Tasks")).toBeVisible({ timeout: 8000 });

    // Default should be Daily 9am — human preview visible
    await expect(page.getByText("Every day at 09:00 UTC")).toBeVisible();

    // Switch to Every 15 min
    await page.locator("button", { hasText: "Every 15 min" }).click();
    await expect(page.getByText("Every 15 minutes")).toBeVisible();

    // Switch to Weekly Mon
    await page.locator("button", { hasText: "Weekly Mon" }).click();
    await expect(page.getByText(/Mon.*09:00 UTC/)).toBeVisible();

    // Switch to Custom — raw input appears
    await page.locator("button", { hasText: "Custom" }).click();
    await expect(page.locator("input[placeholder='0 9 * * *']").first()).toBeVisible();
  });

  test("create task with preset, verify human preview in card", async ({ page }) => {
    await page.goto(`${BASE}/hosted-agents/${agentId}`);
    await injectToken(page);
    await page.reload();
    await page.locator("button", { hasText: "Schedule" }).first().click();
    await expect(page.getByText("Scheduled Tasks")).toBeVisible({ timeout: 8000 });

    // Fill form
    await page.locator("input[placeholder='Daily report']").fill("Morning standup");
    // Pick "Daily 9am" preset (likely already selected by default)
    await page.locator("button", { hasText: "Daily 9am" }).click();
    await page.locator("textarea").fill("Generate a morning standup summary from recent tasks.");
    await page.locator("button", { hasText: "Create Task" }).click();

    // Card should appear with human preview
    await expect(page.getByText("Morning standup")).toBeVisible({ timeout: 8000 });
    await expect(page.getByText("Every day at 09:00 UTC")).toBeVisible();
    await expect(page.getByText("0 9 * * *")).toBeVisible();
  });

  test("toggle enabled / pause a task", async ({ page }) => {
    await page.goto(`${BASE}/hosted-agents/${agentId}`);
    await injectToken(page);
    await page.reload();
    await page.locator("button", { hasText: "Schedule" }).first().click();
    await expect(page.getByText("Morning standup")).toBeVisible({ timeout: 8000 });

    // Pause
    await page.locator("button", { hasText: "Pause" }).first().click();
    await expect(page.locator("button", { hasText: "Resume" }).first()).toBeVisible({ timeout: 5000 });

    // Resume
    await page.locator("button", { hasText: "Resume" }).first().click();
    await expect(page.locator("button", { hasText: "Pause" }).first()).toBeVisible({ timeout: 5000 });
  });

  test("edit task inline", async ({ page }) => {
    await page.goto(`${BASE}/hosted-agents/${agentId}`);
    await injectToken(page);
    await page.reload();
    await page.locator("button", { hasText: "Schedule" }).first().click();
    await expect(page.getByText("Morning standup")).toBeVisible({ timeout: 8000 });

    await page.locator("button", { hasText: "Edit" }).first().click();
    await expect(page.getByText("Edit task")).toBeVisible();

    // Change name
    const nameInput = page.locator("input").filter({ hasText: "" }).first();
    await nameInput.clear();
    await nameInput.fill("Updated standup");

    await page.locator("button", { hasText: "Save changes" }).click();
    await expect(page.getByText("Updated standup")).toBeVisible({ timeout: 8000 });
  });

  test("delete task via confirm dialog", async ({ page }) => {
    await page.goto(`${BASE}/hosted-agents/${agentId}`);
    await injectToken(page);
    await page.reload();
    await page.locator("button", { hasText: "Schedule" }).first().click();
    await expect(page.getByText("Scheduled Tasks")).toBeVisible({ timeout: 8000 });

    // If no tasks exist (previous test may have deleted), create one first
    const hasTask = await page.locator("button", { hasText: "Delete" }).count();
    if (hasTask === 0) {
      await page.locator("input[placeholder='Daily report']").fill("Temp task");
      await page.locator("button", { hasText: "Daily 9am" }).click();
      await page.locator("textarea").fill("Temporary task for delete test.");
      await page.locator("button", { hasText: "Create Task" }).click();
      await expect(page.locator("button", { hasText: "Delete" }).first()).toBeVisible({ timeout: 8000 });
    }

    // Open delete confirm
    await page.locator("button", { hasText: "Delete" }).first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByText("This cannot be undone.")).toBeVisible();

    // Confirm delete
    await page.getByRole("dialog").locator("button", { hasText: "Delete" }).click();

    // Empty state or remaining tasks
    await page.waitForTimeout(1000);
    const remaining = await page.locator("button", { hasText: "Delete" }).count();
    if (remaining === 0) {
      await expect(page.getByText("No scheduled tasks yet")).toBeVisible({ timeout: 5000 });
    }
  });
});

test.describe.serial("Cron UX — mobile (390px)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeAll(async ({ request }) => {
    if (!token) await loginViaAPI(request);
  });

  test("cron form is usable at 390px", async ({ page }) => {
    if (!agentId) test.skip();
    await page.goto(`${BASE}/hosted-agents/${agentId}`);
    await page.evaluate((t) => localStorage.setItem("access_token", t), token);
    await page.reload();
    await page.locator("button", { hasText: "Schedule" }).first().click();
    await expect(page.getByText("Scheduled Tasks")).toBeVisible({ timeout: 8000 });

    // Preset chips should be visible (horizontal scroll)
    await expect(page.locator("button", { hasText: "Daily 9am" })).toBeVisible();
    await expect(page.locator("button", { hasText: "Every 15 min" })).toBeVisible();

    // Form fields should be full-width — check they don't overflow
    const nameInput = page.locator("input[placeholder='Daily report']");
    await expect(nameInput).toBeVisible();
    const box = await nameInput.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(300);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390 + 2); // within viewport

    // Preset human preview visible
    await page.locator("button", { hasText: "Daily 9am" }).click();
    await expect(page.getByText("Every day at 09:00 UTC")).toBeVisible();
  });

  test("task cards render single-column at 375px", async ({ page }) => {
    test.use({ viewport: { width: 375, height: 667 } });
    if (!agentId) test.skip();
    await page.goto(`${BASE}/hosted-agents/${agentId}`);
    await page.evaluate((t) => localStorage.setItem("access_token", t), token);
    await page.reload();
    await page.locator("button", { hasText: "Schedule" }).first().click();
    await expect(page.getByText("Scheduled Tasks")).toBeVisible({ timeout: 8000 });
    // If tasks exist, cards should be within viewport width
    const cards = page.locator(".rounded-xl.border").filter({ hasText: "active" });
    const count = await cards.count();
    for (let i = 0; i < count; i++) {
      const box = await cards.nth(i).boundingBox();
      if (box) expect(box.x + box.width).toBeLessThanOrEqual(376);
    }
  });
});
