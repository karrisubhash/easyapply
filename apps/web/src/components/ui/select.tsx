import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A styled native select.
 *
 * Deliberately not the Radix listbox: the values here are short closed sets
 * that come straight from the API contract, and a native control gets keyboard
 * behaviour, mobile pickers and assistive-technology support for free — plus it
 * is honestly testable, which a portalled listbox in jsdom is not.
 */
export const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  >
    {children}
  </select>
));
Select.displayName = "Select";
