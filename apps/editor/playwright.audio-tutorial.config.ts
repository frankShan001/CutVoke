import { defineConfig } from '@playwright/test';
const port = Number(process.env.CUTVOKE_AUDIO_TUTORIAL_PORT || '8807');
const targets = [
  { name: 'original', port, restored: '0' },
  { name: 'restored-package', port: port + 1, restored: '1' },
];
export default defineConfig({
  testDir: './e2e-audio-tutorial', workers: 1, timeout: 90_000,
  outputDir: '../../.artifacts/test-results/audio-tutorial',
  expect: { timeout: 15_000 },
  use: { browserName: 'chromium',
    channel: process.platform === 'win32' ? 'chrome' : undefined,
    launchOptions: { executablePath: process.env.CUTVOKE_E2E_BROWSER },
    viewport: { width: 1600, height: 960 }, locale: 'zh-CN', trace: 'retain-on-failure' },
  projects: targets.map(target => ({ name: target.name,
    use: { baseURL: `http://127.0.0.1:${target.port}` } })),
  webServer: targets.map(target => ({ command: 'python ../../tests/support/audio_tutorial_server.py',
    url: `http://127.0.0.1:${target.port}/api/v1/capabilities`, reuseExistingServer: false,
    timeout: 240_000, env: { CUTVOKE_AUDIO_TUTORIAL_PORT: String(target.port),
      CUTVOKE_AUDIO_TUTORIAL_RESTORED: target.restored, PYTHONIOENCODING: 'utf-8' } })),
});
