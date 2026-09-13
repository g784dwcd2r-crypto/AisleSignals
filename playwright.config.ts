import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:8799',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: {
    command: `${process.env.AISLESIGNALS_TEST_PYTHON || 'python'} scripts/e2e_server.py`,
    url: 'http://127.0.0.1:8799/api/health',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
