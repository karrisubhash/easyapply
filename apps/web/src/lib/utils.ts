import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Render an ISO timestamp without exploding on a null one. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    dateStyle: "medium",
  });
}

/**
 * Render a date only as precisely as it was actually known.
 *
 * The domain stores "May 2024" as 2024-05-01 with precision "month". Formatting
 * that as "May 1, 2024" would invent a day the resume never gave — putting back
 * exactly the false precision the precision column exists to prevent.
 */
export function formatDateWithPrecision(
  value: string | null | undefined,
  precision: "year" | "month" | "day" | null | undefined,
): string {
  if (!value) return "—";
  const date = new Date(`${value}T00:00:00`);

  if (precision === "year") {
    return date.toLocaleDateString(undefined, { year: "numeric" });
  }
  if (precision === "month") {
    return date.toLocaleDateString(undefined, { year: "numeric", month: "long" });
  }
  return date.toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** "full_time" -> "Full time" */
export function humanise(value: string | null | undefined): string {
  if (!value) return "—";
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
