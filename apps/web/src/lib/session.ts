/**
 * The account this page was rendered for, as the client knows it.
 *
 * The layout renders for one account and hands its tag (`WebSession.sessionTag`)
 * to <Providers>, which records it here BEFORE any child renders — so even a
 * page's very first query carries it. Every request to /api/backend then sends
 * it as X-EasyApply-Session (through `apiFetch` in ./api/client.ts, the only way
 * this app reaches the proxy), and the proxy refuses a request whose tag is not
 * the account the browser's cookie now says: 409 `session_changed`. So a tab
 * left open while someone else signs in in another tab cannot read or write
 * that person's account as if it were the one on screen.
 */

import { SESSION_TAG_HEADER } from "@easyapply/shared";

let renderedTag: string | null = null;

export function setRenderedSessionTag(tag: string | null): void {
  renderedTag = tag;
}

export function renderedSessionTag(): string | null {
  return renderedTag;
}

export function sessionTagHeaders(): Record<string, string> {
  return renderedTag ? { [SESSION_TAG_HEADER]: renderedTag } : {};
}

/** Why a page was reloaded, carried across the reload in `?session=`. */
export type SessionNotice = "expired" | "changed" | "signed_out";

export const SESSION_NOTICE_PARAM = "session";
export const AUTH_ERROR_PARAM = "auth_error";

/** The current URL with `?session=<notice>` set: what to reload to. */
export function urlWithNotice(notice: SessionNotice, location: Location = window.location): string {
  const url = new URL(location.href);
  url.searchParams.set(SESSION_NOTICE_PARAM, notice);
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Tabs of this app tell each other when the session may have changed. */
export const SESSION_CHANNEL = "easyapply-session";

let leaving = false;

/**
 * This page is ending the session itself and navigating away (sign-out): its
 * own session checks stand down, so they do not race that navigation with a
 * reload of their own. (A BroadcastChannel delivers to every other instance of
 * the channel, the sending page's own included.)
 */
export function markLeaving(): void {
  leaving = true;
}

export function isLeaving(): boolean {
  return leaving;
}

/** The navigation away did not happen after all (cancelled, or restored from the back-forward cache). */
export function clearLeaving(): void {
  leaving = false;
}
