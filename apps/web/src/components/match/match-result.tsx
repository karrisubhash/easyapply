"use client";

import {
  MATCH_EVIDENCE_TIER_LABELS,
  type MatchDetail,
  type MatchEvidence,
  type MatchEvidenceTier,
  type MatchRequirementResult,
} from "@easyapply/shared";
import { FileText, Quote } from "lucide-react";

import { MatchStatusBadge } from "@/components/match/match-status-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  CATEGORY_LABELS,
  asPercentage,
  groupByCategory,
} from "@/lib/api/match-queries";
import { cn, formatDate, humanise } from "@/lib/utils";

/**
 * A match result, arranged so the evidence is what you read.
 *
 * §20's instruction — "do not make the percentage visually dominate the page" —
 * is a statement about what this screen is for. The useful question is not
 * "how good is this match" but "what does this posting ask for that my profile
 * does not show, and what did it point at for the things it did". So the
 * coverage number appears once, small, beside the counts that explain it, and
 * everything below is requirements and the profile rows that justify them.
 *
 * Nothing here derives a verdict. Every status, explanation and piece of
 * evidence is rendered as the server sent it; the component's only decisions
 * are ordering and grouping.
 */
export function MatchResultView({ match }: { match: MatchDetail }) {
  const groups = groupByCategory(match.requirement_results ?? []);

  return (
    <div className="space-y-6">
      <CoverageSummary match={match} />

      {groups.length === 0 ? (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            This posting has no structured requirements to check a profile against.
          </CardContent>
        </Card>
      ) : (
        groups.map((group) => (
          <Card key={group.category}>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">{CATEGORY_LABELS[group.category]}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              {group.required.length > 0 ? (
                <RequirementGroup
                  label="Required"
                  results={group.required}
                  testId={`${group.category}-required`}
                />
              ) : null}
              {group.preferred.length > 0 ? (
                <RequirementGroup
                  label="Preferred"
                  results={group.preferred}
                  testId={`${group.category}-preferred`}
                />
              ) : null}
            </CardContent>
          </Card>
        ))
      )}

      <Provenance match={match} />
    </div>
  );
}

/**
 * The counts first, the percentage second and small.
 *
 * Required and preferred are separate rows because they are separate facts:
 * §8's whole point is that a missing preferred requirement is not a failed
 * qualification, and one blended bar would say it was.
 */
function CoverageSummary({ match }: { match: MatchDetail }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Requirement coverage</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <AxisRow
          label="Required"
          total={match.required_total}
          matched={match.required_matched}
          missing={match.required_missing}
          uncertain={match.required_uncertain}
          coverage={match.required_coverage}
        />
        <AxisRow
          label="Preferred"
          total={match.preferred_total}
          matched={match.preferred_matched}
          missing={match.preferred_missing}
          uncertain={match.preferred_uncertain}
          coverage={match.preferred_coverage}
        />
        <p className="text-xs text-muted-foreground">
          Coverage of this posting&rsquo;s structured requirements by evidence in the profile
          version that was matched. It is not a prediction about an application.
          Requirements that could not be settled count against coverage but are listed as
          uncertain rather than missing.
        </p>
      </CardContent>
    </Card>
  );
}

