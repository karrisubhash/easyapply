import { createRequire } from "node:module";

import { defineConfig, devices } from "@playwright/test";

import {
  API_DIR,
  API_URL,
  E2E,
  ENTRA_API_URL,
  ENTRA_URL,
  MOCK_ENTRA_AUTHORITY,
  MOCK_ENTRA_ISSUER,
  OWNER_URL,
  PYTHON,
  WEB_DIR,
} from "./e2e/support/env.cjs";

/**
 * Next's own entry point, launched through node.
 *
 * Not `npx`: two `npx next start` invocations racing each other at startup is
 * a lock contention nobody needs, and it fails silently — the second server
 * simply never appears. Resolved rather than hardcoded because npm workspaces
 * hoist `next` to the repo root.
 */
const NEXT_BIN = createRequire(__filename).resolve("next/dist/bin/next");

/**
 * The parent environment, minus the parts that must not reach a child server.
 *
 * A server started by Playwright still needs PATH, SYSTEMROOT and the rest, so
 * the environment is inherited rather than replaced. But two of Playwright's
 * own variables are actively harmful to inherit:
 *
 *   NODE_OPTIONS  carries Playwright's TypeScript loader hooks. A Next
 *                 production server that loads them fails at the first request
 *                 with a module-resolution error out of the webpack runtime,
 *                 and logs nothing that points at the cause.
 *   NODE_ENV      is "test" inside Playwright, and `next start` serving a
 *                 production build under a non-production NODE_ENV is not a
 *                 configuration anybody ships.
 */
function parentEnv(): Record<string, string> {
  const inherited: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (key === "NODE_OPTIONS" || key === "NODE_ENV") continue;
    if (value !== undefined) inherited[key] = value;
  }
  return inherited;
}

/**
 * Browser tests against the real stack.
 *
 * Three servers, all real:
 *
 *   - the FastAPI application on its own port, pointed at its own database and
 *     its own storage root, running its real thread-pool workers. Nothing is
 *     stubbed: a resume is genuinely extracted, a job is genuinely structured, a
 *     resume is genuinely generated, validated and rendered.
 *   - the web app, twice.
 *
 * Twice, because identity is decided server-side. `DEV_USER_EMAIL` is read by
 * the proxy route, so the only way for the browser to be a different person is
 * for it to talk to a different server — and that is the honest way to test
 * ownership, since it exercises the real boundary rather than a header the test
 * chose to send. They share one build, one API and one database.
 *
 * `next start`, not `next dev`: the suite should exercise what ships.
 *
 * Serial, one worker. The tests share a database and truncate it between each
 * other, which is what keeps them independent — and two workers truncating one
 * database would make them anything but.
 */
