"use client";

import { MATCH_STATUS_LABELS, type MatchStatus } from "@easyapply/shared";
import { CircleHelp, CircleSlash, CircleCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * The three verdicts, labelled in the words the domain uses (shared with the
 * Chrome extension: @easyapply/shared, presentation.ts).
 *
 * "Missing" is deliberately not styled as a failure. §13 forbids this engine
 * from concluding that a candidate is unsuitable, and a red badge on every
 * unmet requirement says exactly that in colour where the words are careful
 * not to. Missing is neutral; uncertain is the one that asks for attention,
 * because it is the one a person can actually resolve.
 */

const PRESENTATION: Record<
  MatchStatus,
  { label: string; className: string; Icon: typeof CircleCheck }
> = {
  matched: {
    label: MATCH_STATUS_LABELS.matched,
    className: "border-emerald-600/30 bg-emerald-600/10 text-emerald-800 dark:text-emerald-300",
    Icon: CircleCheck,
  },
  uncertain: {
    label: MATCH_STATUS_LABELS.uncertain,
    className: "border-amber-600/30 bg-amber-600/10 text-amber-800 dark:text-amber-300",
    Icon: CircleHelp,
  },
  missing: {
    label: MATCH_STATUS_LABELS.missing,
    // Neutral in hue, like its siblings, and readable like them too:
    // `text-muted-foreground` on `bg-muted` measures 4.35:1, which is under AA.
    // Neutral-600/300 keeps the grey and lands at 7.2:1 light and 7.3:1 dark —
    // the same weight the emerald and amber badges above already carry.
    className:
      "border-muted-foreground/30 bg-muted text-neutral-600 dark:text-neutral-300",
    Icon: CircleSlash,
  },
};

export function matchStatusLabel(status: string): string {
  return PRESENTATION[status as MatchStatus]?.label ?? status;
}

export function MatchStatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  const presentation = PRESENTATION[status as MatchStatus];
  if (!presentation) return <Badge variant="outline">{status}</Badge>;

  const { label, className: tone, Icon } = presentation;
  return (
    <Badge variant="outline" className={cn("gap-1", tone, className)}>
      <Icon aria-hidden className="size-3" />
      {label}
    </Badge>
  );
}
