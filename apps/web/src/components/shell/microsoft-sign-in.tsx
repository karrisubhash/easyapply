"use client";

import { usePathname, useSearchParams } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { AUTH_ERROR_PARAM, SESSION_NOTICE_PARAM } from "@/lib/session";

/**
 * Real sign-in, shown in place of every page when this server runs
 * AUTH_MODE=entra and the browser has no session.
 *
 * A plain same-origin form POST — not a fetch — because the answer is a
 * redirect to Microsoft the whole tab must follow. It sends only where to come
 * back to; the server decides everything else (state, nonce, PKCE) and keeps
 * it in a sealed cookie, so nothing about the sign-in is ever in this page.
 */
export function MicrosoftSignIn() {
  const pathname = usePathname();
  const params = useSearchParams();
  const [pending, setPending] = React.useState(false);

  // Back from Microsoft with the browser's Back button: the page is restored
  // from the cache with the button still saying "Redirecting…".
  React.useEffect(() => {
    const onShow = (event: PageTransitionEvent) => {
      if (event.persisted) setPending(false);
    };
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, []);

  const kept = new URLSearchParams(params.toString());
  kept.delete(AUTH_ERROR_PARAM);
  kept.delete(SESSION_NOTICE_PARAM);
  const query = kept.toString();
  const returnTo = pathname === "/signed-out" ? "/" : `${pathname}${query ? `?${query}` : ""}`;

  return (
    <Card className="mx-auto max-w-md">
      <CardHeader>
        {/* The page's one heading: this is what every page is while signed out. */}
        <h1 className="text-lg font-semibold leading-none tracking-tight">Sign in to EasyApply</h1>
        <CardDescription>
          Sign in with your Microsoft account. Your profile, resumes, jobs and tailored resumes — and
          every job the EasyApply extension saves from this browser — belong to the account you sign in
          with.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form method="post" action="/api/auth/sign-in" onSubmit={() => setPending(true)}>
          <input type="hidden" name="return_to" value={returnTo} />
          <Button type="submit" disabled={pending} aria-busy={pending || undefined}>
            {pending ? "Redirecting to Microsoft…" : "Sign in with Microsoft"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
