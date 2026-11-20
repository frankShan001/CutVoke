import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
import base from './playwright.config';

// Merge by object spread: defineConfig(base, overrides) concatenates web servers.
export default defineConfig({
  ...base,
  testDir: './qa',
  testMatch: 'quick-whip.spec.ts',
  webServer: {
    ...(base.webServer as object),
    reuseExistingServer: false,
    env: {
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
      CUTVOKE_E2E_PORT: process.env.CUTVOKE_E2E_PORT || '8799',
      CUTVOKE_E2E_FOLDERS: '1',
      CUTVOKE_EFFECTS_PATH: resolve('../../effects/transitions'),
    },
  },
});
