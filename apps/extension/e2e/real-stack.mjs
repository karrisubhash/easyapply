/**
 * The browser suite against the REAL stack: FastAPI + PostgreSQL + the Next.js
 * web app and its proxy, with the built extension in Chromium.
 *
 *   npm run test:browser:real --workspace @easyapply/extension
 *
 * What it starts, all on its own ports and data so that a developer's servers
 * (web :3000, API :8000) and the web app's own e2e suite (:3100-3102, :8100,
 * easyapply_e2e) keep running beside it:
 *
 *   database   easyapply_ext_e2e, dropped and rebuilt from the real Alembic
 *              migrations (apps/web/e2e/support/db_admin.py refuses any
 *              database not ending in _e2e)
 *   API        127.0.0.1:8300, real in-process workers, every AI switch OFF —
 *              the deterministic structurer, matcher and tailor are what run
 *   web app    127.0.0.1:3300, `next start` of a production build in
 *              apps/web/.next-ext-e2e, AUTH_MODE=dev-session (a session per
 *              browser: the multi-user case) and PROXY_ALLOWED_EXTENSION_IDS
 *              naming this extension
 *
 * Then runs `playwright test` with EXTENSION_E2E_WEB_URL and
 * EXTENSION_E2E_API_URL set (the specs seed confirmed profiles through the
 * API directly — setup, not the thing under test), and stops everything it
 * started, whatever the outcome.
 *
 * With `--entra` (or EXT_E2E_AUTH=entra) the same run uses REAL SIGN-IN: the
 * API and the web app in AUTH_MODE=entra, signing in through a local OpenID
 * provider shaped like Microsoft Entra External ID
 * (apps/web/e2e/support/mock-entra.mjs, in this process, on :3391), whose
 * account picker takes any address so each run's people exist. Nothing about a
 * real tenant is exercised.
 *
 * EXT_E2E_SKIP_BUILD=1 reuses an existing build. Extra arguments go to
 * Playwright (e.g. `-- e2e/workflow.spec.ts`).
 */

