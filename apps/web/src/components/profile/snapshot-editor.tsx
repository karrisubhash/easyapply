"use client";

import { DATE_PRECISIONS, EMPLOYMENT_TYPES, PROFICIENCIES, type Snapshot } from "@easyapply/shared";
import { Plus, Trash2 } from "lucide-react";
import * as React from "react";
import {
  get,
  useFieldArray,
  useForm,
  type FieldArrayPath,
  type FieldError,
  type UseFormRegister,
} from "react-hook-form";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import {
  EMPTY_CERTIFICATION,
  EMPTY_EDUCATION,
  EMPTY_EXPERIENCE,
  EMPTY_LINK,
  EMPTY_PROJECT,
  EMPTY_SKILL,
  projectLinksProblem,
  toFormValues,
  toSnapshotInput,
  type SnapshotFormValues,
} from "@/components/profile/snapshot-form-values";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { humanise } from "@/lib/utils";

interface Props {
  snapshot: Snapshot;
  onSave: (values: SnapshotFormValues) => void;
  isSaving: boolean;
  saveError: unknown;
  /** Rendered next to Save — the caller decides what happens after a save. */
  secondaryAction?: React.ReactNode;
}

/**
 * Edits a draft version's whole snapshot.
 *
 * The API replaces the snapshot wholesale, so the form holds the entire
 * document and one Save sends all of it. That is also why there is no
 * per-row save button: a half-saved snapshot is not a state the domain has.
 *
 * Client-side validation is limited to what the contract marks as required.
 * Cross-field rules — a date ordered before its start, a duplicated skill
 * name, a date without its precision — are the database's to enforce, and are
 * reported back through ApiErrorAlert rather than re-implemented here where
 * they could drift.
 *
 * Both kinds of failure are said where the user is. A required field left
 * empty used to block the save with no message at all — focus jumped to a
 * field that said nothing about why — so each one now carries its own sentence,
 * tied to the input by `aria-describedby`. A rejected save was reported only at
 * the top of a long form, above a Save button that sticks to the bottom of the
 * screen, so the bar says it too and focus moves to the explanation.
 */
