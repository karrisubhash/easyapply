"use client";

import { usePathname, useSearchParams } from "next/navigation";
import * as React from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { AUTH_ERROR_PARAM, SESSION_NOTICE_PARAM } from "@/lib/session";

/** What a failed sign-in says. The codes are the callback's (src/server/auth/sign-in-flow.ts). */
export const AUTH_ERROR_MESSAGES: Record<string, string> = {
  cancelled: "Sign-in was cancelled. Nothing was changed.",
  expired: "That sign-in took too long or was already used. Sign in again.",
  unavailable: "Microsoft sign-in could not be reached. Try again in a moment.",
  failed: "Signing in with Microsoft did not work. Try again.",
};

export const SESSION_NOTICE_MESSAGES: Record<string, string> = {
  expired: "Your EasyApply session expired. Sign in again.",
  changed:
    "This browser was signed in to a different EasyApply account in another tab, so this page was reloaded for that account.",
  signed_out: "You signed out of EasyApply in another tab.",
};

export const SIGNED_OUT_MESSAGE = "You signed out of EasyApply.";

/**
 * The one line that explains why this page is what it is: a sign-in that
 * failed, a session that ended or changed, a sign-out. Read from the URL — the
 * only thing that survives the redirect or reload that caused it — and then
 * taken out of the address bar, so reloading does not say it again. Shown
 * whether or not anyone is signed in: a failed sign-in leaves an existing
 * session as it was, and still deserves an answer.
 */
export function SignInNotice() {
  const pathname = usePathname();
  const params = useSearchParams();
  const [message] = React.useState(() => {
    // Own keys only: `__proto__` or `constructor` in the URL must not reach a lookup.
    const error = params.get(AUTH_ERROR_PARAM);
    if (error !== null) {
      return { text: Object.hasOwn(AUTH_ERROR_MESSAGES, error) ? AUTH_ERROR_MESSAGES[error]! : AUTH_ERROR_MESSAGES.failed!, bad: true };
    }
    const notice = params.get(SESSION_NOTICE_PARAM);
    if (notice !== null && Object.hasOwn(SESSION_NOTICE_MESSAGES, notice)) {
      return { text: SESSION_NOTICE_MESSAGES[notice]!, bad: notice === "expired" };
    }
    if (pathname === "/signed-out") return { text: SIGNED_OUT_MESSAGE, bad: false };
    return null;
  });

  React.useEffect(() => {
    if (!params.has(AUTH_ERROR_PARAM) && !params.has(SESSION_NOTICE_PARAM)) return;
    const url = new URL(window.location.href);
    url.searchParams.delete(AUTH_ERROR_PARAM);
    url.searchParams.delete(SESSION_NOTICE_PARAM);
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }, [params]);

  if (!message) return null;
  return (
    <Alert variant={message.bad ? "destructive" : "default"} className="mx-auto mb-6 max-w-md" id="sign-in-notice">
      <AlertDescription>{message.text}</AlertDescription>
    </Alert>
  );
}
