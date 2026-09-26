"use client";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";

const TITLES: Record<number, string> = {
  409: "That conflicts with the current state",
  422: "Some of this data was rejected",
  502: "The API is unreachable",
};

/**
 * Renders whatever the API said went wrong.
 *
 * The frontend has no opinion about which rules exist: it shows the backend's
 * own message, and its `code` — which is the database constraint name for a
 * constraint violation — so a support conversation can name the exact rule.
 *
 * `onRetry` is for a failed READ. Queries here do not refetch on window focus,
 * so without it the only way out of an error state was a full page reload —
 * which a person does not know is safe. It is never passed for a refused write:
 * repeating a request the server rejected on its merits gets the same answer.
 */
export function ApiErrorAlert({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  if (!error) return null;

  const retry = onRetry ? (
    <div className="mt-3">
      <Button type="button" variant="outline" size="sm" onClick={() => onRetry()}>
        Retry
      </Button>
    </div>
  ) : null;

  if (!(error instanceof ApiError)) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Something went wrong</AlertTitle>
        <AlertDescription>
          <p>{error instanceof Error ? error.message : "An unexpected error occurred."}</p>
          {retry}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert variant={error.status === 409 ? "warning" : "destructive"}>
      <AlertTitle>{TITLES[error.status] ?? "Request failed"}</AlertTitle>
      <AlertDescription>
        <p>{error.message}</p>

        {error.issues.length > 0 && (
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {error.issues.map((issue, index) => (
              <li key={index}>
                <span className="font-medium">{fieldLabel(issue.loc)}</span>: {issue.msg}
              </li>
            ))}
          </ul>
        )}

        {error.code && error.code !== "unknown" && (
          <p className="mt-2 text-xs opacity-70">Reference: {error.code}</p>
        )}

        {retry}
      </AlertDescription>
    </Alert>
  );
}

/** ["body", "skills", 2, "name"] -> "skills #3 name" */
function fieldLabel(loc: (string | number)[]): string {
  return loc
    .filter((part) => part !== "body")
    .map((part) => (typeof part === "number" ? `#${part + 1}` : part))
    .join(" ");
}
