import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://127.0.0.1:8799",
    browserName: "chromium",
    channel: process.platform === "win32" ? "chrome" : undefined,
    viewport: { width: 1600, height: 960 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "python ../../tests/e2e_server.py",
    url: "http://127.0.0.1:8799/api/v1/capabilities",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { CUTVOKE_E2E_PORT: "8799" },
  },
});