export default defineConfig({
  testDir: "./e2e/specs",
  outputDir: "./e2e/.artifacts/results",

  fullyParallel: false,
  workers: 1,
  // A retry hides a flake rather than fixing it, and every wait in this suite
  // is on an observable state rather than a timer. If something here is
  // unreliable, that is a finding.
  retries: 0,
  forbidOnly: !!process.env.CI,

  // Generation and extraction are real work on real workers.
  timeout: 90_000,
  expect: { timeout: 15_000 },

  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],

  use: {
    baseURL: OWNER_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    actionTimeout: 15_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: [
    api("api", E2E.apiPort, API_URL, {
      // Pinned: the shell's or services/api/.env's AUTH_MODE must not switch
      // the development-mode servers' API into real sign-in (every request
      // would be a 401) or into a web-only value it refuses to start with.
      AUTH_MODE: "development",
    }),
    webApp("owner", E2E.ownerPort, E2E.ownerEmail),
    webApp("stranger", E2E.strangerPort, E2E.strangerEmail),
    // The same build, deciding identity per browser instead of per server.
    webApp("session", E2E.sessionPort, E2E.sessionSuggestedEmail, {
      AUTH_MODE: "dev-session",
      SESSION_SECRET: E2E.sessionSecret,
    }),

    // Real sign-in: a local OpenID provider shaped like Entra External ID, an
    // API in AUTH_MODE=entra, and a web server in AUTH_MODE=entra in front of it.
    {
      name: "mock-entra",
      command: `"${process.execPath}" e2e/support/mock-entra.mjs`,
      cwd: WEB_DIR,
      url: `http://127.0.0.1:${E2E.mockEntraPort}/health`,
      reuseExistingServer: false,
      stdout: "pipe" as const,
      stderr: "pipe" as const,
      timeout: 30_000,
      env: {
        ...parentEnv(),
        MOCK_ENTRA_PORT: String(E2E.mockEntraPort),
        MOCK_ENTRA_TENANT_ID: E2E.entraTenantId,
        MOCK_ENTRA_CLIENT_ID: E2E.entraClientId,
        MOCK_ENTRA_CLIENT_SECRET: E2E.entraClientSecret,
        MOCK_ENTRA_REDIRECT_URIS: `${ENTRA_URL}/api/auth/callback`,
        MOCK_ENTRA_POST_LOGOUT_URIS: `${ENTRA_URL}/signed-out`,
        MOCK_ENTRA_USERS: JSON.stringify(E2E.entraUsers),
      },
    },
    api("api-entra", E2E.entraApiPort, ENTRA_API_URL, {
      AUTH_MODE: "entra",
      IDENTITY_ASSERTION_SECRET: E2E.entraAssertionSecret,
      ENTRA_TENANT_ID: E2E.entraTenantId,
      ENTRA_ISSUER: MOCK_ENTRA_ISSUER,
      OPERATOR_API_TOKEN: E2E.operatorToken,
    }),
    webApp("entra", E2E.entraPort, "", {
      AUTH_MODE: "entra",
      API_BASE_URL: ENTRA_API_URL,
      APP_ORIGIN: ENTRA_URL,
      ENTRA_TENANT_ID: E2E.entraTenantId,
      ENTRA_AUTHORITY: MOCK_ENTRA_AUTHORITY,
      ENTRA_ISSUER: MOCK_ENTRA_ISSUER,
      ENTRA_CLIENT_ID: E2E.entraClientId,
      ENTRA_CLIENT_SECRET: E2E.entraClientSecret,
      SESSION_SECRET: E2E.entraSessionSecret,
      IDENTITY_ASSERTION_SECRET: E2E.entraAssertionSecret,
      OPERATOR_API_TOKEN: E2E.operatorToken,
    }),
  ],
});

/**
 * One FastAPI process: the real application on its own port, pointed at the
 * suite's database and storage root, running its real thread-pool workers.
 */
function api(name: string, port: number, url: string, extra: Record<string, string>) {
  return {
    name,
    command: `"${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port ${port} --log-level info`,
    cwd: API_DIR,
    url: `${url}/health`,
    reuseExistingServer: false,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
    timeout: 120_000,
    env: {
      // Inherited explicitly. Playwright documents `env` as replacing the
      // environment rather than extending it, and a Windows process started
      // without SYSTEMROOT and PATH does not get far.
      ...parentEnv(),
      DATABASE_URL: E2E.databaseUrl,
      RESUME_STORAGE_ROOT: E2E.storageRoot,
      // The real in-process pools. The point of this suite is that the work
      // actually happens and the UI converges on it.
      RESUME_QUEUE_BACKEND: "thread",
      TAILORING_QUEUE_BACKEND: "thread",
      // Off: the reaper's sweep would race a suite that truncates tables.
      RESUME_REAPER_ENABLED: "false",
      // Explicitly off, per the brief. The deterministic generator is what
      // ships by default and is what these tests must exercise.
      RESUME_AI_ENABLED: "false",
      JOB_AI_ENABLED: "false",
      TAILORING_AI_ENABLED: "false",
      ...extra,
    },
  };
}

/**
 * One web server, bound to one identity.
 *
 * `DEV_USER_EMAIL` is passed through the environment. Next loads `.env.local`
 * but does not overwrite a variable already present in `process.env`, so this
 * wins over the developer's own setting — which the suite asserts directly in
 * `identity.spec.ts` rather than assuming.
 */
function webApp(name: string, port: number, email: string, extra: Record<string, string> = {}) {
  return {
    name,
    command: `"${process.execPath}" "${NEXT_BIN}" start -p ${port}`,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
    timeout: 120_000,
    env: {
      // See `parentEnv` for what is deliberately not inherited.
      ...parentEnv(),
      API_BASE_URL: API_URL,
      DEV_USER_EMAIL: email,
      NODE_ENV: "production",
      // The build `prepare` produced. Not `.next`, which a development server
      // beside this suite would be rewriting under it.
      NEXT_DIST_DIR: E2E.distDir,
      // Explicit, so a developer's own .env.local cannot switch a server's mode.
      AUTH_MODE: "development",
      ...extra,
    },
  };
}
