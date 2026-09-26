/**
 * What the session routes answer. The body is `WebSession` from
 * @easyapply/shared — the shape both the web UI and the extension read.
 */

import type { WebSession } from "@easyapply/shared";
import { NextResponse } from "next/server";

import { refuse } from "@/server/request-guard";

import type { Identity } from "./identity";

const NO_STORE = { "Cache-Control": "no-store" };

export function sessionBody(identity: Exclude<Identity, { status: "misconfigured" }>): WebSession {
  return identity.status === "authenticated"
    ? {
        mode: identity.mode,
        authenticated: true,
        email: identity.email,
        name: identity.name,
        sessionTag: identity.sessionTag,
      }
    : { mode: identity.mode, authenticated: false, email: null, name: null, sessionTag: null };
}

export function sessionResponse(
  identity: Exclude<Identity, { status: "misconfigured" }>,
  init: { status?: number; setCookie?: string; extra?: Record<string, unknown> } = {},
): Response {
  const headers: Record<string, string> = { ...NO_STORE };
  if (init.setCookie) headers["Set-Cookie"] = init.setCookie;
  return NextResponse.json({ ...sessionBody(identity), ...init.extra }, { status: init.status ?? 200, headers });
}

export function misconfigured(reason: string): Response {
  console.error(`auth.misconfigured: ${reason}`);
  return refuse("Sign-in is misconfigured on this server.", "auth_misconfigured", 500);
}
