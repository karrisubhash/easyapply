"use client";

import type { TailoredResume, TailoringIssue } from "@easyapply/shared";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";

/**
 * Whether this document can be stood behind, and what is wrong with it if not.
 *
 * Shown ABOVE the document on the review page, never below it and never folded
 * into a collapsed section. A user who scrolls straight to the download button
 * must have already passed the sentence saying the document contains a claim
 * their profile does not support — §9's requirement, and the reason this
 * component leads with the verdict rather than with a count.
 *
 * The vocabulary matters. A failed document is not "a draft", not "unverified"
 * and not "pending review": those all suggest the problem is that nobody has
 * looked yet. Something did look, and it found a specific sentence.
 */
export function GroundingSummary({ result }: { result: TailoredResume }) {
  // `issues` is optional in the generated contract because the Pydantic field
  // has a default. An approved document legitimately has none.
  const issues = result.issues ?? [];
  const errors = issues.filter((issue) => issue.severity === "error");
  const warnings = issues.filter((issue) => issue.severity === "warning");

  if (!result.is_approved) {
    return (
      <Alert variant="destructive">
        <AlertTitle>This resume was not approved</AlertTitle>
        <AlertDescription className="space-y-3">
          <p>
            {errors.length === 1
              ? "One statement in this resume could not be traced back to the profile version it was generated from."
              : `${errors.length} statements in this resume could not be traced back to the profile version they were generated from.`}{" "}
            It has not been turned into a file, and it cannot be downloaded.
          </p>
          <IssueList issues={errors} />
          <p className="text-xs opacity-80">
            You can generate again, or add the missing detail to your profile first and
            confirm a new version.
          </p>
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert>
      <AlertTitle className="flex flex-wrap items-center gap-2">
        Every statement traces back to your profile
        <Badge variant="confirmed">Checked</Badge>
      </AlertTitle>
      <AlertDescription className="space-y-3">
        <p>
          Each line below was checked against the confirmed profile version it was
          generated from. Nothing in it states a fact, a number or a name your profile
          does not contain.
        </p>
        {warnings.length > 0 ? (
          <>
            <p>
              {warnings.length === 1
                ? "One line is worth a second look before you send this:"
                : `${warnings.length} lines are worth a second look before you send this:`}
            </p>
            <IssueList issues={warnings} />
          </>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

/**
 * The findings themselves.
 *
 * Each one shows the generated text it is about, because a finding the user
 * cannot locate in the document is a finding they cannot act on. `field_path`
 * is shown too — it is the server's own address for the line, and it is what a
 * support conversation can name.
 */
function IssueList({ issues }: { issues: TailoringIssue[] }) {
  if (issues.length === 0) return null;

  return (
    <ul className="space-y-2">
      {issues.map((issue, index) => (
        <li key={index} className="rounded-md border border-current/20 p-3 text-sm">
          <p className="font-medium">{issue.reason}</p>
          {issue.claim ? (
            <p className="mt-1 break-words italic opacity-90">&ldquo;{issue.claim}&rdquo;</p>
          ) : null}
          <p className="mt-1 text-xs opacity-70">
            {issue.section}
            {issue.field_path ? ` · ${issue.field_path}` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}
