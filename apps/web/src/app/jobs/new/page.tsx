"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import { useForm } from "react-hook-form";
import type { JobDetail, JobIngestionRequest } from "@easyapply/shared";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCreateJob } from "@/lib/api/job-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

/**
 * Save a job posting.
 *
 * This form posts the same `JobIngestionRequest` the Chrome extension will.
 * That is the whole design: one ingestion endpoint, one set of validators, one
 * normalizer, one deduplication rule. A hand-written "web job" path beside the
 * real one would be a second definition of what a job is, free to drift.
 *
 * Nothing here validates the payload beyond what the browser does for free.
 * URL scheme, field lengths, currency codes and control characters are the
 * server's rules (`app/schemas/job.py`), and re-implementing them here would
 * produce a second copy that disagrees — so a rejection comes back as a 422
 * with field-level issues and `ApiErrorAlert` renders exactly what the API
 * said. The one rule enforced here is presence, because each required field
 * gets its own sentence on the field rather than a silent refusal to submit.
 *
 * The description is required. It is what the requirements are read out of,
 * and a saved job cannot be changed: saving the same posting again returns the
 * job already stored, untouched. So a job saved without one could never be
 * matched or tailored against, and could not be fixed by saving it again.
 */

const SOURCE_TYPES = [
  "linkedin",
  "indeed",
  "greenhouse",
  "lever",
  "workday",
  "company_careers",
  "other",
] as const;

type FormValues = {
  source_type: (typeof SOURCE_TYPES)[number];
  source_url: string;
  job_title: string;
  company_name: string;
  location_raw: string;
  employment_type: string;
  workplace_type: string;
  description_text: string;
};

