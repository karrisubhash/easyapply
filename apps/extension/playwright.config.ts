import { defineConfig } from "@playwright/test";

/**
 * The extension in real Chromium: the built, unpacked extension loaded into a
 * persistent profile, job-site pages served from the test fixtures at their
 * real https://job-boards.greenhouse.io addresses (so Chrome's own content
 * script matching decides where it runs), and the web app it saves to either a
 * recording stub or — with EXTENSION_E2E_WEB_URL — a real one. See e2e/env.ts.
 *
 * One worker: every test launches its own browser with the extension in it,
 * and the stub listens on one fixed port the build was pointed at.
 */
export default defineConfig({
  testDir: "./e2e",
  outputDir: "./.artifacts/e2e-results",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
});
