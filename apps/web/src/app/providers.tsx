"use client";

import { SESSION_CHANGED_CODE, SESSION_REQUIRED_CODE } from "@easyapply/shared";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { ApiError } from "@/lib/api/client";
import { clearLeaving, isLeaving, SESSION_CHANNEL, setRenderedSessionTag, urlWithNotice, type SessionNotice } from "@/lib/session";

/** What a session failure seen by a query or a mutation should do. */
export type SessionFailureHandler = (error: ApiError, source: "query" | "mutation") => void;

export function makeQueryClient(onSessionFailure?: SessionFailureHandler) {
  const report = (source: "query" | "mutation") => (error: unknown) => {
    if (
      onSessionFailure &&
      error instanceof ApiError &&
      (error.code === SESSION_CHANGED_CODE || (error.status === 401 && error.code === SESSION_REQUIRED_CODE))
    ) {
      onSessionFailure(error, source);
    }
  };
  return new QueryClient({
    queryCache: new QueryCache({ onError: report("query") }),
    mutationCache: new MutationCache({ onError: report("mutation") }),
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        // 404 and 409 are answers, not transport failures. Retrying them
        // just delays the empty state the user should already be seeing. So
        // is 401: no session is not cured by asking again.
        retry: (failureCount, error) =>
          error instanceof ApiError && (error.isNotFound || error.isConflict || error.status === 401)
            ? false
            : failureCount < 2,
      },
      mutations: { retry: false },
    },
  });
}

/**
 * Leave this page for a fresh render of the account the browser is now in:
 * nothing cached for the previous account may be shown again. A full load,
 * with the reason in the URL so the next page can say it (a message set here
 * would not survive the reload).
 */
function reloadFor(client: QueryClient, notice: SessionNotice) {
  client.clear();
  window.location.replace(urlWithNotice(notice));
}

/**
 * Keeps what this tab shows consistent with the browser's session.
 *
 *   Another account is signed in now         reload for it (a 409 session_changed,
 *   (409, or the session check)              or the check below finds another tag):
 *                                            nothing of the previous account may
 *                                            stay on screen.
 *   Signed out in another tab                reload to the sign-in, saying so: the
 *                                            person meant to leave.
 *   The session expired (a 401, or the       STAY: this page may hold unsaved edits,
 *   check finds nobody signed in)            and it is the same person's. A notice
 *                                            offers sign-in in a new tab; signing in
 *                                            again as the same person keeps the
 *                                            account tag, so retrying just works and
 *                                            the notice goes.
 *
 * The check asks /api/auth/session when the tab regains focus or becomes
 * visible, when another tab of this app renders, signs in or signs out
 * (BroadcastChannel), and when the page is restored from the back-forward cache.
 */
export function Providers({ children, sessionTag = null }: { children: React.ReactNode; sessionTag?: string | null }) {
  // Before any child renders, so the first query a page makes carries it.
  setRenderedSessionTag(sessionTag);

  const [sessionEnded, setSessionEnded] = React.useState(false);
  const markEnded = React.useCallback((ended: boolean) => setSessionEnded(ended), []);
  const [client] = React.useState(() => {
    const created = makeQueryClient((error) => {
      if (isLeaving()) return;
      if (error.code === SESSION_CHANGED_CODE) reloadFor(created, "changed");
      else markEnded(true);
    });
    return created;
  });

  React.useEffect(() => {
    // Checks never overlap, and none is dropped: a signal that arrives while
    // one is in flight runs one more afterwards — the sign-in in another tab
    // that follows a moment after its sign-in page rendered must not be lost.
    let checking = false;
    let again = false;
    // Another tab said it signed out: if the session is gone, that is why —
    // not an expiry, and the page should not say it was.
    let signedOutElsewhere = false;

    async function check() {
      if (isLeaving()) return;
      if (checking) {
        again = true;
        return;
      }
      checking = true;
      try {
        const response = await fetch("/api/auth/session", { cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) return;
        const body = (await response.json()) as { authenticated?: unknown; sessionTag?: unknown };
        const current = body.authenticated === true && typeof body.sessionTag === "string" ? body.sessionTag : null;
        if (isLeaving()) return;
        if (sessionTag === null) {
          // Rendered signed out; someone has signed in since.
          if (current !== null) window.location.reload();
        } else if (current === sessionTag) {
          // Still (or again) this account: whatever was signalled did not end it.
          signedOutElsewhere = false;
          markEnded(false);
        } else if (current === null) {
          if (signedOutElsewhere) reloadFor(client, "signed_out");
          // Expired: keep the page, say so.
          else markEnded(true);
        } else {
          reloadFor(client, "changed");
        }
      } catch {
        // The web app did not answer: nothing to conclude.
      } finally {
        checking = false;
        if (again) {
          again = false;
          void check();
        }
      }
    }

    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    // Back from the back-forward cache — e.g. a sign-out whose navigation was
    // abandoned: this page is live again, so its safeguards are too.
    const onPageShow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      clearLeaving();
      void check();
    };
    window.addEventListener("focus", onVisible);
    window.addEventListener("pageshow", onPageShow);
    document.addEventListener("visibilitychange", onVisible);

    let channel: BroadcastChannel | null = null;
    if (typeof BroadcastChannel !== "undefined") {
      channel = new BroadcastChannel(SESSION_CHANNEL);
      channel.onmessage = (event: MessageEvent) => {
        if ((event.data as { signedOut?: unknown } | null)?.signedOut === true) signedOutElsewhere = true;
        void check();
      };
      // Tell other tabs a page just rendered for this account (or for nobody).
      channel.postMessage({ rendered: true });
    }

    return () => {
      window.removeEventListener("focus", onVisible);
      window.removeEventListener("pageshow", onPageShow);
      document.removeEventListener("visibilitychange", onVisible);
      channel?.close();
    };
  }, [client, markEnded, sessionTag]);

  return (
    <QueryClientProvider client={client}>
      {sessionEnded ? (
        <div className="mx-auto max-w-5xl px-4 pt-4">
          <Alert variant="destructive" role="alert" id="session-ended">
            <AlertDescription>
              Your EasyApply session expired. Sign in again.{" "}
              <a href="/" target="_blank" rel="noopener" className="font-medium underline">
                Sign in in a new tab
              </a>{" "}
              — what you entered here is kept; try again once you are signed in.
            </AlertDescription>
          </Alert>
        </div>
      ) : null}
      {children}
    </QueryClientProvider>
  );
}
