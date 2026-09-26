"use client";

import * as React from "react";

const PRODUCT = "EasyApply";

/**
 * The document title for a page: most specific first, product last.
 *
 * `pageTitle(["Match", "Senior Backend Engineer"])` is
 * "Match · Senior Backend Engineer · EasyApply". Empty parts are dropped, so a
 * page can pass data that has not loaded yet and get a shorter title rather than
 * "Match ·  · EasyApply".
 */
export function pageTitle(parts: ReadonlyArray<string | null | undefined | false>): string {
  return [...parts.filter((part): part is string => Boolean(part && part.trim())), PRODUCT].join(
    " · ",
  );
}

/**
 * Give this route its own <title>.
 *
 * Every page here is a client component, so Next's metadata never saw anything
 * but the root layout's "EasyApply" — every tab, every history entry and every
 * bookmark had the same name (WCAG 2.4.2), and Next's route announcer, which
 * reads `document.title` after each navigation and only speaks when it changed,
 * stayed silent on every client-side navigation.
 *
 * Set in an effect, which is early enough for the announcer: its own effect
 * runs after the page's in the same commit, because it is rendered after the
 * page in the router's tree. The first part should therefore be something the
 * page knows without waiting for data — its heading — so the title that gets
 * announced is already distinct; the data-dependent parts refine it once they
 * arrive. Restored on unmount so a page that does not call this is not left
 * wearing the previous page's name.
 */
export function usePageTitle(...parts: Array<string | null | undefined | false>): void {
  const title = pageTitle(parts);

  React.useEffect(() => {
    document.title = title;
  }, [title]);

  React.useEffect(
    () => () => {
      document.title = PRODUCT;
    },
    [],
  );
}
