import type { Metadata } from "next";
import Link from "next/link";
import * as React from "react";

import { Providers } from "@/app/providers";
import { AppNav } from "@/components/shell/app-nav";
import { DevSignIn } from "@/components/shell/dev-sign-in";
import { MicrosoftSignIn } from "@/components/shell/microsoft-sign-in";
import { SignInNotice } from "@/components/shell/sign-in-notice";
import { SignOutButton } from "@/components/shell/sign-out-button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { readAuthConfig } from "@/server/auth/config";
import { resolveIdentity } from "@/server/auth/identity";
import { renderCookies } from "@/server/auth/next";

import "./globals.css";

export const metadata: Metadata = {
  title: "EasyApply",
  description: "Build a confirmed career profile and tailor it to a job.",
};

/**
 * The shell is rendered per request, never prerendered.
 *
 * It names the person looking at it, and reads that from the request — the
 * server environment or the session cookie. Without this, Next prerenders the
 * layout at BUILD time and freezes whatever `DEV_USER_EMAIL` happened to be set
 * to then — while the proxy route that actually attaches the identity is a
 * route handler and reads it at request time. The two then disagree, and the header confidently names
 * somebody the application is not acting as. That is not a cosmetic bug: the
 * whole product is scoped to one identity, and a user looking at an unexpectedly
 * empty profile needs the header to tell them the truth about which account
 * they are in.
 *
 * Applied to the root layout, so it covers every page. Nothing here is
 * cacheable across users anyway — every screen shows one person's data — so the
 * static shells this gives up were never worth anything.
 */
export const dynamic = "force-dynamic";

/**
 * Who the user is, from the same seam the proxy attaches upstream
 * (src/server/auth/identity.ts) — so what is displayed here, what the Chrome
 * extension says it will save as, and what the API actually resolves cannot
 * disagree.
 *
 * Shown rather than hidden because everything in this application is scoped to
 * one identity, and a user looking at an empty profile deserves to be able to
 * see which account is empty.
 *
 * Signed out, every page is the sign-in — "Sign in with Microsoft" under real
 * sign-in, the development form under dev-session: nothing below renders, so
 * nothing below asks the proxy for data it would refuse. Misconfigured, every
 * page says so and renders nothing else — never a fallback to the development
 * identity.
 *
 * The account tag goes to <Providers>, which sends it with every API request
 * so that this page can never act for an account other than the one it shows.
 */
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const identity = await resolveIdentity(await renderCookies());
  const config = readAuthConfig();

  let who: React.ReactNode;
  let content: React.ReactNode;
  if (identity.status === "authenticated") {
    who = (
      <>
        <span
          className="max-w-[40%] truncate text-xs text-muted-foreground sm:ml-auto"
          title={identity.label}
          id="signed-in-account"
        >
          {identity.label}
        </span>
        {identity.mode === "dev-session" || identity.mode === "entra" ? <SignOutButton /> : null}
      </>
    );
    content = children;
  } else if (identity.status === "signed_out") {
    who = <span className="text-xs text-muted-foreground sm:ml-auto">Not signed in</span>;
    content =
      identity.mode === "entra" ? (
        <MicrosoftSignIn />
      ) : (
        <DevSignIn suggestedEmail={config.mode === "dev-session" ? config.suggestedEmail : null} />
      );
  } else {
    console.error(`auth.misconfigured: ${identity.reason}`);
    who = null;
    content = (
      <Alert variant="destructive">
        <AlertTitle>Sign-in is misconfigured on this server</AlertTitle>
        <AlertDescription>
          Nothing can be shown until it is fixed. The web server&apos;s log says what is wrong.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <Providers sessionTag={identity.status === "authenticated" ? identity.sessionTag : null}>
          <header className="border-b">
            <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
              <Link href="/" className="font-semibold tracking-tight">
                EasyApply
              </Link>
              {identity.status === "authenticated" ? (
                <AppNav className="order-3 w-full sm:order-none sm:w-auto sm:flex-1" />
              ) : null}
              {who}
            </div>
          </header>
          <main className="mx-auto max-w-5xl px-4 py-8">
            <React.Suspense fallback={null}>
              <SignInNotice />
            </React.Suspense>
            {identity.status === "signed_out" && identity.mode === "entra" ? (
              <React.Suspense fallback={null}>{content}</React.Suspense>
            ) : (
              content
            )}
          </main>
        </Providers>
      </body>
    </html>
  );
}