import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const EXTENSION_DIR = resolve(HERE, "..");
const REPO = resolve(EXTENSION_DIR, "../..");
const WEB_DIR = resolve(REPO, "apps/web");
const API_DIR = resolve(REPO, "services/api");
const ARTIFACTS = resolve(EXTENSION_DIR, ".artifacts/real-stack");
const PYTHON =
  process.env.E2E_PYTHON ?? resolve(API_DIR, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");

export const REAL = {
  databaseUrl:
    process.env.EXT_E2E_DATABASE_URL ?? "postgresql+psycopg://postgres:subhash@localhost:5432/easyapply_ext_e2e",
  apiPort: Number(process.env.EXT_E2E_API_PORT ?? 8300),
  webPort: Number(process.env.EXT_E2E_WEB_PORT ?? 3300),
  distDir: ".next-ext-e2e",
  sessionSecret: "extension-e2e-only-dev-session-secret-0123456789",
  extensionId: "jjhciekcmhmngciccfgignmmlgjknakk",
  // Real sign-in (--entra). Test values, used for nothing else.
  mockEntraPort: Number(process.env.EXT_E2E_MOCK_ENTRA_PORT ?? 3391),
  entraTenantId: "e4e4e4e4-1111-4222-8333-444444444444",
  entraClientId: "c4c4c4c4-1111-4222-8333-444444444444",
  entraClientSecret: "extension-e2e-only-mock-entra-client-secret",
  entraSessionSecret: "extension-e2e-only-entra-session-secret-0123456789",
  assertionSecret: "extension-e2e-only-identity-assertion-secret-012345",
};

const ENTRA = process.argv.includes("--entra") || process.env.EXT_E2E_AUTH === "entra";
const PLAYWRIGHT_ARGS = process.argv.slice(2).filter((arg) => arg !== "--entra");

const API_URL = `http://127.0.0.1:${REAL.apiPort}`;
const WEB_URL = `http://127.0.0.1:${REAL.webPort}`;

/** The parent environment minus what must not reach a child server (see apps/web/playwright.config.ts). */
function parentEnv() {
  const env = { ...process.env };
  delete env.NODE_OPTIONS;
  delete env.NODE_ENV;
  return env;
}

function step(label, file, args, options = {}) {
  process.stdout.write(`  ${label}… `);
  try {
    execFileSync(file, args, { stdio: "pipe", ...options });
    process.stdout.write("ok\n");
  } catch (error) {
    process.stdout.write("FAILED\n");
    throw new Error(`${label} failed:\n${[error.stdout?.toString(), error.stderr?.toString()].filter(Boolean).join("\n")}`);
  }
}

async function waitFor(url, label, timeoutMs = 120_000) {
  const started = Date.now();
  for (;;) {
    try {
      const response = await fetch(url, { redirect: "manual" });
      if (response.status < 500) return;
    } catch {
      // not up yet
    }
    if (Date.now() - started > timeoutMs) throw new Error(`${label} did not come up at ${url}`);
    await new Promise((done) => setTimeout(done, 500));
  }
}

const children = [];
let provider = null;

function start(label, file, args, options) {
  const child = spawn(file, args, { stdio: ["ignore", "pipe", "pipe"], ...options });
  const log = resolve(ARTIFACTS, `${label}.log`);
  const chunks = [];
  child.stdout.on("data", (chunk) => chunks.push(chunk));
  child.stderr.on("data", (chunk) => chunks.push(chunk));
  child.on("exit", () => writeFileSync(log, Buffer.concat(chunks)));
  children.push({ label, child, log, chunks });
  return child;
}

function stopAll() {
  provider?.close();
  for (const { label, child, log, chunks } of children) {
    if (child.exitCode === null) {
      if (process.platform === "win32") {
        try {
          execFileSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
        } catch {
          // already gone
        }
      } else {
        child.kill("SIGTERM");
      }
    }
    writeFileSync(log, Buffer.concat(chunks));
    console.log(`  ${label} log: ${log}`);
  }
}

async function main() {
  rmSync(ARTIFACTS, { recursive: true, force: true });
  mkdirSync(resolve(ARTIFACTS, "storage"), { recursive: true });
  console.log("Preparing the extension's real-stack run");

  step("recreating easyapply_ext_e2e", PYTHON, [resolve(WEB_DIR, "e2e/support/db_admin.py"), "recreate"], {
    cwd: API_DIR,
    env: { ...process.env, E2E_DATABASE_URL: REAL.databaseUrl },
  });
  step("applying migrations", PYTHON, ["-m", "alembic", "upgrade", "head"], {
    cwd: API_DIR,
    env: { ...process.env, DATABASE_URL: REAL.databaseUrl },
  });

  const nextBin = createRequire(resolve(WEB_DIR, "package.json")).resolve("next/dist/bin/next");
  if (process.env.EXT_E2E_SKIP_BUILD === "1" && existsSync(resolve(WEB_DIR, REAL.distDir, "BUILD_ID"))) {
    console.log("  building the web app… skipped (EXT_E2E_SKIP_BUILD=1)");
  } else {
    // `next build` rewrites next-env.d.ts to point at the build directory, and
    // adds the build directory's types to tsconfig.json's `include`. Neither
    // is this run's to change: put both back, byte for byte.
    const touched = ["next-env.d.ts", "tsconfig.json"].map((name) => resolve(WEB_DIR, name));
    const originals = touched.map((file) => (existsSync(file) ? readFileSync(file) : null));
    try {
      step("building the web app into .next-ext-e2e", process.execPath, [nextBin, "build"], {
        cwd: WEB_DIR,
        env: { ...parentEnv(), NEXT_DIST_DIR: REAL.distDir },
      });
    } finally {
      touched.forEach((file, index) => {
        const original = originals[index];
        if (original) writeFileSync(file, original);
      });
    }
  }

  let apiAuth = { AUTH_MODE: "development" };
  let webAuth = { AUTH_MODE: "dev-session", SESSION_SECRET: REAL.sessionSecret };
  if (ENTRA) {
    const { startMockEntra } = await import(pathToFileURL(resolve(WEB_DIR, "e2e/support/mock-entra.mjs")).href);
    provider = await startMockEntra({
      port: REAL.mockEntraPort,
      tenantId: REAL.entraTenantId,
      clients: [
        {
          clientId: REAL.entraClientId,
          clientSecret: REAL.entraClientSecret,
          redirectUris: [`${WEB_URL}/api/auth/callback`],
          postLogoutRedirectUris: [`${WEB_URL}/signed-out`],
        },
      ],
      users: [],
      allowAnyAccount: true,
    });
    console.log(`  local Entra-shaped provider: authority ${provider.authority}, issuer ${provider.issuer}`);
    apiAuth = {
      AUTH_MODE: "entra",
      IDENTITY_ASSERTION_SECRET: REAL.assertionSecret,
      ENTRA_TENANT_ID: REAL.entraTenantId,
      ENTRA_ISSUER: provider.issuer,
    };
    webAuth = {
      AUTH_MODE: "entra",
      APP_ORIGIN: WEB_URL,
      ENTRA_TENANT_ID: REAL.entraTenantId,
      ENTRA_AUTHORITY: provider.authority,
      ENTRA_ISSUER: provider.issuer,
      ENTRA_CLIENT_ID: REAL.entraClientId,
      ENTRA_CLIENT_SECRET: REAL.entraClientSecret,
      SESSION_SECRET: REAL.entraSessionSecret,
      IDENTITY_ASSERTION_SECRET: REAL.assertionSecret,
    };
  }

  start("api", PYTHON, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(REAL.apiPort), "--log-level", "info"], {
    cwd: API_DIR,
    env: {
      ...parentEnv(),
      DATABASE_URL: REAL.databaseUrl,
      RESUME_STORAGE_ROOT: resolve(ARTIFACTS, "storage"),
      RESUME_QUEUE_BACKEND: "thread",
      TAILORING_QUEUE_BACKEND: "thread",
      RESUME_REAPER_ENABLED: "false",
      RESUME_AI_ENABLED: "false",
      JOB_AI_ENABLED: "false",
      TAILORING_AI_ENABLED: "false",
      TAILORING_AI_USER_IDS: "",
      // Pinned either way: a shell's or services/api/.env's AUTH_MODE must not decide.
      ...apiAuth,
    },
  });
  start("web", process.execPath, [nextBin, "start", "-p", String(REAL.webPort), "-H", "127.0.0.1"], {
    cwd: WEB_DIR,
    env: {
      ...parentEnv(),
      NODE_ENV: "production",
      NEXT_DIST_DIR: REAL.distDir,
      API_BASE_URL: API_URL,
      ...webAuth,
      PROXY_ALLOWED_EXTENSION_IDS: REAL.extensionId,
      DEV_USER_EMAIL: "nobody@ext-e2e.easyapply.test",
    },
  });
  await waitFor(`${API_URL}/health`, "the API");
  await waitFor(`${WEB_URL}/api/auth/session`, "the web app");
  console.log(`Ready: API ${API_URL}, web ${WEB_URL} (${ENTRA ? "entra" : "dev-session"})\n`);

  const playwright = createRequire(resolve(EXTENSION_DIR, "package.json")).resolve("@playwright/test/cli");
  const code = await new Promise((done) => {
    const run = spawn(process.execPath, [playwright, "test", ...PLAYWRIGHT_ARGS], {
      cwd: EXTENSION_DIR,
      stdio: "inherit",
      env: {
        ...process.env,
        EXTENSION_E2E_WEB_URL: WEB_URL,
        EXTENSION_E2E_API_URL: API_URL,
        ...(ENTRA
          ? {
              EXTENSION_E2E_AUTH: "entra",
              EXTENSION_E2E_ENTRA_TENANT_ID: REAL.entraTenantId,
              EXTENSION_E2E_ENTRA_ISSUER: provider.issuer,
              EXTENSION_E2E_ASSERTION_SECRET: REAL.assertionSecret,
            }
          : {}),
      },
    });
    run.on("exit", (status) => done(status ?? 1));
  });
  return code;
}

let exitCode = 1;
try {
  exitCode = await main();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
} finally {
  stopAll();
}
process.exit(exitCode);
