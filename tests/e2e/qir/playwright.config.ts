import { join, resolve } from "node:path";

import {
  defineConfig,
  devices,
} from "../../../apps/web/node_modules/@playwright/test/index.mjs";

const repositoryRoot = resolve(__dirname, "../../..");

export default defineConfig({
  testDir: ".",
  testMatch: "public-demo-static.spec.ts",
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
        viewport: { width: 390, height: 844 },
        isMobile: true,
      },
    },
  ],
  webServer: {
    command: "node tests/serve-static.mjs",
    cwd: join(repositoryRoot, "apps/web"),
    port: 4173,
    reuseExistingServer: false,
    timeout: 15_000,
  },
});
