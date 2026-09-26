"use client";

import type {
  JobCandidate,
  JobExperienceItem,
  JobProvenance,
} from "@easyapply/shared";
import { Quote } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * What the structurer read out of a job's description, with its evidence.
 *
 * The mirror of `resume/candidate-review.tsx`: every claim shows the section
 * it was found under and the exact text it was taken from, and a citation the
 * server could not verify against the description is called out rather than
 * quietly rendered — the same "review screen, not a rubber stamp" posture.
 *
 * Required and preferred are shown as visually distinct groups everywhere the
 * domain distinguishes them, because that distinction is the entire reason
 * `JobCandidate` splits them into separate lists rather than one bag of
 * skills — collapsing them back together here would throw the fact away one
 * layer later than the backend already refused to.
 */
export function JobRequirementsReview({ candidate }: { candidate: JobCandidate }) {
  const isEmpty =
    (candidate.required_skills?.length ?? 0) === 0 &&
    (candidate.preferred_skills?.length ?? 0) === 0 &&
    (candidate.required_experience?.length ?? 0) === 0 &&
    (candidate.preferred_experience?.length ?? 0) === 0 &&
    (candidate.education?.length ?? 0) === 0 &&
    (candidate.certifications?.length ?? 0) === 0 &&
    (candidate.responsibilities?.length ?? 0) === 0 &&
    (candidate.technologies?.length ?? 0) === 0 &&
    (candidate.qualifications?.length ?? 0) === 0;

  if (isEmpty) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">
          No structured requirements were found in this job&rsquo;s description.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <RequiredPreferredSection
        title="Skills"
        required={candidate.required_skills}
        preferred={candidate.preferred_skills}
        render={(item) => <SkillChip name={item.name} provenance={item.provenance} />}
        chipLabel={(item) => item.name}
        chips
      />

      <RequiredPreferredSection
        title="Experience"
        required={candidate.required_experience}
        preferred={candidate.preferred_experience}
        render={(item) => <ExperienceEntry item={item} />}
      />

      <FlagSection title="Technologies" items={candidate.technologies} chips />
      <FlagSection title="Education" items={candidate.education} />
      <FlagSection title="Certifications" items={candidate.certifications} />
      <FlagSection title="Qualifications" items={candidate.qualifications} />

      {candidate.responsibilities && candidate.responsibilities.length > 0 ? (
        <Section title="Responsibilities" count={candidate.responsibilities.length}>
          {candidate.responsibilities.map((item, index) => (
            <Entry key={index} heading={item.description} provenance={item.provenance} />
          ))}
        </Section>
      ) : null}
    </div>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          {title}
          <span className="text-xs font-normal text-muted-foreground">({count})</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  );
}

/** Skills and experience: the domain splits these into two lists at the API
 * level, so the UI shows two groups rather than one list with a flag column —
 * the split is the point, not an implementation detail to hide. */