function AxisRow({
  label,
  total,
  matched,
  missing,
  uncertain,
  coverage,
}: {
  label: string;
  total: number;
  matched: number;
  missing: number;
  uncertain: number;
  coverage: number;
}) {
  if (total === 0) {
    return (
      <div className="text-sm">
        <span className="font-medium">{label}</span>
        <span className="ml-2 text-muted-foreground">
          This posting states no {label.toLowerCase()} requirements.
        </span>
      </div>
    );
  }

  return (
    <div data-testid={`axis-${label.toLowerCase()}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-sm text-muted-foreground">
          {matched} of {total} matched
        </span>
        <span className="text-xs text-muted-foreground">
          · {missing} not found · {uncertain} uncertain
        </span>
        <span className="ml-auto text-xs tabular-nums text-muted-foreground">
          {asPercentage(coverage)}% coverage
        </span>
      </div>
      <div
        className="mt-1.5 flex h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={`${label}: ${matched} matched, ${uncertain} uncertain, ${missing} not found, out of ${total}`}
      >
        <div className="bg-emerald-600/70" style={{ width: `${(matched / total) * 100}%` }} />
        <div className="bg-amber-500/60" style={{ width: `${(uncertain / total) * 100}%` }} />
      </div>
    </div>
  );
}

function RequirementGroup({
  label,
  results,
  testId,
}: {
  label: string;
  results: MatchRequirementResult[];
  testId: string;
}) {
  return (
    <section data-testid={testId}>
      <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
        <span className="ml-1 font-normal normal-case">({results.length})</span>
      </h3>
      <ul className="space-y-3">
        {results.map((result) => (
          <li key={result.id}>
            <RequirementRow result={result} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function RequirementRow({ result }: { result: MatchRequirementResult }) {
  // Optional in the generated contract because the field has a server-side
  // default, never actually absent in a response. A MISSING verdict sends an
  // empty list — which is the claim "we looked and found nothing", not an
  // omission.
  const evidence = result.evidence ?? [];

  return (
    <div className="rounded-md border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 text-sm font-medium">{result.requirement_label}</p>
        <MatchStatusBadge status={result.status} />
      </div>

      {/* The explanation is the product, not a tooltip. Every verdict says why
          it was reached, including the ones that found nothing. */}
      <p className="mt-1.5 text-sm text-muted-foreground">{result.explanation}</p>

      {evidence.length > 0 ? (
        <ul className="mt-2.5 space-y-1.5">
          {evidence.map((item, index) => (
            <li key={index}>
              <EvidenceRow evidence={item} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * How strong each grade of evidence is, in the words a user would use.
 *
 * Shown because the tier is the answer to the question the engine's own
 * benchmark found people cannot otherwise answer: why did listing a skill
 * satisfy one requirement and not another?
 */
const TIER_LABELS: Record<MatchEvidenceTier, string> = MATCH_EVIDENCE_TIER_LABELS;

/** One profile-version row, as it read at the time the match was made. */
function EvidenceRow({ evidence }: { evidence: MatchEvidence }) {
  const tier = evidence.tier as MatchEvidenceTier | null;
  // Evidence that did NOT meet the bar is shown rather than hidden — it is
  // how an uncertain verdict explains itself. Muted, so it does not read as
  // support for a conclusion it did not support.
  const fell_short = !evidence.qualifies;

  return (
    <div
      data-testid="evidence"
      className={cn(
        "flex items-start gap-2 rounded border-l-2 px-2.5 py-1.5 text-xs",
        fell_short
          ? "border-dashed border-muted-foreground/30 bg-muted/20"
          : "border-muted-foreground/20 bg-muted/40",
      )}
    >
      <FileText aria-hidden className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
      <div className="min-w-0 space-y-0.5">
        <p className="font-medium">{evidence.label}</p>
        {evidence.detail ? <p className="text-muted-foreground">{evidence.detail}</p> : null}
        {evidence.quote ? (
          <p className="flex items-start gap-1 italic text-muted-foreground">
            <Quote aria-hidden className="mt-0.5 size-2.5 shrink-0" />
            <span>{evidence.quote}</span>
          </p>
        ) : null}
        <p className="flex flex-wrap items-center gap-1.5 pt-0.5">
          {tier ? (
            <Badge variant="outline" className="px-1.5 py-0 text-[10px] font-normal">
              {TIER_LABELS[tier] ?? tier}
            </Badge>
          ) : null}
          {evidence.note ? (
            <span className="text-muted-foreground">{evidence.note}</span>
          ) : null}
        </p>
      </div>
    </div>
  );
}

/**
 * What produced this result.
 *
 * Present because a stored verdict is only interpretable alongside the rules
 * that reached it and the inputs it was reached from — and because
 * `as_of_date` is the reason "5+ years" gives the same answer next year.
 */
function Provenance({ match }: { match: MatchDetail }) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
      <Badge variant="outline">{humanise(match.algorithm_version)}</Badge>
      {/*
       * The version NUMBER, which is what the user confirmed and what every
       * other screen calls it. This printed `profile_version_id` — the database
       * row's primary key — under the label "Profile version". The two agree on
       * a freshly truncated database, so every test and every demo looked
       * right; on a real profile they do not, and a match run against version 1
       * was reported on screen as version 2.
       *
       * The raw requirements id that used to sit beside it is gone. It was the
       * `job_requirements` primary key, and a person cannot look that up, act
       * on it, or tell two of them apart — it was a number in a line of facts,
       * which reads as a fact.
       */}
      {match.profile_version_number !== null &&
      match.profile_version_number !== undefined ? (
        <span>Profile version {match.profile_version_number}</span>
      ) : null}
      <span>· Measured against {formatDate(match.as_of_date)}</span>
    </p>
  );
}
