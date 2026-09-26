/**
 * What the service worker records about each request it makes, for whoever is
 * debugging the extension (chrome://extensions → service worker → console).
 *
 * Structured and deliberately thin: the route with its ids replaced, the ids
 * themselves (job, match, tailoring run, profile version — database keys, not
 * personal data), the status, how long it took, and which failure category it
 * fell into. Never a request or response body, never an email, never a
 * requirement, a profile value or a job description — the things this
 * extension handles that belong to somebody.
 */

import type { FailureKind } from "../api/client";

export interface Diagnostic {
  at: string;
  /** e.g. `GET /jobs/:id/requirements`. */
  route: string;
  outcome: "ok" | FailureKind | "created" | "existing" | "rejected" | "unreachable";
  status: number | null;
  ms: number;
  ids: Record<string, number>;
}

export interface Diagnostics {
  record(entry: Omit<Diagnostic, "at">): void;
  /** The most recent entries, oldest first. */
  entries(): readonly Diagnostic[];
}

export function createDiagnostics({
  log = (line: string) => console.info(line),
  now = () => new Date(),
  limit = 50,
}: { log?: (line: string) => void; now?: () => Date; limit?: number } = {}): Diagnostics {
  const buffer: Diagnostic[] = [];
  return {
    record(entry) {
      const full: Diagnostic = { at: now().toISOString(), ...entry };
      buffer.push(full);
      if (buffer.length > limit) buffer.shift();
      log(`easyapply.api ${JSON.stringify(full)}`);
    },
    entries() {
      return [...buffer];
    },
  };
}
