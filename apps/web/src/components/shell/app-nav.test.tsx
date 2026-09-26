import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { usePathname } from "next/navigation";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "@/test/render";

import { AppNav, isActive } from "./app-nav";

vi.mock("next/navigation", () => ({ usePathname: vi.fn(() => "/") }));

/** Puts the user on a page. The component reads nothing else about location. */
function at(pathname: string | null) {
  vi.mocked(usePathname).mockReturnValue(pathname as string);
}

beforeEach(() => at("/"));

function links() {
  return screen.getAllByRole("link");
}

function currentItems() {
  return links().filter((link) => link.getAttribute("aria-current") === "page");
}

describe("isActive", () => {
  it("lights the dashboard up on the dashboard and nowhere else", () => {
    // A prefix match on "/" is a prefix match on everything, which would leave
    // two sections looking current at once.
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/jobs", "/")).toBe(false);
    expect(isActive("/tailored-resumes/4", "/")).toBe(false);
  });

  it("keeps a section current while the user is deeper inside it", () => {
    // Someone reading a match is still in Jobs; the nav losing its highlight
    // three levels in reads as having left the section.
    expect(isActive("/jobs", "/jobs")).toBe(true);
    expect(isActive("/jobs/12", "/jobs")).toBe(true);
    expect(isActive("/jobs/12/match", "/jobs")).toBe(true);
  });

  it("does not treat a different route that merely starts the same as the section", () => {
    // A bare startsWith would make "/jobsomething" light up Jobs. The boundary
    // is the slash, not the character count.
    expect(isActive("/jobsomething", "/jobs")).toBe(false);
    expect(isActive("/jobs-archive", "/jobs")).toBe(false);
    // The pair the product actually ships: "Resumes" must not claim the user
    // when they are looking at a tailored resume, and vice versa.
    expect(isActive("/tailored-resumes", "/resume")).toBe(false);
    expect(isActive("/resume/9", "/tailored-resumes")).toBe(false);
  });
});

describe("AppNav", () => {
  it("is findable as the main navigation", () => {
    render(<AppNav />);

    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  const SECTIONS: Array<[pathname: string, label: string]> = [
    ["/", "Dashboard"],
    ["/profile", "Profile"],
    ["/profile/versions/3/edit", "Profile"],
    ["/resume", "Resumes"],
    ["/resume/9", "Resumes"],
    ["/jobs", "Jobs"],
    ["/jobs/12/match", "Jobs"],
    ["/tailored-resumes", "Tailored resumes"],
    ["/tailored-resumes/4", "Tailored resumes"],
  ];

  for (const [pathname, label] of SECTIONS) {
    it(`announces ${label} as the current page on ${pathname}`, () => {
      at(pathname);
      render(<AppNav />);

      // Both halves of "you are here": the right item says it, and — just as
      // important — nothing else does. A second aria-current is the same as
      // none, because the screen-reader user still has to guess.
      const current = currentItems();
      expect(current).toHaveLength(1);
      expect(current[0]).toHaveTextContent(label);
    });
  }

  it("marks nothing as current on a page the nav does not cover", () => {
    at("/internal/resume-processing");
    render(<AppNav />);

    // Better than falsely highlighting the dashboard: the user is somewhere
    // the nav cannot describe, and the nav says so by staying quiet.
    expect(currentItems()).toHaveLength(0);
  });

  it("falls back to the dashboard rather than crashing when there is no pathname", () => {
    // usePathname is nullable outside a route; a nav that throws takes the
    // whole shell down with it.
    at(null);
    render(<AppNav />);

    const current = currentItems();
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Dashboard");
  });

  // --- no dead navigation -------------------------------------------------------

  it("leads nowhere that does not exist", () => {
    render(<AppNav />);

    // Derived from what the component actually renders, not from a copy of its
    // list — a duplicated list would keep passing after someone adds an item.
    const hrefs = links().map((link) => link.getAttribute("href"));
    expect(hrefs.length).toBeGreaterThan(0);
    // Every entry in the list is one of those links: an item rendered as inert
    // text is dead navigation too, just a quieter kind.
    expect(screen.getAllByRole("listitem")).toHaveLength(hrefs.length);

    for (const href of hrefs) {
      expect(href, "a nav item rendered without an href").toBeTruthy();
      // An item pointing at a page that is not built yet is worse than no item:
      // the user spends their trust discovering it.
      expect(existsSync(pageFileFor(href!)), `${href} has no page in src/app`).toBe(true);
    }
  });

  it("gives every item a label, so none of them reads as a blank chip", () => {
    render(<AppNav />);

    for (const link of links()) {
      expect(link.textContent?.trim()).toBeTruthy();
      expect(link.textContent).not.toMatch(/undefined|null|\[object/i);
    }
  });

  it("does not link to /internal", () => {
    render(<AppNav />);

    // The page exists, and is deliberately reachable only by typing the URL: a
    // visible link would be visible to every user and would imply an access
    // control that does not exist yet.
    expect(existsSync(pageFileFor("/internal/resume-processing"))).toBe(true);
    expect(
      links().every((link) => !link.getAttribute("href")?.startsWith("/internal")),
    ).toBe(true);
  });
});

// Resolved from this file, not from the working directory, so the check means
// the same thing however vitest was invoked. (fileURLToPath takes the string:
// the global URL here is jsdom's, which node:url will not accept.)
const APP_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "app");

/** Where the App Router would have to find a page for this href to resolve. */
function pageFileFor(href: string) {
  return join(APP_DIR, ...href.split("/").filter(Boolean), "page.tsx");
}
