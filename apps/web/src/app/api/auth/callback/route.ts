/**
 * GET /api/auth/callback — where Microsoft sends the browser back after
 * sign-in (AUTH_MODE=entra only). Registered in the tenant as the app's Web
 * redirect URI, `${APP_ORIGIN}/api/auth/callback`.
 *
 * A top-level cross-site navigation by design, so there is no Origin check:
 * what binds it to this browser is the sealed transaction cookie its `state`
 * names (src/server/auth/sign-in-flow.ts). The Host check still applies.
 */

import type { NextRequest } from "next/server";

import { readAuthConfig } from "@/server/auth/config";
import { misconfigured } from "@/server/auth/responses";
import { completeSignIn } from "@/server/auth/sign-in-flow";
import { HOST_REFUSAL, isAllowedHost, refuse } from "@/server/request-guard";

export async function GET(request: NextRequest) {
  if (!isAllowedHost(request.headers.get("host"))) return refuse(...HOST_REFUSAL);

  const config = readAuthConfig();
  if (config.mode === "misconfigured") return misconfigured(config.reason);
  if (config.mode !== "entra") return refuse("Not Found", "not_found", 404);
  return completeSignIn(request, config);
}
