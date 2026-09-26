"use client";

import type { OperatorDocument, OperatorState } from "@easyapply/shared";
import * as React from "react";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { LoadingCard } from "@/components/profile/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useAttention, useOperatorDocument } from "@/lib/api/operations-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

/**
 * An operational tool, not a product dashboard.
 *
 * It answers one question — which resume processing needs a person — and it is
 * deliberately plain: a table, a filter, and a way to see one document's
 * attempts. Anything more would be a feature nobody asked for standing between
 * an operator and the answer.
 *
 * Nothing here can show resume content, because the API does not return any.
 * That is not a UI decision to keep: this page reads across all users, and the
 * data minimisation is what makes that acceptable while the authorization
 * boundary is still a shared token.
 */

/** What each state means, and whether it is someone's problem. */
const STATES: Record<OperatorState, { label: string; hint: string; tone: Tone }> = {
  exhausted: {
    label: "Exhausted",
    hint: "Retried until the budget ran out. Needs a person.",
    tone: "bad",
  },
  failed_permanent: {
    label: "Permanent failure",
    hint: "The document cannot be processed. Retrying will not help.",
    tone: "bad",
  },
  stalled: {
    label: "Stalled",
    hint: "Not terminal, not running, nobody holding it.",
    tone: "bad",
  },
  waiting_retry: {
    label: "Waiting to retry",
    hint: "Backing off before the next attempt. Working as intended.",
    tone: "wait",
  },
  processing: { label: "Processing", hint: "In flight or queued.", tone: "ok" },
  completed: { label: "Completed", hint: "Produced a draft.", tone: "ok" },
};

type Tone = "bad" | "wait" | "ok";

function StateBadge({ state }: { state: string }) {
  const known = STATES[state as OperatorState];
  if (!known) return <Badge variant="outline">{state}</Badge>;

  const variant =
    known.tone === "bad" ? "draft" : known.tone === "wait" ? "secondary" : "confirmed";
  return <Badge variant={variant}>{known.label}</Badge>;
}

