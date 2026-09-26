"use client";

import type {
  CandidateDate,
  Provenance,
  StructuredResult,
} from "@easyapply/shared";
import { AlertTriangle, Quote } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * What the machine read, with its evidence.
 *
 * Every claim shows the page it came from and the text it was taken from, and a
 * citation the server could not find on that page is called out rather than
 * quietly rendered — that is the difference between a review screen and a
 * rubber stamp.
 */
export function CandidateReview({ result }: { result: StructuredResult }) {
  const candidate = result.candidate;

  return (
    <div className="space-y-4">
      {result.unmapped && result.unmapped.length > 0 && (
        <Alert variant="warning">
          <AlertTitle>Some items could not be added to the draft</AlertTitle>
          <AlertDescription>
            <p className="mb-2">
              These were found in the resume but are missing information the profile requires. They
              were left out rather than guessed at — you can add them by hand.
            </p>
            <ul className="list-disc space-y-1 pl-5">
              {result.unmapped.map((item, index) => (
                <li key={index}>
                  <span className="font-medium">{item.summary}</span> — {item.reason}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <Section title="Experience" count={candidate.experiences?.length ?? 0}>
        {(candidate.experiences ?? []).map((item, index) => (
          <Item
            key={index}
            heading={item.job_title ?? item.company ?? "Untitled role"}
            subheading={item.job_title ? item.company : null}
            dates={[item.start_date, item.end_date]}
            provenance={item.provenance}
          />
        ))}
      </Section>

      <Section title="Education" count={candidate.education?.length ?? 0}>
        {(candidate.education ?? []).map((item, index) => (
          <Item
            key={index}
            heading={item.degree ?? item.institution ?? "Qualification"}
            subheading={item.degree ? item.institution : null}
            dates={[item.start_date, item.end_date]}
            provenance={item.provenance}
          />
        ))}
      </Section>

      <Section title="Skills" count={candidate.skills?.length ?? 0}>
        <ul className="flex flex-wrap gap-2">
          {(candidate.skills ?? []).map((item, index) => (
            <li key={index}>
              <Badge variant="outline" title={citationTitle(item.provenance)}>
                {item.name}
                {unverified(item.provenance) && (
                  <AlertTriangle aria-label="Unverified citation" className="size-3" />
                )}
              </Badge>
            </li>
          ))}
        </ul>

        {/* A skill whose evidence did not check out gets the same visible
            explanation as anything else — a tooltip is not a disclosure. */}
        {(candidate.skills ?? [])
          .filter((item) => unverified(item.provenance))
          .map((item, index) => (
            <div key={index}>
              <p className="text-sm font-medium">{item.name}</p>
              <Citations provenance={item.provenance} />
            </div>
          ))}
      </Section>

      <Section title="Projects" count={candidate.projects?.length ?? 0}>
        {(candidate.projects ?? []).map((item, index) => (
          <Item
            key={index}
            heading={item.name ?? "Project"}
            subheading={item.role}
            dates={[item.start_date, item.end_date]}
            provenance={item.provenance}
          />
        ))}
      </Section>

      <Section title="Certifications" count={candidate.certifications?.length ?? 0}>
        {(candidate.certifications ?? []).map((item, index) => (
          <Item
            key={index}
            heading={item.name ?? "Certification"}
            subheading={item.issuing_organization}
            dates={[item.issue_date, item.expiration_date]}
            provenance={item.provenance}
          />
        ))}
      </Section>
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
  if (count === 0) return null;
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

function Item({
  heading,
  subheading,
  dates,
  provenance,
}: {
  heading: string;
  subheading?: string | null;
  dates?: (CandidateDate | null | undefined)[];
  provenance?: Provenance[];
}) {
  return (
    <div className="border-l-2 border-border pl-4">
      <p className="font-medium">{heading}</p>
      {subheading && <p className="text-sm text-muted-foreground">{subheading}</p>}
      {dates && dates.some(Boolean) && (
        <p className="mt-1 text-xs text-muted-foreground">
          {dates.filter(Boolean).map((date, index) => (
            <span key={index}>
              {index > 0 && " — "}
              <DateWithPrecision date={date!} />
            </span>
          ))}
        </p>
      )}
      <Citations provenance={provenance} />
    </div>
  );
}

/**
 * Shows the words the resume used, not a date we normalized.
 *
 * "May 2024" is displayed as "May 2024". Rendering it as 1 May 2024 would put
 * back exactly the false precision the pipeline works to avoid.
 */
function DateWithPrecision({ date }: { date: CandidateDate }) {
  return (
    <span title={date.value ? `Stored as ${date.value} (${date.precision} precision)` : "Not normalized"}>
      {date.raw}
    </span>
  );
}

function Citations({ provenance }: { provenance?: Provenance[] }) {
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
            <span className="font-medium">Page {entry.page}</span>: “{entry.quote}”
            {entry.verified === false && (
              <span className="ml-1 text-destructive">
                — this quote was not found on that page
              </span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

function unverified(provenance?: Provenance[]): boolean {
  return (provenance ?? []).some((entry) => entry.verified === false);
}

function citationTitle(provenance?: Provenance[]): string {
  const first = provenance?.[0];
  return first ? `Page ${first.page}: ${first.quote}` : "";
}
