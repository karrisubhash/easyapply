import { redirect } from "next/navigation";

import { resolveIdentity } from "@/server/auth/identity";
import { renderCookies } from "@/server/auth/next";

/**
 * Where Microsoft returns the browser after signing out
 * (`${APP_ORIGIN}/signed-out`, registered in the tenant as a redirect URI).
 *
 * Signed out, the root layout renders the sign-in in place of this page, and
 * the notice above it says "You signed out of EasyApply." Signed in (they
 * signed in again, or typed this address), there is nothing to see here.
 *
 * The page decides that itself rather than relying on the layout: Next renders
 * a page and its layout in parallel, so a page that redirected unconditionally
 * would redirect even while the layout is showing the sign-in.
 */
export default async function SignedOutPage() {
  const identity = await resolveIdentity(await renderCookies());
  if (identity.status === "authenticated") redirect("/");
  return null;
}