export default function ResumeProcessingOperations() {
  const [includeAll, setIncludeAll] = React.useState(false);
  const [openDocument, setOpenDocument] = React.useState<number | null>(null);

  const attention = useAttention(includeAll);
  const detail = useOperatorDocument(openDocument);

  usePageTitle("Resume processing");

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Resume processing</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Internal. Shows processing state across all users — ids, timings and
          failure codes only, never resume contents.
        </p>
      </header>

      {attention.isError ? (
        <ApiErrorAlert error={attention.error} onRetry={() => void attention.refetch()} />
      ) : null}
      {attention.isPending ? <LoadingCard label="Loading processing state" /> : null}

      {attention.isSuccess ? (
        <>
          <Counts counts={attention.data.counts ?? {}} />

          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-muted-foreground">
              {includeAll
                ? "Showing every document."
                : "Showing only what needs attention."}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIncludeAll((shown) => !shown)}
            >
              {includeAll ? "Only what needs attention" : "Show everything"}
            </Button>
          </div>

          {(attention.data.documents ?? []).length === 0 ? (
            <Card>
              <CardContent className="p-6 text-sm text-muted-foreground">
                {includeAll
                  ? "No resumes have been uploaded."
                  : "Nothing needs attention."}
              </CardContent>
            </Card>
          ) : (
            <DocumentTable
              documents={attention.data.documents ?? []}
              onInspect={setOpenDocument}
              openDocument={openDocument}
            />
          )}
        </>
      ) : null}

      {openDocument !== null ? (
        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="flex items-center justify-between">
              <h2 className="font-medium">Document {openDocument} — attempts</h2>
              <Button variant="ghost" size="sm" onClick={() => setOpenDocument(null)}>
                Close
              </Button>
            </div>

            {detail.isError ? <ApiErrorAlert error={detail.error} /> : null}
            {detail.isPending ? <LoadingCard label="Loading attempts" /> : null}
            {detail.isSuccess ? <AttemptTable detail={detail.data} /> : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function Counts({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([state, count]) => (
        <span
          key={state}
          className="inline-flex items-center gap-2 rounded-md border px-3 py-1 text-sm"
          title={STATES[state as OperatorState]?.hint ?? state}
        >
          <StateBadge state={state} />
          <span className="tabular-nums font-medium">{count}</span>
        </span>
      ))}
    </div>
  );
}

function DocumentTable({
  documents,
  onInspect,
  openDocument,
}: {
  documents: OperatorDocument[];
  onInspect: (id: number) => void;
  openDocument: number | null;
}) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <Th>Doc</Th>
            <Th>User</Th>
            <Th>State</Th>
            <Th>Attempts</Th>
            <Th>Run</Th>
            <Th>Stage</Th>
            <Th>Failure</Th>
            <Th>Next retry</Th>
            <Th>Heartbeat</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {documents.map((document) => (
            <tr
              key={document.document_id}
              className={
                document.document_id === openDocument ? "bg-accent/50" : undefined
              }
            >
              <Td className="tabular-nums">{document.document_id}</Td>
              <Td className="tabular-nums">{document.user_id}</Td>
              <Td>
                <StateBadge state={document.state} />
              </Td>
              <Td className="tabular-nums">
                {document.attempts_used}/{document.max_attempts}
              </Td>
              <Td className="tabular-nums">{document.latest_run_id ?? "—"}</Td>
              <Td>{document.failed_stage ?? document.status ?? "—"}</Td>
              <Td>
                {document.error_code ? (
                  <span title={document.failure_classification ?? undefined}>
                    <code className="text-xs">{document.error_code}</code>
                    {document.failure_classification ? (
                      <span className="ml-1 text-xs text-muted-foreground">
                        ({document.failure_classification})
                      </span>
                    ) : null}
                  </span>
                ) : (
                  "—"
                )}
              </Td>
              <Td>{formatDateTime(document.next_retry_at)}</Td>
              <Td>
                {document.leased ? "held · " : ""}
                {formatDateTime(document.heartbeat_at)}
              </Td>
              <Td>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onInspect(document.document_id)}
                >
                  Attempts
                </Button>
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AttemptTable({
  detail,
}: {
  detail: {
    runs?: {
      id: number;
      attempt: number;
      status: string;
      failed_stage?: string | null;
      error_code?: string | null;
      failure_classification?: string | null;
      created_at: string;
      started_at?: string | null;
      finished_at?: string | null;
      next_retry_at?: string | null;
      provider?: string | null;
      model?: string | null;
      page_count?: number | null;
    }[];
  };
}) {
  const runs = detail.runs ?? [];
  if (runs.length === 0) {
    return <p className="text-sm text-muted-foreground">No attempts recorded.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <Th>#</Th>
            <Th>Run</Th>
            <Th>Status</Th>
            <Th>Stage</Th>
            <Th>Failure</Th>
            <Th>Started</Th>
            <Th>Finished</Th>
            <Th>Next retry</Th>
            <Th>Read by</Th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <Td className="tabular-nums">{run.attempt}</Td>
              <Td className="tabular-nums">{run.id}</Td>
              <Td>{run.status}</Td>
              <Td>{run.failed_stage ?? "—"}</Td>
              <Td>
                {run.error_code ? <code className="text-xs">{run.error_code}</code> : "—"}
              </Td>
              <Td>{formatDateTime(run.started_at)}</Td>
              <Td>{formatDateTime(run.finished_at)}</Td>
              <Td>{formatDateTime(run.next_retry_at)}</Td>
              <Td>
                {run.provider ? `${run.provider}${run.model ? ` · ${run.model}` : ""}` : "—"}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children }: { children?: React.ReactNode }) {
  return <th className="whitespace-nowrap px-3 py-2 font-medium">{children}</th>;
}

function Td({
  children,
  className,
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <td className={`whitespace-nowrap border-t px-3 py-2 ${className ?? ""}`}>
      {children}
    </td>
  );
}
