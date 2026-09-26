/**
 * POST /api/auth/sign-out — end this browser's session.
 *
 * Same-origin only, for the same reason as sign-in: a cross-site page must not
 * be able to change which account the browser is in. Signing out ends the
 * session for the extension too — it presents the same cookie — which is the
 * point: there is one session per browser, not one per client.
 *
 * The cookie is cleared, not revoked: both session kinds are stateless, so a
 * copy of the token taken before sign-out stays valid until it expires (at
 * most SESSION_MAX_AGE_SECONDS; rotating SESSION_SECRET without a previous
 * secret ends every session at once). Under real sign-in the answer also
 * carries `logoutUrl`, Microsoft's end-session endpoint: the client navigates
 * there so the Microsoft session ends too, and Microsoft returns the browser to
 * `${APP_ORIGIN}/signed-out`. Without that, the next "Sign in with Microsoft"
 * on a shared computer would still offer the previous person's account.
 */

import type { NextRequest } from "next/server";

import { readAuthConfig } from "@/server/auth/config";
import { clearedSessionCookie, isHttps, sessionCookieName } from "@/server/auth/identity";
import { discover, logoutUrl } from "@/server/auth/oidc";
import { sessionResponse } from "@/server/auth/responses";
import { CROSS_SITE_REFUSAL, HOST_REFUSAL, isAllowedHost, isSameOrigin, refuse } from "@/server/request-guard";

export async function POST(request: NextRequest) {
  const host = request.headers.get("host");
  if (!isAllowedHost(host)) return refuse(...HOST_REFUSAL);
  if (!isSameOrigin(request, host)) return refuse(...CROSS_SITE_REFUSAL);

  const config = readAuthConfig();
  if (config.mode === "development") {
    return refuse(
      "Signing out is not used here: this server acts as its configured identity (AUTH_MODE=development).",
      "sign_in_not_available",
      404,
    );
  }

  if (config.mode === "entra") {
    let url = `${config.appOrigin}/signed-out`;
    try {
      url = logoutUrl(await discover(config.entra), config.entra) ?? url;
    } catch {
      // Microsoft unreachable: EasyApply's session still ends, here and now.
    }
    console.info("auth.sign_out");
    return sessionResponse(
      { status: "signed_out", mode: "entra" },
      { setCookie: clearedSessionCookie(config.secureCookies, sessionCookieName(config)), extra: { logoutUrl: url } },
    );
  }

  // Cleared even when misconfigured: removing a cookie can never grant anything.
  return sessionResponse(
    { status: "signed_out", mode: "dev-session" },
    { setCookie: clearedSessionCookie(isHttps(request)) },
  );
}
