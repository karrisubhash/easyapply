/**
 * GET /api/auth/session — who requests from this browser are made as.
 *
 * Read by the web UI and by the Chrome extension, which shows the answer on its
 * review screen ("Saving as …") before anything is sent. The body is the mode,
 * whether anyone is signed in, the account's email and name for display, and
 * its account tag (`WebSession` in @easyapply/shared) — never the session
 * token: the cookie is HttpOnly precisely so that no client code ever holds it.
 *
 * A read, so no Origin check — a cross-site page cannot read the answer
 * (there is no CORS on this server) — but the Host check applies, because DNS
 * rebinding would make a hostile page same-origin and let it read exactly this.
 */

import type { NextRequest } from "next/server";

import { resolveIdentity } from "@/server/auth/identity";
import { requestCookies } from "@/server/auth/next";
import { misconfigured, sessionResponse } from "@/server/auth/responses";
import { HOST_REFUSAL, isAllowedHost, refuse } from "@/server/request-guard";

export async function GET(request: NextRequest) {
  if (!isAllowedHost(request.headers.get("host"))) return refuse(...HOST_REFUSAL);

  const identity = await resolveIdentity(requestCookies(request));
  if (identity.status === "misconfigured") return misconfigured(identity.reason);
  return sessionResponse(identity);
}
