/**
 * One place that knows where the end-to-end stack lives.
 *
 * CommonJS on purpose. Three very different loaders read this file — bare Node
 * ESM (`prepare.mjs`), Playwright's TypeScript config loader, and the compiled
 * specs — and Playwright transpiles to CJS, which cannot import an ES module.
 * CJS is the one format all three can agree on. `env.d.cts` beside it is what
 * makes it typed on the TypeScript side.
 *
 * Everything here is deliberately DIFFERENT from the development defaults: a
 * suite that truncates tables between tests must not be able to reach the
 * database somebody is using.
 */

const { resolve } = require("node:path");

const WEB_DIR = resolve(__dirname, "../..");
const REPO_DIR = resolve(WEB_DIR, "../..");
const API_DIR = resolve(REPO_DIR, "services/api");

const PYTHON =
  process.env.E2E_PYTHON ??
  resolve(API_DIR, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");

/** Where the suite's own files go. Gitignored; wiped by `prepare`. */
const ARTIFACTS = resolve(__dirname, "../.artifacts");

const E2E = {
  /** Its own database. `_e2e`, never `_test` (pytest owns that) and never the
   *  development one. `db_admin.py` refuses anything not ending in `_e2e`. */
  databaseUrl:
    process.env.E2E_DATABASE_URL ??
    "postgresql+psycopg://postgres:subhash@localhost:5432/easyapply_e2e",

  apiPort: 8100,
  /** Two web servers, because identity is a server-side environment variable
   *  and the only honest way to be a different user is to be a different
   *  server. See apps/web/src/app/api/backend/[...path]/route.ts. */
  ownerPort: 3100,
  strangerPort: 3101,
  /** A third web server, in AUTH_MODE=dev-session: one server, a session per
   *  browser, so identity is decided by who signed in rather than by which
   *  server was reached — the path real sign-in will take. See
   *  docs/authentication.md and specs/session.spec.ts. */
  sessionPort: 3102,
  /** Its signing secret. A test value, used for nothing else. */
  sessionSecret: "e2e-only-dev-session-secret-0123456789abcdef",
  /** What its sign-in form offers. Offered, never assumed: nobody is this
   *  account until they press Sign in. */
  sessionSuggestedEmail: "suggested@e2e.easyapply.test",

  ownerEmail: "owner@e2e.easyapply.test",
  strangerEmail: "stranger@e2e.easyapply.test",

  /** Real sign-in (AUTH_MODE=entra) against a local OpenID provider shaped
   *  like Entra External ID (support/mock-entra.mjs): its own web server, and
   *  its own API process in AUTH_MODE=entra — one API cannot be in both modes,
   *  and a development-mode API refuses the entra web server's assertions.
   *  Same database and storage as the rest. See specs/entra.spec.ts. */
  entraPort: 3103,
  entraApiPort: 8101,
  mockEntraPort: 3190,
  entraTenantId: "e2e0e2e0-1111-4222-8333-444444444444",
  entraClientId: "c11e0000-1111-4222-8333-444444444444",
  /** Test values, used for nothing else. */
  entraClientSecret: "e2e-only-mock-entra-client-secret",
  entraSessionSecret: "e2e-only-entra-session-secret-0123456789abcdef",
  entraAssertionSecret: "e2e-only-identity-assertion-secret-0123456789",
  operatorToken: "e2e-only-operator-token-0123456789",
  /** The people the provider's account picker offers. Two of them share an
   *  email on purpose: the account is the Entra object id, never the address. */
  entraUsers: [
    { id: "ada", name: "Ada Lovelace", email: "ada@e2e.easyapply.test", oid: "0a0a0a0a-0000-4000-8000-00000000000a" },
    { id: "grace", name: "Grace Hopper", email: "grace@e2e.easyapply.test", oid: "0b0b0b0b-0000-4000-8000-00000000000b" },
    { id: "ada-twin", name: "Ada Twin", email: "ada@e2e.easyapply.test", oid: "0c0c0c0c-0000-4000-8000-00000000000c" },
    { id: "olive", name: "Olive Operator", email: "olive@e2e.easyapply.test", oid: "0d0d0d0d-0000-4000-8000-00000000000d", claims: { roles: ["EasyApply.Operator"] } },
    { id: "nameless", name: "unknown", email: null, oid: "0e0e0e0e-0000-4000-8000-00000000000e" },
    { id: "wrong-audience", name: "Mallory", email: "mallory@e2e.easyapply.test", oid: "0f0f0f0f-0000-4000-8000-00000000000f", claims: { aud: "99999999-1111-4222-8333-444444444444" } },
  ],

  /** A build directory of its own.
   *
   *  `next dev` rewrites `.next` continuously, so a production build served out
   *  of the same directory answers every route with a webpack runtime error.
   *  The suite therefore builds somewhere else, and a developer's own dev
   *  server can keep running beside it. Read by `next.config.ts`. */
  distDir: process.env.E2E_NEXT_DIST_DIR ?? ".next-e2e",

  storageRoot: resolve(ARTIFACTS, "storage"),
  downloadDir: resolve(ARTIFACTS, "downloads"),
  fixtureResume: resolve(ARTIFACTS, "fixture-resume.pdf"),

  /** The evaluation dataset's resume: contact block, summary, labelled skill
   *  rows, two jobs with wrapped bullets, projects, certifications and real
   *  hyperlinks. The plain fixture above has none of that, which is why a green
   *  suite said nothing about whether any of it survived to the screen. */
  goldenResume: resolve(ARTIFACTS, "golden-resume.pdf"),
};

exports.WEB_DIR = WEB_DIR;
exports.REPO_DIR = REPO_DIR;
exports.API_DIR = API_DIR;
exports.PYTHON = PYTHON;
exports.E2E = E2E;
exports.OWNER_URL = `http://127.0.0.1:${E2E.ownerPort}`;
exports.STRANGER_URL = `http://127.0.0.1:${E2E.strangerPort}`;
exports.SESSION_URL = `http://127.0.0.1:${E2E.sessionPort}`;
exports.API_URL = `http://127.0.0.1:${E2E.apiPort}`;
exports.ENTRA_URL = `http://127.0.0.1:${E2E.entraPort}`;
exports.ENTRA_API_URL = `http://127.0.0.1:${E2E.entraApiPort}`;
/** What the provider serves discovery from, and the issuer its tokens carry (another host, like External ID). */
exports.MOCK_ENTRA_AUTHORITY = `http://127.0.0.1:${E2E.mockEntraPort}/${E2E.entraTenantId}/v2.0`;
exports.MOCK_ENTRA_ISSUER = `http://localhost:${E2E.mockEntraPort}/${E2E.entraTenantId}/v2.0`;
