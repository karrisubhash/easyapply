/**
 * What reading an application form can produce, and what filling it may do.
 *
 * The content script reads the form into `FieldDescriptor`s — what each field
 * IS (its control, its label, whether it is required, whether it already holds
 * something) and never what it CONTAINS: a value someone typed into a job
 * site's form is theirs, and nothing here reads it. The popup maps descriptors
 * to profile values (src/profile/mapping.ts), shows the person every proposal,
 * and sends back only the values they approved, as `FillInstruction`s. The
 * content script then fills those — text fields only, empty ones only — and
 * reports what it did. It never submits, never clicks, never touches anything
 * it was not told to.
 */

export type ControlKind =
  | "text"
  | "email"
  | "tel"
  | "url"
  | "number"
  | "date"
  | "textarea"
  /** A native <select>. */
  | "select"
  /** An ARIA combobox (Greenhouse's react-select): its options are not in the page until opened. */
  | "combobox"
  | "radio_group"
  | "checkbox"
  | "checkbox_group"
  | "file"
  | "other";

/** Where on the form a field sits, as the adapter can tell. */
export type FormSection = "core" | "questions" | "demographic";

export interface FieldDescriptor {
  /** The adapter's handle for finding this field again at fill time. Opaque to everything else. */
  key: string;
  control: ControlKind;
  /** The field's label as the person sees it, whitespace collapsed, required-star dropped. */
  label: string;
  required: boolean;
  section: FormSection;
  /** The page's own `autocomplete` token, a hint the site gives about the field. */
  autocomplete: string | null;
  /** Whether it already holds something. The value itself is never read. */
  filled: boolean;
  /** Whether the filler could set it at all: a visible, enabled text control. */
  settable: boolean;
  /** The page's own length limit, when it states one. */
  maxLength: number | null;
}

export type FormDetection =
  | { status: "detected"; site: string; fields: FieldDescriptor[] }
  /** A supported site, but no application form on this page. */
  | { status: "no_form"; site: string; message: string }
  /** No form adapter claims this page. */
  | { status: "unsupported"; message: string };

/** One value the person approved, for one field. */
export interface FillInstruction {
  key: string;
  value: string;
  /** The label the person saw when approving it: if the field now says something else, it is not filled. */
  label: string;
}

export type FillSkipReason =
  /** The field is no longer on the page. */
  | "gone"
  /** Its label changed since the person approved it. */
  | "changed"
  /** Not a control the filler sets (a choice, a file, a checkbox…). */
  | "not_settable"
  /** It holds something already; the filler never overwrites. */
  | "has_value"
  /** Its label reads as a question EasyApply never answers for the person. */
  | "sensitive"
  /** Longer than the field allows; nothing is cut to fit. */
  | "too_long";

export interface FillReport {
  filled: string[];
  skipped: { key: string; reason: FillSkipReason }[];
}

/**
 * One site's application form.
 *
 * `detect` only reads. `resolve` finds a field again by the key `detect` gave
 * it — at fill time, when the page may have changed — or says it is gone.
 * `describe` reads one element the way `detect` read it, so the filler can
 * check a field is still what the person approved.
 */
export interface FormAdapter {
  readonly id: string;
  readonly siteName: string;
  handles(url: URL): boolean;
  detect(document: Document, url: URL): FormDetection;
  resolve(document: Document, key: string): HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null;
  describe(element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): FieldDescriptor | null;
}
