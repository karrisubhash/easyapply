/**
 * The seam's two entry points from Next: a route handler's request, and a
 * server component's request scope. Kept apart from identity.ts so that file
 * stays free of Next's request-scoped APIs and can be tested on its own.
 */

import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

import type { CookieReader } from "./identity";

export function requestCookies(request: NextRequest): CookieReader {
  return { get: (name) => request.cookies.get(name)?.value };
}

/** For server components and layouts: the cookies of the request being rendered. */
export async function renderCookies(): Promise<CookieReader> {
  const store = await cookies();
  return { get: (name) => store.get(name)?.value };
}
