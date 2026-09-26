/**
 * What the popup says about each outcome. Plain data — no DOM — so each
 * sentence is testable on its own.
 */

import { SESSION_CHANGED_CODE, SESSION_REQUIRED_CODE } from "@easyapply/shared";

import type { ApiFailure } from "../api/client";
import type { SubmitOutcome } from "../api/jobs";
import { signInUrl, type SessionState } from "../api/session";
import type { JobDetail, JobIngestionRequest } from "../contract";

import type { Notice } from "./dom";

export type { Notice };

export interface DescribeContext {
  webUrl: string;
  extensionId: string;
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

const COMPARED: { field: keyof JobIngestionRequest & keyof JobDetail; label: string }[] = [
  { field: "job_title", label: "title" },
  { field: "company_name", label: "company" },
  { field: "location_raw", label: "location" },
  { field: "description_text", label: "description" },
];

/**
 * Which reviewed values the already-saved job does NOT have.
 *
 * A 200 means EasyApply kept the version it saved first and ignored this one,
 * so an edit the person just made was not applied. Saying so is the difference
 * between "already saved" and "saved as you just reviewed it".
 */
export function differencesFromSaved(payload: JobIngestionRequest, job: JobDetail): string[] {
  return COMPARED.filter(({ field }) => (payload[field] ?? null) !== (job[field] ?? null)).map(
    ({ label }) => label,
  );
}

function requirementsLine(job: JobDetail): string | null {
  switch (job.latest_run?.status) {
    case "completed":
      return "EasyApply has read its requirements.";
    case "failed":
      return "It was saved, but reading its requirements failed — you can retry from the job's page in EasyApply.";
    case "queued":
    case "processing":
      return "EasyApply is still reading its requirements.";
    default:
      return null;
  }
}

function issueLocation(loc: (string | number)[]): string {
  // FastAPI reports ["body", "job_title"]; the field is what the person knows.
  const path = loc[0] === "body" ? loc.slice(1) : loc;
  return path.length > 0 ? path.join(".") : "request";
}

/**
 * The session this popup was opened with has ended (the proxy's 401 after the
 * popup knew who was signed in). Distinct from never having been signed in,
 * which the review screen's own "Sign in to EasyApply" notice covers.
 */
export function describeSessionExpired(subject: string, { webUrl }: Pick<DescribeContext, "webUrl">): Notice {
  return {
    tone: "error",
    title: "Your EasyApply session expired. Sign in again.",
    lines: [`EasyApply couldn't ${subject}. Once you've signed in, open EasyApply on this page again.`],
    // Its own id: the review screen's account notice carries "sign-in" at the same time.
    link: { href: signInUrl(webUrl), text: "Sign in to EasyApply", id: "session-sign-in" },
  };
}

/**
 * The browser is signed in to a different account than the one this popup was
 * showing (the proxy's 409 `session_changed`). The popup has already thrown
 * away everything it held for the other account; it offers to start again
 * (a Reload button beside this notice) as whoever is signed in now.
 */
export function describeSessionChanged(): Notice {
  return {
    tone: "error",
    title: "You're signed in to EasyApply as a different account now.",
    lines: [
      "This popup was for the account signed in before, so EasyApply has cleared it and done nothing more. " +
        "Reload to continue as the account signed in now.",
    ],
  };
}

/**
 * Which account a save would land in — part of "exactly what will be saved".
 * Null when the account is known and is shown as it is.
 */
export function describeSession(session: SessionState, { webUrl }: Pick<DescribeContext, "webUrl">): Notice | null {
  switch (session.kind) {
    case "signed_in":
      return null;
    case "signed_out":
      return {
        tone: "info",
        title: "Sign in to EasyApply to save this job.",
        lines: [
          "A job is saved to the account this browser is signed in to EasyApply as. Nothing is sent until you sign in.",
        ],
        link: { href: signInUrl(webUrl), text: "Sign in to EasyApply", id: "sign-in" },
      };
    case "unknown":
      return {
        tone: "info",
        title: "Couldn't check which account this would be saved to.",
        lines: [`${session.message} Saving may still work, and will say if it does not.`],
      };
  }
}

export function describeOutcome(
  outcome: SubmitOutcome,
  payload: JobIngestionRequest,
  { webUrl, extensionId }: DescribeContext,
): Notice {
  switch (outcome.kind) {
    case "created": {
      const lines = [requirementsLine(outcome.job)].filter((line): line is string => line !== null);
      return { tone: "success", title: "Saved to EasyApply.", lines };
    }

    case "existing": {
      const lines = [
        `EasyApply already had this posting, saved ${formatDate(outcome.job.created_at)}. ` +
          "It keeps the version it saved first, so nothing was added or changed.",
      ];
      const differences = differencesFromSaved(payload, outcome.job);
      if (differences.length > 0) {
        lines.push(`The saved version differs from what you reviewed in: ${differences.join(", ")}.`);
      }
      return { tone: "info", title: "Already in EasyApply.", lines };
    }

    case "unreachable":
      switch (outcome.code) {
        case "network":
          return {
            tone: "error",
            title: "EasyApply isn't reachable.",
            lines: [`Nothing answered at ${webUrl}. Is the web app running (npm run dev)? Nothing was saved.`],
          };
        case "api_unreachable":
          return {
            tone: "error",
            title: "The EasyApply API isn't running.",
            lines: [
              `The web app at ${webUrl} answered, but the API behind it did not. ` +
                "Start it (services/api: uvicorn app.main:app), then try again. Nothing was saved.",
            ],
          };
        case "timeout":
          return { tone: "error", title: "No answer from EasyApply.", lines: [outcome.message] };
        case "unexpected_response":
          return {
            tone: "error",
            title: "That isn't EasyApply.",
            lines: [`${outcome.message} Is another application using that address?`],
          };
      }
      break;

    case "rejected":
      if (outcome.code === "request_validation") {
        return {
          tone: "error",
          title: "EasyApply refused some fields.",
          lines: outcome.issues.map((issue) => `${issueLocation(issue.loc)}: ${issue.msg}`),
        };
      }
      if (outcome.code === SESSION_REQUIRED_CODE) {
        // The popup could not tell who was signed in when Save was pressed. When
        // it could, the session expired instead: see describeSessionExpired.
        return {
          tone: "error",
          title: "You're not signed in to EasyApply.",
          lines: ["Nothing was saved. Sign in, then open EasyApply again on this page."],
          link: { href: signInUrl(webUrl), text: "Sign in to EasyApply", id: "session-sign-in" },
        };
      }
      if (outcome.code === SESSION_CHANGED_CODE) return describeSessionChanged();
      if (outcome.code === "cross_site_request") {
        return {
          tone: "error",
          title: "The web app doesn't accept this extension yet.",
          lines: [
            "Add this line to apps/web/.env.local, then restart the web app:",
            `PROXY_ALLOWED_EXTENSION_IDS=${extensionId}`,
          ],
        };
      }
      return {
        tone: "error",
        title: "EasyApply refused the save.",
        lines: [`${outcome.message} (${outcome.code}, HTTP ${outcome.status})`],
      };
  }
  return { tone: "error", title: "Something went wrong.", lines: [] };
}

/**
 * What the popup says when a read or write after the save did not work.
 *
 * `subject` completes "EasyApply couldn't …" — load this job's requirements,
 * match your profile — so every failure names what did not happen. The
 * server's own words are shown only where they are the API's fixed sentences
 * (a 409's reason); nothing here echoes a job description or a profile value.
 */
export function describeFailure(
  failure: ApiFailure,
  subject: string,
  { webUrl, extensionId }: DescribeContext,
): Notice {
  switch (failure.kind) {
    case "session_required":
      return describeSessionExpired(subject, { webUrl });
    case "session_changed":
      return describeSessionChanged();
    case "api_unreachable":
      return {
        tone: "error",
        title: "The EasyApply API isn't running.",
        lines: [
          `EasyApply couldn't ${subject}: the web app at ${webUrl} answered, but the API behind it did not. ` +
            "Start it (services/api: uvicorn app.main:app), then try again.",
        ],
      };
    case "network":
      return {
        tone: "error",
        title: "EasyApply isn't reachable.",
        lines: [`EasyApply couldn't ${subject}: nothing answered at ${webUrl}. Is the web app running?`],
      };
    case "timeout":
      return { tone: "error", title: "No answer from EasyApply.", lines: [`EasyApply couldn't ${subject}. ${failure.message} Try again.`] };
    case "not_found":
      return {
        tone: "error",
        title: "That isn't in your EasyApply account.",
        lines: [
          `EasyApply couldn't ${subject}. It may have been removed, or belong to another account — ` +
            "EasyApply only ever shows the account you are signed in as its own jobs, matches and resumes.",
        ],
      };
    case "conflict":
      return { tone: "info", title: `EasyApply couldn't ${subject} yet.`, lines: [failure.message] };
    case "refused":
      if (failure.code === "cross_site_request") {
        return {
          tone: "error",
          title: "The web app doesn't accept this request from the extension.",
          lines: [
            "The web app's proxy lets this extension save jobs, request matches and start tailored resumes, " +
              "and only when this line is in apps/web/.env.local (restart the web app after adding it):",
            `PROXY_ALLOWED_EXTENSION_IDS=${extensionId}`,
          ],
        };
      }
      return {
        tone: "error",
        title: `EasyApply refused to ${subject}.`,
        lines: [`${failure.message}${failure.code ? ` (${failure.code}, HTTP ${failure.status})` : ` (HTTP ${failure.status})`}`],
      };
    case "unexpected_response":
      return {
        tone: "error",
        title: "That isn't EasyApply.",
        lines: [`EasyApply couldn't ${subject}. ${failure.message}`],
      };
  }
}
