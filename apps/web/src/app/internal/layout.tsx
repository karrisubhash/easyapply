/**
 * Layout for internal operations pages.
 *
 * Two jobs.
 *
 * Access: under real sign-in (AUTH_MODE=entra) these pages exist only for a
 * holder of the operator app role; anyone else gets the same 404 an unknown
 * address gets. The proxy refuses /api/backend/internal/* on the same rule and
 * the API checks the role again, so this is presentation, not the boundary —
 * but it means an ordinary account never sees an operator screen at all. In the
 * development modes everyone is an operator, as before.
 *
 * Width: the public layout clamps every page to a max width, and an operations
 * table has more columns than that fits — the heartbeat and next-retry columns
 * fall off the right edge, which are two of the columns an operator is actually
 * here to read. The full-bleed trick below breaks out of that clamp without
 * touching the public layout, so an ops page cannot change how the product
 * pages look.
 *
 * It deliberately does NOT add anything to the navigation. Nothing links here;
 * you arrive by typing the URL.
 */

import { notFound } from "next/navigation";

import { resolveIdentity } from "@/server/auth/identity";
import { renderCookies } from "@/server/auth/next";

export default async function InternalLayout({ children }: { children: React.ReactNode }) {
  const identity = await resolveIdentity(await renderCookies());
  if (identity.status === "authenticated" && !identity.operator) notFound();

  return (
    <div className="relative left-1/2 right-1/2 -ml-[50vw] -mr-[50vw] w-screen px-4 sm:px-8">
      {children}
    </div>
  );
}
