"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { clearLeaving, markLeaving, SESSION_CHANNEL } from "@/lib/session";

/** A navigation target from the server: an absolute https URL, loopback http, or a same-origin path. */
const LOGOUT_URL = /^(https:\/\/|http:\/\/(localhost|127\.0\.0\.1|\[::1\])[:/]|\/(?!\/))/;

/** If the page is still here this long after navigating away, the navigation did not happen. */
const ABANDONED_NAVIGATION_MS = 15_000;

/**
 * Ends this browser's session — for the web app and the extension alike, since
 * they present the same cookie. A full navigation afterwards, for the reason
 * `DevSignIn` reloads: nothing cached for this person may outlive their session
 * in this tab. Other tabs are told, so they stop showing this person's data.
 *
 * Under real sign-in the server answers with `logoutUrl`, Microsoft's
 * end-session endpoint, and the navigation goes there: ending only EasyApply's
 * session would leave "Sign in with Microsoft" one click from the previous
 * person's account on a shared computer. Microsoft returns to /signed-out.
 */
export function SignOutButton({
  onSignedOut = (logoutUrl) => {
    window.location.assign(logoutUrl ?? "/");
    // Still here long after navigating away: the navigation was cancelled.
    // Stand the safeguards back up and show the truth.
    window.setTimeout(() => {
      clearLeaving();
      window.location.reload();
    }, ABANDONED_NAVIGATION_MS);
  },
}: {
  /** What happens once the cookie is cleared. A full navigation; replaceable in tests. */
  onSignedOut?: (logoutUrl: string | null) => void;
}) {
  const [pending, setPending] = React.useState(false);

  async function signOut() {
    setPending(true);
    // This page's own session checks must not race the navigation below.
    markLeaving();
    let logoutUrl: string | null = null;
    let signedOut = false;
    try {
      const response = await fetch("/api/auth/sign-out", { method: "POST", headers: { Accept: "application/json" } });
      signedOut = response.ok;
      const body = (await response.json().catch(() => null)) as { logoutUrl?: unknown } | null;
      if (typeof body?.logoutUrl === "string" && LOGOUT_URL.test(body.logoutUrl)) {
        logoutUrl = body.logoutUrl;
      }
    } catch {
      // Nothing to show here: the page that loads next says whether a session
      // is still in force, which is the truth either way.
    }
    // Other tabs are told only about a sign-out that happened.
    if (signedOut && typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel(SESSION_CHANNEL);
      channel.postMessage({ signedOut: true });
      channel.close();
    }
    onSignedOut(logoutUrl);
  }

  return (
    <Button type="button" variant="ghost" size="sm" onClick={() => void signOut()} disabled={pending}>
      {pending ? "Signing out…" : "Sign out"}
    </Button>
  );
}
