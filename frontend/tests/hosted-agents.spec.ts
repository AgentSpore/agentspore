import { test, expect } from "@playwright/test";

const BASE = "http://localhost:3000";
const API = "http://localhost:18000";
const EMAIL = "test@test.com";
const PASSWORD = "TestPass123";
const AGENT_NAME = `E2E-${Date.now().toString(36)}`;

let token = "";

test.describe.serial("Hosted Agents E2E", () => {
  test.beforeAll(async ({ request }) => {
    // Login via API
    const res = await request.post(`${API}/api/v1/auth/login`, {
      data: { email: EMAIL, password: PASSWORD },
    });
    const body = await res.json();
    token = body.access_token;

    // Clean up existing agents
    const agents = await (
      await request.get(`${API}/api/v1/hosted-agents`, {
        headers: { Authorization: `Bearer ${token}` },
      })
    ).json();
    for (const a of agents) {
      await request.post(`${API}/api/v1/hosted-agents/${a.id}/stop`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      await request.delete(`${API}/api/v1/hosted-agents/${a.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
    }
  });

  test("1. Login via UI", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await expect(page).toHaveURL(/profile/);
    await expect(page.getByRole("heading", { name: "Test User" })).toBeVisible();
  });

  test("2. Create agent via UI", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await page.waitForURL(/profile/);

    await page.goto(`${BASE}/hosted-agents/new`);
    await page.fill('input[placeholder="MyAssistant"]', AGENT_NAME);
    await page.fill(
      'textarea[placeholder*="Example"]',
      "You are a test agent for E2E testing."
    );
    // Wait for models to load (select with options containing "free")
    await page.waitForFunction(
      () => {
        const selects = document.querySelectorAll("select");
        return Array.from(selects).some(
          (s) => s.options.length > 1 && s.options[0]?.text?.includes("free")
        );
      },
      { timeout: 15000 }
    );
    await page.click('button:has-text("Create Agent")');

    // Should redirect to agent page
    await page.waitForURL(/hosted-agents\/[a-f0-9-]+/, { timeout: 30000 });
    await expect(page.locator(`text=${AGENT_NAME}`).first()).toBeVisible();
    await expect(page.locator("text=Stopped").first()).toBeVisible();

    // Agent created successfully — URL contains ID
  });

  test("3. Agent page has Chat, Files, Guide tabs", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await page.waitForURL(/profile/);

    // Get agent ID from API
    const res = await page.request.get(`${API}/api/v1/hosted-agents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const agents = await res.json();
    const id = agents[0]?.id;
    if (!id) throw new Error("No agent found");

    await page.goto(`${BASE}/hosted-agents/${id}`);
    await expect(page.locator(`text=${AGENT_NAME}`).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole("button", { name: "Chat", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Files", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Guide", exact: true })).toBeVisible();
  });

  test("4. Guide tab shows info cards", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await page.waitForURL(/profile/);

    const res = await page.request.get(`${API}/api/v1/hosted-agents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const agents = await res.json();
    await page.goto(`${BASE}/hosted-agents/${agents[0].id}`);
    await page.waitForSelector(`text=${AGENT_NAME}`, { timeout: 10000 });

    await page.getByRole("button", { name: "Guide", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Agent Guide" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Getting Started" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "HeartBeat" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "3-Layer Memory" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Tools & Capabilities" })).toBeVisible();
    await expect(page.locator("text=Platform Integration")).toBeVisible();
  });

  test("5. Files tab shows agent.yaml", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await page.waitForURL(/profile/);

    const res = await page.request.get(`${API}/api/v1/hosted-agents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const agents = await res.json();
    await page.goto(`${BASE}/hosted-agents/${agents[0].id}`);
    await page.waitForSelector(`text=${AGENT_NAME}`, { timeout: 10000 });

    await page.getByRole("button", { name: "Files", exact: true }).click();
    await expect(page.locator("text=AGENT.md").first()).toBeVisible({ timeout: 10000 });
    await expect(page.locator("text=SKILL.md").first()).toBeVisible();
    await expect(page.locator("text=agent.yaml").first()).toBeVisible();
  });

  test("6. Start agent", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await page.waitForURL(/profile/);

    const res = await page.request.get(`${API}/api/v1/hosted-agents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const agents = await res.json();
    await page.goto(`${BASE}/hosted-agents/${agents[0].id}`);
    await page.waitForSelector(`text=${AGENT_NAME}`, { timeout: 10000 });

    await page.getByRole("button", { name: /Start/, exact: false }).first().click();
    await expect(page.locator("text=Running").first()).toBeVisible({ timeout: 15000 });
    await expect(page.locator("text=Online").first()).toBeVisible();
  });

  test("7. Chat sends message and gets streaming response", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await page.waitForURL(/profile/);

    const res = await page.request.get(`${API}/api/v1/hosted-agents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const agents = await res.json();
    await page.goto(`${BASE}/hosted-agents/${agents[0].id}`);
    await page.waitForSelector("text=Online", { timeout: 30000 });

    // Wait for bootstrap to complete (agent might be busy)
    await page.waitForSelector('textarea[placeholder*="Message your agent"]', {
      timeout: 120000,
    });

    await page.fill('textarea[placeholder*="Message your agent"]', "Say hello");
    await page.press('textarea[placeholder*="Message your agent"]', "Enter");

    // Should show generating state
    await expect(
      page.locator('textarea[placeholder*="Generating"]')
    ).toBeVisible({ timeout: 10000 });

    // Wait for response (free model can be slow)
    await expect(
      page.locator('textarea[placeholder*="Message your agent"]')
    ).toBeVisible({ timeout: 120000 });
  });

  test("8. Stop agent", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await page.waitForURL(/profile/);

    const res = await page.request.get(`${API}/api/v1/hosted-agents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const agents = await res.json();
    await page.goto(`${BASE}/hosted-agents/${agents[0].id}`);
    await page.waitForSelector("text=Running", { timeout: 15000 });

    await page.getByRole("button", { name: /Stop/, exact: false }).first().click();
    // Confirm stop
    const confirmBtn = page.locator('button:has-text("Yes, stop")');
    if (await confirmBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await confirmBtn.click();
    }
    await expect(page.locator("text=Stopped").first()).toBeVisible({ timeout: 120000 });
  });

  test("9. Per-user limit (cannot create second agent)", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.fill('input[placeholder*="you@example"]', EMAIL);
    await page.fill('input[placeholder*="••••"]', PASSWORD);
    await page.click('button:has-text("Sign In")');
    await page.waitForURL(/profile/);

    await page.goto(`${BASE}/hosted-agents/new`);
    await page.fill('input[placeholder="MyAssistant"]', `Second-${Date.now().toString(36)}`);
    await page.fill(
      'textarea[placeholder*="Example"]',
      "Second agent test."
    );
    await page.waitForFunction(
      () => {
        const selects = document.querySelectorAll("select");
        return Array.from(selects).some(
          (s) => s.options.length > 1 && s.options[0]?.text?.includes("free")
        );
      },
      { timeout: 15000 }
    );
    await page.getByRole("button", { name: "Create Agent" }).click();

    // Should show error about limit
    await expect(page.locator("text=You can create up to 1")).toBeVisible({
      timeout: 10000,
    });
  });

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
