import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "research-experience.spec.ts",
  fullyParallel: false,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:4173",
    channel: "chrome",
    headless: true,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-chrome",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile-chrome",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 360, height: 800 },
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
  webServer: {
    command: "node tests/serve-static.mjs",
    port: 4173,
    reuseExistingServer: false,
    timeout: 15_000,
  },
});
