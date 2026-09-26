import {
  TAILORING_FAILURE_HELP,
  TAILORING_STAGE_LABELS,
  tailoringFailureTitle as sharedTailoringFailureTitle,
  type TailoringRun,
  type TailoringRunDetail,
} from "@easyapply/shared";

import { Badge } from "@/components/ui/badge";
import { humanise } from "@/lib/utils";

type AnyRun = TailoringRun | TailoringRunDetail;

/**
 * Tailoring has TWO outcomes to communicate and only one badge to do it in.
 *
 * `status` says how far the pipeline got. `grounding_status` says whether the
 * document it produced is one we stand behind. A run can be completed and not
 * approved, and that combination is the whole point of the feature — so it gets
 * its own label rather than sharing "Ready" with a document that passed.
 *
 * "Not approved — no file" is deliberately not "Failed": nothing went wrong
 * with the generation, and telling the user it failed would send them to retry
 * a thing that worked. It is not "Needs review" either, which it used to be:
 * that promised a review would release the document, and no review does — a
 * withheld document never gets a file. The review page is where they find out
 * which sentence is the problem.
 *
 * The stage labels and failure help are shared with the Chrome extension
 * (@easyapply/shared, presentation.ts). Failure help is written for the person
 * reading it, never the server's own words: a provider error quotes the prompt,
 * and the prompt contains the user's career history.
 */
export function tailoringStatusLabel(status: string): string {
  return TAILORING_STAGE_LABELS[status] ?? humanise(status);
}

export function TailoringStatusBadge({ run }: { run: AnyRun | null | undefined }) {
  if (!run) return <Badge variant="secondary">Not started</Badge>;

  if (run.status === "failed") {
    return (
      <Badge variant="outline" className="border-destructive text-destructive">
        Failed
      </Badge>
    );
  }

  if (run.status === "completed") {
    return run.grounding_status === "passed" ? (
      <Badge variant="confirmed">Ready</Badge>
    ) : (
      <Badge variant="outline" className="border-destructive text-destructive">
        Not approved — no file
      </Badge>
    );
  }

  if (run.is_stalled) {
    return (
      <Badge variant="outline" className="border-destructive text-destructive">
        Stopped responding
      </Badge>
    );
  }

  if (run.status === "queued") return <Badge variant="secondary">Queued</Badge>;

  return <Badge variant="draft">{tailoringStatusLabel(run.status)}</Badge>;
}


/** "Generation failed while preparing the file", or just "Generation failed". */
export function tailoringFailureTitle(run: Pick<AnyRun, "failed_stage">): string {
  return sharedTailoringFailureTitle(run);
}

export function tailoringFailureExplanation(run: AnyRun): string {
  if (run.error_code && TAILORING_FAILURE_HELP[run.error_code]) {
    return TAILORING_FAILURE_HELP[run.error_code];
  }
  return run.error_message ?? "Generation failed.";
}
