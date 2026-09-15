import { defineConfig, devices } from '@playwright/test';

const testPort = Number(process.env.AISLESIGNALS_E2E_PORT || '8799');
if (!Number.isInteger(testPort) || testPort < 1024 || testPort > 65535) {
  throw new Error('AISLESIGNALS_E2E_PORT must be an unprivileged TCP port');
}
const testOrigin = `http://127.0.0.1:${testPort}`;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Windows CI starts a fresh Python/SQLite fixture for each stateful journey.
  // Module loading can exceed the generic 30-second test budget under runner
  // contention, while each test can still set a tighter interaction timeout.
  timeout: process.platform === 'win32' ? 45_000 : 30_000,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: testOrigin,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: {
    command: `${process.env.AISLESIGNALS_TEST_PYTHON || 'python'} scripts/e2e_server.py`,
    url: `${testOrigin}/api/health`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