export default function NewJobPage() {
  const router = useRouter();
  const create = useCreateJob();
  // The job the server already had, when saving returned it rather than
  // creating one. Everything typed here was discarded — which the user has to
  // be told, because it looks exactly like a successful save.
  const [existing, setExisting] = React.useState<JobDetail | null>(null);

  usePageTitle("Add a job");

  const form = useForm<FormValues>({
    defaultValues: {
      source_type: "other",
      source_url: "",
      job_title: "",
      company_name: "",
      location_raw: "",
      employment_type: "",
      workplace_type: "",
      description_text: "",
    },
  });

  const errors = form.formState.errors;

  function onSubmit(values: FormValues) {
    // Empty optional strings become absent rather than "", because "" is a
    // value the server would have to decide the meaning of and `null` already
    // means "not stated".
    const payload: JobIngestionRequest = {
      source_type: values.source_type,
      source_url: values.source_url.trim(),
      job_title: values.job_title.trim(),
      company_name: values.company_name.trim() || null,
      location_raw: values.location_raw.trim() || null,
      employment_type: values.employment_type.trim() || null,
      workplace_type: values.workplace_type.trim() || null,
      description_text: values.description_text.trim() || null,
    };

    // A save whose answer was lost (a timeout, a dropped connection) and is then
    // retried comes back as "already saved" — with exactly what was entered.
    // Telling that user nothing was saved would be false.
    const isWhatWasEntered = (job: JobDetail) =>
      job.job_title === payload.job_title &&
      (job.company_name ?? null) === payload.company_name &&
      (job.description_text ?? null) === payload.description_text;

    setExisting(null);
    create.mutate(payload, {
      onSuccess: ({ job, created }) => {
        if (created || isWhatWasEntered(job)) router.push(`/jobs/${job.id}`);
        else setExisting(job);
      },
    });
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Add a job</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Paste a posting. EasyApply reads it into structured requirements you can match
            and tailor against.
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link href="/jobs">Back to jobs</Link>
        </Button>
      </div>

      {create.isError ? <ApiErrorAlert error={create.error} /> : null}

      {existing ? (
        <Alert variant="warning">
          <AlertTitle>You already saved this posting</AlertTitle>
          <AlertDescription>
            <p>
              A job with this link, found on the same site, was saved{" "}
              {formatDateTime(existing.created_at)} as &ldquo;{existing.job_title}&rdquo;. It was
              left exactly as it was: nothing you entered here was saved, including the
              description. A saved job cannot be edited.
            </p>
            {!existing.description_text ? (
              <p className="mt-2">
                That job has no description, so it has no requirements to match or tailor
                against.
              </p>
            ) : null}
            <div className="mt-3">
              <Button variant="outline" size="sm" asChild>
                <Link href={`/jobs/${existing.id}`}>Open the saved job</Link>
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : null}

      <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">The posting</CardTitle>
            <CardDescription>
              The title, the link and the description are required. The description is what
              the requirements are read out of — without it there is nothing to match or tailor
              against, and a saved job cannot be edited to add one later.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Job title" required error={errors.job_title?.message}>
                {({ id, required, describedBy }) => (
                  <Input
                    id={id}
                    required={required}
                    {...form.register("job_title", { validate: present("Job title") })}
                    placeholder="Senior Backend Engineer"
                    aria-invalid={Boolean(errors.job_title)}
                    aria-describedby={describedBy}
                  />
                )}
              </Field>

              <Field label="Company">
                {({ id }) => (
                  <Input
                    id={id}
                    {...form.register("company_name")}
                    placeholder="Acme Corp"
                  />
                )}
              </Field>

              <Field label="Posting URL" required error={errors.source_url?.message}>
                {({ id, required, describedBy }) => (
                  <Input
                    id={id}
                    required={required}
                    {...form.register("source_url", { validate: present("Posting URL") })}
                    type="url"
                    inputMode="url"
                    placeholder="https://example.com/jobs/123"
                    aria-invalid={Boolean(errors.source_url)}
                    aria-describedby={describedBy}
                  />
                )}
              </Field>

              <Field label="Where you found it">
                {({ id }) => (
                  <select
                    id={id}
                    {...form.register("source_type")}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                  >
                    {SOURCE_TYPES.map((value) => (
                      <option key={value} value={value}>
                        {value.replace(/_/g, " ")}
                      </option>
                    ))}
                  </select>
                )}
              </Field>

              <Field label="Location">
                {({ id }) => (
                  <Input
                    id={id}
                    {...form.register("location_raw")}
                    placeholder="Berlin, Germany"
                  />
                )}
              </Field>

              <Field label="Employment type">
                {({ id }) => (
                  <Input
                    id={id}
                    {...form.register("employment_type")}
                    placeholder="Full-time"
                  />
                )}
              </Field>

              <Field label="Workplace type">
                {({ id }) => (
                  <Input
                    id={id}
                    {...form.register("workplace_type")}
                    placeholder="Hybrid"
                  />
                )}
              </Field>
            </div>

            <Field
              label="Description"
              required
              error={errors.description_text?.message}
              hint="The full posting text. Saving the same posting again will not replace it."
            >
              {({ id, required, describedBy }) => (
                <Textarea
                  id={id}
                  required={required}
                  {...form.register("description_text", { validate: present("Description") })}
                  rows={14}
                  placeholder="Paste the full posting text here."
                  className="font-mono text-xs"
                  aria-invalid={Boolean(errors.description_text)}
                  aria-describedby={describedBy}
                />
              )}
            </Field>

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? "Saving…" : "Save job"}
              </Button>
              <Button type="button" variant="outline" asChild>
                <Link href="/jobs">Cancel</Link>
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Saving the same posting twice returns the job you already have rather than a
              duplicate.
            </p>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}

/**
 * A labelled control.
 *
 * The id is generated here and handed down, because a `<Label>` that is merely
 * a SIBLING of its input names nothing: a screen reader announces an unnamed
 * text box, and clicking the label does not focus the field. The render prop is
 * what makes the association impossible to forget — there is no way to use this
 * component without receiving the id it expects you to apply.
 *
 * `required` reaches the control as well as the asterisk. An asterisk is a
 * sighted convention that assistive technology does not read, so on its own it
 * told some users a field was required and told everyone else nothing until the
 * submit had already failed. It is `aria-hidden` here precisely because
 * `required` on the control is now carrying that meaning properly.
 */
function Field({
  label,
  required,
  error,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  /** The field's validation message, shown under it and read with it. */
  error?: string;
  hint?: string;
  children: (props: {
    id: string;
    required: boolean;
    describedBy: string | undefined;
  }) => React.ReactNode;
}) {
  const id = React.useId();
  const errorId = error ? `${id}-error` : undefined;
  const hintId = hint ? `${id}-hint` : undefined;
  const describedBy = [errorId, hintId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>
        {label}
        {required ? (
          <span className="ml-1 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </Label>
      {children({ id, required: Boolean(required), describedBy })}
      {error ? (
        <p id={errorId} className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {hint ? (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** A required field's rule: something other than whitespace, or a sentence saying so. */
function present(label: string) {
  return (value: string) => value.trim() !== "" || `${label} is required.`;
}
