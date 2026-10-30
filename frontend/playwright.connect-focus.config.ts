import { defineConfig } from "@playwright/test";

const baseURL = "http://127.0.0.1:3187";
if (process.env.PLAYWRIGHT_BASE_URL && process.env.PLAYWRIGHT_BASE_URL !== baseURL) {
  throw new Error("PLAYWRIGHT_BASE_URL must be http://127.0.0.1:3187");
}

export default defineConfig({
  testDir: "./tests",
  testMatch: "connect-focus.spec.ts",
  timeout: 180000,
  expect: { timeout: 30000 },
  use: { baseURL, headless: true, serviceWorkers: "block" },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "firefox", use: { browserName: "firefox" } },
    { name: "webkit", use: { browserName: "webkit" } },
  ],
});
