"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The application's navigation.
 *
 * One list, in the order the product is used: you arrive at the dashboard, you
 * establish who you are (profile, resume), then you work (jobs, tailored
 * resumes). Nothing here leads anywhere that does not exist — a navigation item
 * pointing at a page that is not built yet is worse than no item at all,
 * because the user spends their trust discovering it.
 *
 * `/internal` is deliberately absent, as it has been since it was added: a
 * visible link would be visible to every user and would imply an access control
 * that does not exist yet. You arrive there by typing the URL.
 */
const ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/profile", label: "Profile" },
  { href: "/resume", label: "Resumes" },
  { href: "/jobs", label: "Jobs" },
  { href: "/tailored-resumes", label: "Tailored resumes" },
] as const;

/**
 * Whether this item is the section the user is currently in.
 *
 * Prefix matching, so `/jobs/12/match` still lights up "Jobs" — a user three
 * levels into a section has not left it. `/` is exact, or it would be active
 * everywhere.
 */
export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppNav({ className }: { className?: string }) {
  const pathname = usePathname() ?? "/";

  return (
    <nav aria-label="Main" className={className}>
      <ul className="flex flex-wrap items-center gap-1">
        {ITEMS.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                // aria-current is the accessible half of "you are here"; the
                // colour is the visual half. Both, because either alone leaves
                // one set of users guessing.
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-8 items-center rounded-md px-3 text-sm transition-colors",
                  active
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                )}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
