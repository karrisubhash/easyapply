import type { VersionStatus } from "@easyapply/shared";

import { Badge } from "@/components/ui/badge";

/**
 * Draft versus Confirmed is the most important distinction in the product, so
 * it gets a consistent visual treatment everywhere it appears rather than being
 * re-styled per screen.
 */
export function StatusBadge({ status }: { status: VersionStatus }) {
  return status === "confirmed" ? (
    <Badge variant="confirmed" aria-label="Confirmed version">
      Confirmed
    </Badge>
  ) : (
    <Badge variant="draft" aria-label="Draft version">
      Draft
    </Badge>
  );
}

export function CurrentBadge() {
  return (
    <Badge variant="secondary" aria-label="Current version">
      Current
    </Badge>
  );
}
