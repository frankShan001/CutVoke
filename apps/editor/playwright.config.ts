import { defineConfig } from "@playwright/test";

const port = process.env.CUTVOKE_E2E_PORT || "8799";
const serverUrl = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../../.artifacts/test-results",
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: serverUrl,
    browserName: "chromium",
    channel: process.platform === "win32" ? "chrome" : undefined,
    viewport: { width: 1600, height: 960 },
    locale: "zh-CN",
    launchOptions: { executablePath: process.env.CUTVOKE_E2E_BROWSER },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "python ../../tests/support/e2e_server.py",
    url: `${serverUrl}/api/v1/capabilities`,
    reuseExistingServer: !process.env.CI && (!process.env.CUTVOKE_E2E_PORT || process.env.CUTVOKE_E2E_REUSE === "1"),
    timeout: 120_000,
    env: { CUTVOKE_E2E_PORT: port },
  },
});
