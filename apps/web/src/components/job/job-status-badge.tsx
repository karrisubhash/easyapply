import {
  JOB_FAILURE_HELP,
  JOB_STAGE_LABELS,
  jobFailureTitle as sharedJobFailureTitle,
  type JobProcessingRun,
} from "@easyapply/shared";

import { Badge } from "@/components/ui/badge";
import { humanise } from "@/lib/utils";

/**
 * The mirror of `resume/processing-status.tsx`, sized down for a shorter state
 * graph: no OCR, no multi-stage extraction, just "structuring the description"
 * and then done one way or the other.
 *
 * The words — stage labels and failure help — are shared with the Chrome
 * extension (@easyapply/shared, presentation.ts), so both clients say the same
 * thing about the same run. Every failure string there is written for the
 * person reading it, never the server's own words verbatim: a structuring
 * bug's exception text can quote the job description, and the description is
 * not ours to show back unfiltered.
 */

export function jobStatusLabel(status: string): string {
  return JOB_STAGE_LABELS[status] ?? humanise(status);
}

export function JobStatusBadge({ run }: { run: JobProcessingRun | null | undefined }) {
  if (!run) {
    return <Badge variant="secondary">Not processed</Badge>;
  }
  if (run.status === "completed") {
    return <Badge variant="confirmed">Ready to review</Badge>;
  }
  if (run.status === "failed") {
    return (
      <Badge variant="outline" className="border-destructive text-destructive">
        Failed
      </Badge>
    );
  }
  if (run.status === "queued") {
    return <Badge variant="secondary">Queued</Badge>;
  }
  return <Badge variant="draft">{jobStatusLabel(run.status)}</Badge>;
}

/** "Processing failed while reading the description", or just "Processing failed". */
export function jobFailureTitle(run: Pick<JobProcessingRun, "failed_stage">): string {
  return sharedJobFailureTitle(run);
}

export function jobFailureExplanation(run: JobProcessingRun): string {
  if (run.error_code && JOB_FAILURE_HELP[run.error_code]) {
    return JOB_FAILURE_HELP[run.error_code];
  }
  return run.error_message ?? "Processing failed.";
}
