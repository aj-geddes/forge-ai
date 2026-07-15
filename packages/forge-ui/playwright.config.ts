import { defineConfig, devices } from "@playwright/test";

// This suite drives the real Vite dev server in a real browser but mocks
// every backend call via Playwright route interception (see e2e/*.spec.ts)
// -- there is no live gateway in this workstream. It is distinct from the
// repo-root `e2e-tests/` package (CLAUDE.md), which targets an
// already-deployed instance and does not start the app itself.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev -- --port 5173 --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
