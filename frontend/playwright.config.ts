import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 180000,
  expect: { timeout: 30000 },
  use: {
    baseURL: "http://localhost:3000",
    headless: true,
    screenshot: "on",
    video: "on",
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
});
