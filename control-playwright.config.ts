import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/control-e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  reporter: [
    ["list"],
    ["html", { outputFolder: ".local/control-browser-report", open: "never" }],
  ],
  outputDir: ".local/control-browser-results",
  use: {
    ...devices["Desktop Chrome"],
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