export function SnapshotEditor({ snapshot, onSave, isSaving, saveError, secondaryAction }: Props) {
  const form = useForm<SnapshotFormValues>({ defaultValues: toFormValues(snapshot) });
  const { register, handleSubmit, formState } = form;
  const errorAt = (name: string) =>
    (get(formState.errors, name) as FieldError | undefined)?.message;
  const invalid = Object.keys(formState.errors).length > 0;
  // Every other rule is "required"; a project's links can also be malformed,
  // and "some required fields are empty" would send the user looking for a
  // blank field that is not there.
  const projectErrors: unknown = get(formState.errors, "projects");
  const badLinks =
    Array.isArray(projectErrors) &&
    projectErrors.some((item: { links?: FieldError } | undefined) => item?.links);

  // A NEW save error takes focus, so a keyboard or screen-reader user who
  // pressed Save at the bottom lands on the reason rather than on silence.
  const saveErrorRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (saveError) saveErrorRef.current?.focus();
  }, [saveError]);

  const links = useFieldArray({ control: form.control, name: "links" });
  const experiences = useFieldArray({ control: form.control, name: "experiences" });
  const education = useFieldArray({ control: form.control, name: "education" });
  const skills = useFieldArray({ control: form.control, name: "skills" });
  const projects = useFieldArray({ control: form.control, name: "projects" });
  const certifications = useFieldArray({ control: form.control, name: "certifications" });

  return (
    <form onSubmit={handleSubmit(onSave)} className="space-y-4" noValidate>
      {saveError ? (
        <div ref={saveErrorRef} tabIndex={-1} className="outline-none">
          <ApiErrorAlert error={saveError} />
        </div>
      ) : null}

      {/* Contact and summary are one-of-a-kind, so they get no add button. */}
      <Section title="Contact">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Full name">
            <Input {...register("contact.full_name")} />
          </Field>
          <Field label="Email">
            <Input type="email" {...register("contact.email")} />
          </Field>
          <Field label="Phone">
            <Input {...register("contact.phone")} />
          </Field>
          <Field label="Location">
            <Input {...register("contact.location")} placeholder="Bristol, United Kingdom" />
          </Field>
        </div>
      </Section>

      <Section title="Summary">
        <Field label="Summary" hint="The candidate's own words, as the resume wrote them" full>
          <Textarea rows={5} {...register("summary")} />
        </Field>
      </Section>

      <Section title="Links" onAdd={() => links.append(EMPTY_LINK)} addLabel="Add link">
        {links.fields.map((field, index) => (
          <Row key={field.id} onRemove={() => links.remove(index)} label={`Link ${index + 1}`}>
            <Field label="URL" required error={errorAt(`links.${index}.url`)}>
              <Input {...register(`links.${index}.url`, { required: `URL is required.` })} />
            </Field>
            <Field label="Label" hint="How the resume showed it">
              <Input {...register(`links.${index}.label`)} placeholder="GitHub" />
            </Field>
          </Row>
        ))}
      </Section>

      <Section
        title="Experience"
        onAdd={() => experiences.append(EMPTY_EXPERIENCE)}
        addLabel="Add experience"
      >
        {experiences.fields.map((field, index) => (
          <Row key={field.id} onRemove={() => experiences.remove(index)} label={`Experience ${index + 1}`}>
            <Field label="Company" required error={errorAt(`experiences.${index}.company`)}>
              <Input {...register(`experiences.${index}.company`, { required: `Company is required.` })} />
            </Field>
            <Field label="Job title" required error={errorAt(`experiences.${index}.job_title`)}>
              <Input {...register(`experiences.${index}.job_title`, { required: `Job title is required.` })} />
            </Field>
            <Field label="Employment type">
              <Select {...register(`experiences.${index}.employment_type`)}>
                <option value="">Not specified</option>
                {EMPLOYMENT_TYPES.map((value) => (
                  <option key={value} value={value}>
                    {humanise(value)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Location">
              <Input {...register(`experiences.${index}.location`)} />
            </Field>
            <DateField
              label="Start date"
              register={register}
              dateName={`experiences.${index}.start_date`}
              precisionName={`experiences.${index}.start_date_precision`}
            />
            <DateField
              label="End date"
              hint="Leave empty if this is your current role"
              register={register}
              dateName={`experiences.${index}.end_date`}
              precisionName={`experiences.${index}.end_date_precision`}
            />
            <Field label="Description" full>
              <Textarea {...register(`experiences.${index}.description`)} />
            </Field>
          </Row>
        ))}
      </Section>

      <Section title="Education" onAdd={() => education.append(EMPTY_EDUCATION)} addLabel="Add education">
        {education.fields.map((field, index) => (
          <Row key={field.id} onRemove={() => education.remove(index)} label={`Education ${index + 1}`}>
            <Field label="Institution" required error={errorAt(`education.${index}.institution`)}>
              <Input {...register(`education.${index}.institution`, { required: `Institution is required.` })} />
            </Field>
            <Field label="Degree">
              <Input {...register(`education.${index}.degree`)} />
            </Field>
            <Field label="Field of study">
              <Input {...register(`education.${index}.field_of_study`)} />
            </Field>
            <Field label="Grade">
              <Input {...register(`education.${index}.grade`)} placeholder="8.7 CGPA" />
            </Field>
            <DateField
              label="Start date"
              register={register}
              dateName={`education.${index}.start_date`}
              precisionName={`education.${index}.start_date_precision`}
            />
            <DateField
              label="End date"
              register={register}
              dateName={`education.${index}.end_date`}
              precisionName={`education.${index}.end_date_precision`}
            />
            <Field label="Description" full>
              <Textarea {...register(`education.${index}.description`)} />
            </Field>
          </Row>
        ))}
      </Section>

      <Section title="Skills" onAdd={() => skills.append(EMPTY_SKILL)} addLabel="Add skill">
        {skills.fields.map((field, index) => (
          <Row key={field.id} onRemove={() => skills.remove(index)} label={`Skill ${index + 1}`}>
            <Field label="Name" required error={errorAt(`skills.${index}.name`)}>
              <Input {...register(`skills.${index}.name`, { required: `Name is required.` })} />
            </Field>
            <Field label="Category">
              <Input {...register(`skills.${index}.category`)} placeholder="Backend" />
            </Field>
            <Field label="Proficiency">
              <Select {...register(`skills.${index}.proficiency`)}>
                <option value="">Not specified</option>
                {PROFICIENCIES.map((value) => (
                  <option key={value} value={value}>
                    {humanise(value)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Years of experience">
              <Input
                type="number"
                step="0.5"
                min="0"
                {...register(`skills.${index}.years_of_experience`)}
              />
            </Field>
          </Row>
        ))}
      </Section>

      <Section title="Projects" onAdd={() => projects.append(EMPTY_PROJECT)} addLabel="Add project">
        {projects.fields.map((field, index) => (
          <Row key={field.id} onRemove={() => projects.remove(index)} label={`Project ${index + 1}`}>
            <Field label="Name" required error={errorAt(`projects.${index}.name`)}>
              <Input {...register(`projects.${index}.name`, { required: `Name is required.` })} />
            </Field>
            <Field label="Role">
              <Input {...register(`projects.${index}.role`)} />
            </Field>
            <Field label="Technologies" hint="Comma separated" full>
              <Input {...register(`projects.${index}.technologies`)} placeholder="FastAPI, PostgreSQL" />
            </Field>
            <Field
              label="Project links"
              hint="One per line: a label, then the address"
              error={errorAt(`projects.${index}.links`)}
              full
            >
              <Textarea
                rows={2}
                {...register(`projects.${index}.links`, {
                  validate: (value) => projectLinksProblem(value) ?? true,
                })}
                placeholder="GitHub https://github.com/you/project"
              />
            </Field>
            <DateField
              label="Start date"
              register={register}
              dateName={`projects.${index}.start_date`}
              precisionName={`projects.${index}.start_date_precision`}
            />
            <DateField
              label="End date"
              register={register}
              dateName={`projects.${index}.end_date`}
              precisionName={`projects.${index}.end_date_precision`}
            />
            <Field label="Project URL" full>
              <Input {...register(`projects.${index}.project_url`)} />
            </Field>
            <Field label="Description" full>
              <Textarea {...register(`projects.${index}.description`)} />
            </Field>
          </Row>
        ))}
      </Section>

      <Section
        title="Certifications"
        onAdd={() => certifications.append(EMPTY_CERTIFICATION)}
        addLabel="Add certification"
      >
        {certifications.fields.map((field, index) => (
          <Row
            key={field.id}
            onRemove={() => certifications.remove(index)}
            label={`Certification ${index + 1}`}
          >
            <Field label="Name" required error={errorAt(`certifications.${index}.name`)}>
              <Input {...register(`certifications.${index}.name`, { required: `Name is required.` })} />
            </Field>
            <Field label="Issuing organization">
              <Input {...register(`certifications.${index}.issuing_organization`)} />
            </Field>
            <Field label="Issue date">
              <Input type="date" {...register(`certifications.${index}.issue_date`)} />
            </Field>
            <Field label="Expiration date" hint="Leave empty if it never expires">
              <Input type="date" {...register(`certifications.${index}.expiration_date`)} />
            </Field>
            <Field label="Credential ID">
              <Input {...register(`certifications.${index}.credential_id`)} />
            </Field>
            <Field label="Credential URL">
              <Input {...register(`certifications.${index}.credential_url`)} />
            </Field>
          </Row>
        ))}
      </Section>

      <div className="sticky bottom-0 flex items-center gap-2 border-t bg-background/95 py-4 backdrop-blur">
        <Button type="submit" disabled={isSaving}>
          {isSaving ? "Saving…" : "Save draft"}
        </Button>
        {secondaryAction}
        {saveError && !isSaving ? (
          <span className="text-xs text-destructive">
            Not saved. The reason is shown at the top of the form.
          </span>
        ) : invalid && !isSaving ? (
          <span className="text-xs text-destructive">
            {badLinks
              ? "Not saved. Some fields need fixing; each one says why."
              : "Not saved. Some required fields are empty."}
          </span>
        ) : formState.isDirty && !isSaving ? (
          <span className="text-xs text-muted-foreground">Unsaved changes</span>
        ) : null}
      </div>
    </form>
  );
}

function Section({
  title,
  onAdd,
  addLabel,
  children,
}: {
  title: string;
  /** Absent for a section holding one record rather than a list of them. */
  onAdd?: () => void;
  addLabel?: string;
  children: React.ReactNode;
}) {
  const repeating = onAdd !== undefined;
  const entries = React.Children.count(children);
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
        {repeating && (
          <Button type="button" variant="outline" size="sm" onClick={onAdd}>
            <Plus aria-hidden="true" className="size-4" />
            {addLabel}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        {repeating && entries === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing added yet.</p>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

function Row({
  label,
  onRemove,
  children,
}: {
  label: string;
  onRemove: () => void;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="relative rounded-md border p-4">
      <legend className="px-1 text-xs font-medium text-muted-foreground">{label}</legend>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={onRemove}
        aria-label={`Remove ${label}`}
        className="absolute right-2 top-2"
      >
        <Trash2 aria-hidden="true" className="size-4" />
      </Button>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

/**
 * A labelled control, with its hint and its error tied to it.
 *
 * The asterisk is `aria-hidden` because `aria-required` on the control carries
 * that meaning for assistive technology; the hint and the error are linked by
 * `aria-describedby`, so they are read with the field rather than having to be
 * found near it.
 */
function Field({
  label,
  hint,
  required,
  error,
  full,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  /** The field's validation message, when it has one. */
  error?: string;
  full?: boolean;
  children: React.ReactNode;
}) {
  const id = React.useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [errorId, hintId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={full ? "sm:col-span-2" : undefined}>
      <Label htmlFor={id}>
        {label}
        {required && <span aria-hidden="true"> *</span>}
      </Label>
      <div className="mt-1">
        {React.isValidElement(children)
          ? React.cloneElement(
              children as React.ReactElement<{
                id?: string;
                "aria-describedby"?: string;
                "aria-invalid"?: boolean;
                "aria-required"?: boolean;
              }>,
              {
                id,
                "aria-describedby": describedBy,
                "aria-invalid": error ? true : undefined,
                "aria-required": required ? true : undefined,
              },
            )
          : children}
      </div>
      {error ? (
        <p id={errorId} className="mt-1 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}

/**
 * A date and the precision it was actually known to.
 *
 * The precision selector is visible rather than defaulted behind the scenes:
 * the whole reason the column exists is that "May 2026" does not say which day,
 * and a hidden default would quietly reintroduce the invented precision the
 * schema was designed to prevent.
 */
function DateField({
  label,
  hint,
  register,
  dateName,
  precisionName,
}: {
  label: string;
  hint?: string;
  register: UseFormRegister<SnapshotFormValues>;
  dateName: FieldArrayPath<SnapshotFormValues> | string;
  precisionName: FieldArrayPath<SnapshotFormValues> | string;
}) {
  const id = React.useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <div className="mt-1 flex gap-2">
        <Input
          id={id}
          aria-describedby={hintId}
          type="date"
          /* eslint-disable-next-line @typescript-eslint/no-explicit-any */
          {...register(dateName as any)}
        />
        <Select
          aria-label={`${label} precision`}
          className="w-28"
          /* eslint-disable-next-line @typescript-eslint/no-explicit-any */
          {...register(precisionName as any)}
        >
          {DATE_PRECISIONS.map((value) => (
            <option key={value} value={value}>
              {humanise(value)}
            </option>
          ))}
        </Select>
      </div>
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}
