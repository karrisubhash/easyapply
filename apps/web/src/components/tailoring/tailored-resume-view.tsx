"use client";

import type {
  TailoredLine,
  TailoredResumeDocument,
} from "@easyapply/shared";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatDateWithPrecision } from "@/lib/utils";

/**
 * The generated resume, section by section, with its sources visible.
 *
 * The one design decision in this file that is not cosmetic: every generated
 * line can be expanded to show the profile text it came from. Not a tooltip,
 * not a footnote, not a separate "provenance" tab — an inline disclosure on the
 * line itself, because the question "where did this sentence come from?" is
 * asked about one sentence at a time, while reading it.
 *
 * The contact block is shown first and without per-line sourcing, because
 * every field in it is a column copied verbatim from the profile version rather
 * than a sentence derived from one — `grounding._check_contact` compares them
 * exactly, so there is no wording distance to disclose. It is shown at all
 * because it is what the user is checking before they send the file, and for a
 * while the generated resume had none of it.
 *
 * Lines whose wording is unchanged say so instead of showing the same string
 * twice. That is the honest signal in the default configuration: the
 * deterministic generator SELECTS rather than writes, so most of a document
 * will read "copied from your profile", and a line that does not is the line
 * worth looking at.
 */
export function TailoredResumeView({
  document,
}: {
  document: TailoredResumeDocument;
}) {
  const contact = document.contact;

  return (
    <div className="space-y-6">
      {contact ? (
        <Section
          title="Contact"
          description="Copied from your profile version, character for character. Nothing here is composed."
        >
          {contact.full_name ? (
            <p className="text-base font-medium">{contact.full_name}</p>
          ) : null}
          <p className="text-sm text-muted-foreground">
            {[contact.location, contact.phone, contact.email].filter(Boolean).join("  ·  ") ||
              "—"}
          </p>
          {(contact.links?.length ?? 0) > 0 ? (
            <p className="mt-2 flex flex-wrap gap-1.5">
              {(contact.links ?? []).map((link) => (
                <Badge key={link.url} variant="outline">
                  {link.label ?? link.url}
                </Badge>
              ))}
            </p>
          ) : null}
        </Section>
      ) : null}

      {document.headline || (document.summary?.length ?? 0) > 0 ? (
        <Section title="Summary">
          {document.headline ? (
            <p className="mb-3 text-base font-medium">{document.headline.text}</p>
          ) : null}
          <ul className="space-y-2">
            {(document.summary ?? []).map((line, index) => (
              <li key={index}>
                <SourcedLine line={line} />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {(document.skills?.length ?? 0) > 0 ? (
        <Section
          title="Skills"
          description="Grouped for this role. Every name here is one your profile lists."
        >
          <div className="space-y-3">
            {(document.skills ?? []).map((group) => (
              <div key={group.category}>
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {group.category}
                </p>
                <p className="mt-1 flex flex-wrap gap-1.5">
                  {(group.skills ?? []).map((skill) => (
                    <Badge key={skill} variant="secondary">
                      {skill}
                    </Badge>
                  ))}
                </p>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {(document.experience?.length ?? 0) > 0 ? (
        <Section title="Experience">
          <ol className="space-y-5">
            {(document.experience ?? []).map((entry, index) => (
              <li key={index}>
                <p className="font-medium">
                  {entry.job_title}, {entry.company}
                </p>
                <p className="text-xs text-muted-foreground">
                  {period(
                    entry.start_date,
                    entry.start_date_precision,
                    entry.end_date,
                    entry.end_date_precision,
                  )}
                  {entry.location ? ` · ${entry.location}` : ""}
                </p>
                <ul className="mt-2 space-y-2">
                  {(entry.bullets ?? []).map((line, position) => (
                    <li key={position}>
                      <SourcedLine line={line} />
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {(document.projects?.length ?? 0) > 0 ? (
        <Section title="Projects">
          <ol className="space-y-5">
            {(document.projects ?? []).map((project, index) => (
              <li key={index}>
                <p className="font-medium">
                  {project.name}
                  {project.role ? <span className="font-normal"> — {project.role}</span> : null}
                </p>
                {(project.technologies?.length ?? 0) > 0 ? (
                  <p className="mt-1 flex flex-wrap gap-1.5">
                    {(project.technologies ?? []).map((technology) => (
                      <Badge key={technology} variant="outline">
                        {technology}
                      </Badge>
                    ))}
                  </p>
                ) : null}
                <ul className="mt-2 space-y-2">
                  {(project.bullets ?? []).map((line, position) => (
                    <li key={position}>
                      <SourcedLine line={line} />
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {(document.education?.length ?? 0) > 0 ? (
        <Section title="Education">
          <ul className="space-y-2">
            {(document.education ?? []).map((entry, index) => (
              <li key={index}>
                <p className="font-medium">{entry.institution}</p>
                <p className="text-sm text-muted-foreground">
                  {[entry.degree, entry.field_of_study].filter(Boolean).join(", ") || "—"}
                </p>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {(document.certifications?.length ?? 0) > 0 ? (
        <Section title="Certifications">
          <ul className="space-y-2">
            {(document.certifications ?? []).map((entry, index) => (
              <li key={index}>
                <p className="font-medium">{entry.name}</p>
                {entry.issuing_organization ? (
                  <p className="text-sm text-muted-foreground">{entry.issuing_organization}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {(document.targeted_requirements?.length ?? 0) > 0 ? (
        <Section
          title="What this resume was aimed at"
          description="Requirements from this posting that the selected content speaks to. This says what the document emphasised — not that you meet them. The match result is where that question is answered."
        >
          <p className="flex flex-wrap gap-1.5">
            {(document.targeted_requirements ?? []).map((label) => (
              <Badge key={label} variant="outline">
                {label}
              </Badge>
            ))}
          </p>
        </Section>
      ) : null}
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

/**
 * One generated line, with the profile text behind it one click away.
 *
 * A native `<details>` rather than a state-driven panel: it is keyboard
 * accessible, it works before hydration, and it needs no library. The summary
 * row is the only interactive element, so a screen reader reads the line and
 * then the offer to see where it came from.
 */
function SourcedLine({ line }: { line: TailoredLine }) {
  const unchanged = line.text.trim() === line.source_quote.trim();

  return (
    <details className="group">
      <summary className="cursor-pointer list-none marker:content-none">
        <span className="text-sm">{line.text}</span>
        <span className="ml-2 whitespace-nowrap text-xs text-muted-foreground underline decoration-dotted underline-offset-2">
          {unchanged ? "copied from your profile" : "show original"}
        </span>
      </summary>
      <div className="mt-2 rounded-md border bg-muted/40 p-3 text-xs">
        {unchanged ? (
          <p className="text-muted-foreground">
            This line is your profile&rsquo;s own wording, unchanged. It was selected for
            this job, not rewritten.
          </p>
        ) : (
          <>
            <p className="font-medium uppercase tracking-wide text-muted-foreground">
              Your profile says
            </p>
            <p className="mt-1 break-words">{line.source_quote}</p>
            <p className="mt-3 font-medium uppercase tracking-wide text-muted-foreground">
              This resume says
            </p>
            <p className="mt-1 break-words">{line.text}</p>
          </>
        )}
        <p className="mt-3 text-muted-foreground">Source field: {line.source_field}</p>
      </div>
    </details>
  );
}

function period(
  start: string | null | undefined,
  startPrecision: "year" | "month" | "day" | null | undefined,
  end: string | null | undefined,
  endPrecision: "year" | "month" | "day" | null | undefined,
): string {
  const from = formatDateWithPrecision(start, startPrecision ?? "month");
  const to = end ? formatDateWithPrecision(end, endPrecision ?? "month") : "Present";
  return `${from} – ${to}`;
}
