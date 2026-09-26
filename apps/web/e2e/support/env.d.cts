/**
 * Types for `env.cjs`.
 *
 * The module is JavaScript because `prepare.mjs` runs under bare Node before
 * any TypeScript tooling exists, and Playwright's config loader needs the same
 * constants from a CommonJS file. One runtime module, one declaration file,
 * rather than two copies of the port numbers drifting apart.
 */

export const WEB_DIR: string;
export const REPO_DIR: string;
export const API_DIR: string;
export const PYTHON: string;

export interface E2EConfig {
  databaseUrl: string;
  apiPort: number;
  ownerPort: number;
  strangerPort: number;
  sessionPort: number;
  sessionSecret: string;
  sessionSuggestedEmail: string;
  ownerEmail: string;
  strangerEmail: string;
  entraPort: number;
  entraApiPort: number;
  mockEntraPort: number;
  entraTenantId: string;
  entraClientId: string;
  entraClientSecret: string;
  entraSessionSecret: string;
  entraAssertionSecret: string;
  operatorToken: string;
  entraUsers: { id: string; name: string; email: string | null; oid: string; claims?: Record<string, unknown> }[];
  distDir: string;
  storageRoot: string;
  downloadDir: string;
  fixtureResume: string;
  goldenResume: string;
}

export const E2E: E2EConfig;
export const OWNER_URL: string;
export const STRANGER_URL: string;
export const SESSION_URL: string;
export const API_URL: string;
export const ENTRA_URL: string;
export const ENTRA_API_URL: string;
export const MOCK_ENTRA_AUTHORITY: string;
export const MOCK_ENTRA_ISSUER: string;
