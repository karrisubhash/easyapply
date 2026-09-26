import type { Snapshot } from "@easyapply/shared";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate, formatDateWithPrecision, humanise } from "@/lib/utils";

/**
 * The read-only rendering of a snapshot — the review step, and the only way a
 * confirmed version is ever shown.
 */
export function SnapshotView({ snapshot }: { snapshot: Snapshot }) {
  // The name is the card's title, so it is not repeated in the detail line.
  const contactShown = [
    snapshot.contact?.email,
    snapshot.contact?.phone,
    snapshot.contact?.location,
  ].filter(Boolean) as string[];
  const hasContact = Boolean(snapshot.contact?.full_name) || contactShown.length > 0;

  // Contact, summary and links count towards "has something to show". Leaving
  // them out rendered "no career data yet" over a profile that had the person's
  // name, address and every link on their resume.
  const isEmpty =
    !hasContact &&
    !snapshot.summary &&
    snapshot.links.length === 0 &&
    snapshot.experiences.length === 0 &&
    snapshot.education.length === 0 &&
    snapshot.skills.length === 0 &&
    snapshot.projects.length === 0 &&
    snapshot.certifications.length === 0;

  if (isEmpty) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">
          This version has no career data yet.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {(hasContact || snapshot.links.length > 0) && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {snapshot.contact?.full_name ?? "Contact"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {contactShown.length > 0 && (
              <p className="text-sm text-muted-foreground">
                {contactShown.join(" · ")}
              </p>
            )}
            {snapshot.links.length > 0 && (
              <ul className="flex flex-wrap gap-x-4 gap-y-1">
                {snapshot.links.map((link, index) => (
                  <li key={index} className="text-sm">
                    <a
                      href={link.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="underline underline-offset-4 hover:text-foreground"
                    >
                      {link.label ?? link.url}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      {snapshot.summary && (
        <Section title="Summary">
          <p className="whitespace-pre-line text-sm">{snapshot.summary}</p>
        </Section>
      )}

      {snapshot.experiences.length > 0 && (
        <Section title="Experience" count={snapshot.experiences.length}>
          {snapshot.experiences.map((item, index) => (
            <Entry
              key={index}
              heading={item.job_title}
              subheading={item.company}
              meta={[
                item.employment_type ? humanise(item.employment_type) : null,
                item.location,
                dateRange(
                  item.start_date,
                  item.end_date,
                  item.start_date_precision,
                  item.end_date_precision,
                ),
              ]}
              body={item.description}
            />
          ))}
        </Section>
      )}

      {snapshot.education.length > 0 && (
        <Section title="Education" count={snapshot.education.length}>
          {snapshot.education.map((item, index) => (
            <Entry
              key={index}
              heading={item.degree ?? item.institution}
              subheading={item.degree ? item.institution : null}
              meta={[item.field_of_study, item.grade, dateRange(
                  item.start_date,
                  item.end_date,
                  item.start_date_precision,
                  item.end_date_precision,
                )]}
              body={item.description}
            />
          ))}
        </Section>
      )}

      {snapshot.skills.length > 0 && (
        <Section title="Skills" count={snapshot.skills.length}>
          <ul className="flex flex-wrap gap-2">
            {snapshot.skills.map((item, index) => (
              <li key={index}>
                <Badge variant="outline">
                  {item.name}
                  {item.proficiency ? ` · ${humanise(item.proficiency)}` : ""}
                  {item.years_of_experience ? ` · ${item.years_of_experience}y` : ""}
                </Badge>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {snapshot.projects.length > 0 && (
        <Section title="Projects" count={snapshot.projects.length}>
          {snapshot.projects.map((item, index) => (
            <Entry
              key={index}
              heading={item.name}
              subheading={item.role}
              meta={[
                dateRange(
                  item.start_date,
                  item.end_date,
                  item.start_date_precision,
                  item.end_date_precision,
                ),
                item.technologies.length > 0 ? item.technologies.join(", ") : null,
              ]}
              body={item.description}
              links={item.links}
            />
          ))}
        </Section>
      )}

      {snapshot.certifications.length > 0 && (
        <Section title="Certifications" count={snapshot.certifications.length}>
          {snapshot.certifications.map((item, index) => (
            <Entry
              key={index}
              heading={item.name}
              subheading={item.issuing_organization}
              meta={[
                item.issue_date ? `Issued ${formatDate(item.issue_date)}` : null,
                item.expiration_date ? `Expires ${formatDate(item.expiration_date)}` : null,
                item.credential_id,
              ]}
            />
          ))}
        </Section>
      )}
    </div>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  /** Omitted by a section that holds one thing rather than a list of them. */
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          {title}
          {count !== undefined && (
            <span className="text-xs font-normal text-muted-foreground">({count})</span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  );
}

function Entry({
  heading,
  subheading,
  meta,
  body,
  links,
}: {
  heading: string;
  subheading?: string | null;
  meta?: (string | null | undefined)[];
  body?: string | null;
  /** A project's own links, read off the resume beside it. */
  links?: { url: string; label?: string | null }[];
}) {
  const shown = (meta ?? []).filter(Boolean);
  return (
    <div className="border-l-2 border-border pl-4">
      <p className="font-medium">{heading}</p>
      {subheading && <p className="text-sm text-muted-foreground">{subheading}</p>}
      {shown.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">{shown.join(" · ")}</p>
      )}
      {body && <p className="mt-2 whitespace-pre-line text-sm">{body}</p>}
      {links && links.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {links.map((link, index) => (
            <li key={index} className="text-sm">
              <a
                href={link.url}
                target="_blank"
                rel="noreferrer noopener"
                className="underline underline-offset-4 hover:text-foreground"
              >
                {link.label ?? link.url}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Precision = "year" | "month" | "day" | null | undefined;

function dateRange(
  start?: string | null,
  end?: string | null,
  startPrecision?: Precision,
  endPrecision?: Precision,
): string | null {
  if (!start && !end) return null;
  // An absent end date is what marks a position as current; the domain has no
  // separate flag, so neither does this.
  const from = start ? formatDateWithPrecision(start, startPrecision) : "?";
  const to = end ? formatDateWithPrecision(end, endPrecision) : "Present";
  return `${from} — ${to}`;
}