function RequiredPreferredSection<T extends { provenance?: JobProvenance[] }>({
  title,
  required,
  preferred,
  render,
  chips,
  chipLabel,
}: {
  title: string;
  required?: T[];
  preferred?: T[];
  render: (item: T) => React.ReactNode;
  chips?: boolean;
  /** Required when `chips` is set — the disclosure below an unverified chip
   * needs a name to show, and only the caller knows which field that is. */
  chipLabel?: (item: T) => string;
}) {
  const label = chipLabel ?? (() => "");
  const requiredItems = required ?? [];
  const preferredItems = preferred ?? [];
  if (requiredItems.length === 0 && preferredItems.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {requiredItems.length > 0 ? (
          <Group label="Required" count={requiredItems.length}>
            {chips ? (
              <>
                <div className="flex flex-wrap gap-2">{requiredItems.map((item, i) => <span key={i}>{render(item)}</span>)}</div>
                <UnverifiedDisclosure items={requiredItems} label={label} />
              </>
            ) : (
              <div className="space-y-3">{requiredItems.map((item, i) => <div key={i}>{render(item)}</div>)}</div>
            )}
          </Group>
        ) : null}
        {preferredItems.length > 0 ? (
          <Group label="Preferred" count={preferredItems.length}>
            {chips ? (
              <>
                <div className="flex flex-wrap gap-2">{preferredItems.map((item, i) => <span key={i}>{render(item)}</span>)}</div>
                <UnverifiedDisclosure items={preferredItems} label={label} />
              </>
            ) : (
              <div className="space-y-3">{preferredItems.map((item, i) => <div key={i}>{render(item)}</div>)}</div>
            )}
          </Group>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Everything else carries `is_required` per item rather than being split
 * into two lists — the shape the backend schema itself uses, since a posting
 * saying "Bachelor's required, Master's preferred" is one section making two
 * claims, not two sections. */
function FlagSection({
  title,
  items,
  chips,
}: {
  title: string;
  items?: Array<{
    name?: string;
    description?: string;
    degree_level?: string | null;
    is_required: boolean;
    provenance?: JobProvenance[];
  }>;
  chips?: boolean;
}) {
  const list = items ?? [];
  if (list.length === 0) return null;

  const required = list.filter((item) => item.is_required);
  const preferred = list.filter((item) => !item.is_required);

  const label = (item: (typeof list)[number]) =>
    item.name ?? item.description ?? item.degree_level ?? "—";

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          {title}
          <span className="text-xs font-normal text-muted-foreground">({list.length})</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {required.length > 0 ? (
          <Group label="Required" count={required.length}>
            {chips ? (
              <>
                <div className="flex flex-wrap gap-2">
                  {required.map((item, i) => (
                    <SkillChip key={i} name={label(item)} provenance={item.provenance} />
                  ))}
                </div>
                <UnverifiedDisclosure items={required} label={label} />
              </>
            ) : (
              <div className="space-y-3">
                {required.map((item, i) => (
                  <Entry key={i} heading={label(item)} provenance={item.provenance} />
                ))}
              </div>
            )}
          </Group>
        ) : null}
        {preferred.length > 0 ? (
          <Group label="Preferred" count={preferred.length}>
            {chips ? (
              <>
                <div className="flex flex-wrap gap-2">
                  {preferred.map((item, i) => (
                    <SkillChip key={i} name={label(item)} provenance={item.provenance} />
                  ))}
                </div>
                <UnverifiedDisclosure items={preferred} label={label} />
              </>
            ) : (
              <div className="space-y-3">
                {preferred.map((item, i) => (
                  <Entry key={i} heading={label(item)} provenance={item.provenance} />
                ))}
              </div>
            )}
          </Group>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Group({
  label,
  count,
  children,
}: {
  label: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label} <span className="font-normal">({count})</span>
      </p>
      {children}
    </div>
  );
}

function ExperienceEntry({ item }: { item: JobExperienceItem }) {
  const range =
    item.minimum_years != null
      ? item.maximum_years != null
        ? `${item.minimum_years}–${item.maximum_years} years`
        : `${item.minimum_years}+ years`
      : null;

  return (
    <Entry
      heading={range ? `${range} — ${item.description}` : item.description}
      provenance={item.provenance}
    />
  );
}

/**
 * A chip's warning glyph is a tooltip, and a tooltip is not a disclosure — the
 * same reasoning `resume/candidate-review.tsx` gives for showing an
 * unverified skill's full citation a second time, in plain text, below the
 * chip row rather than only on hover.
 */
function UnverifiedDisclosure<T extends { provenance?: JobProvenance[] }>({
  items,
  label,
}: {
  items: T[];
  label: (item: T) => string;
}) {
  const unverifiedItems = items.filter((item) =>
    (item.provenance ?? []).some((entry) => entry.verified === false),
  );
  if (unverifiedItems.length === 0) return null;

  return (
    <div className="mt-2 space-y-2">
      {unverifiedItems.map((item, index) => (
        <div key={index}>
          <p className="text-sm font-medium">{label(item)}</p>
          <Citations provenance={item.provenance} />
        </div>
      ))}
    </div>
  );
}

function SkillChip({ name, provenance }: { name: string; provenance?: JobProvenance[] }) {
  const unverified = (provenance ?? []).some((entry) => entry.verified === false);
  return (
    <Badge variant="outline" title={citationTitle(provenance)}>
      {name}
      {unverified ? <span className="ml-1 text-destructive">⚠</span> : null}
    </Badge>
  );
}

function Entry({
  heading,
  provenance,
}: {
  heading: string;
  provenance?: JobProvenance[];
}) {
  return (
    <div className="border-l-2 border-border pl-4">
      <p className="text-sm">{heading}</p>
      <Citations provenance={provenance} />
    </div>
  );
}

function Citations({ provenance }: { provenance?: JobProvenance[] }) {
  if (!provenance || provenance.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1">
      {provenance.map((entry, index) => (
        <li
          key={index}
          className="flex items-start gap-1.5 text-xs text-muted-foreground"
          data-testid="citation"
        >
          <Quote aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
          <span>
            {entry.source_section ? (
              <span className="font-medium">{entry.source_section}</span>
            ) : null}
            {entry.source_section ? ": " : ""}
            &ldquo;{entry.quote}&rdquo;
            <span
              // Not a lighter grey than the quote it annotates. `muted-foreground`
              // is already the faintest token that clears AA on this background
              // (4.7:1); at 70% opacity it composited to #9d9d9d and measured
              // 2.71:1, so the confidence figure — the one number on this page
              // that says how much to trust the rest — was the least readable
              // thing on it. De-emphasis here is the smaller type, not less ink.
              className="ml-1"
              data-testid="citation-confidence"
              title="The structurer's own estimate of how sure it was"
            >
              ({Math.round(entry.confidence * 100)}% confidence)
            </span>
            {entry.verified === false ? (
              <span className="ml-1 text-destructive">
                — this quote was not found in the description
              </span>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

function citationTitle(provenance?: JobProvenance[]): string | undefined {
  const first = provenance?.[0];
  if (!first) return undefined;
  return first.source_section ? `${first.source_section}: ${first.quote}` : first.quote;
}
