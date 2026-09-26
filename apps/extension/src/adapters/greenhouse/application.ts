/**
 * The application form Greenhouse renders beside a posting, on the same page
 * (job-boards.greenhouse.io/<board>/jobs/<id>, `form#application-form`).
 *
 * What the live forms look like (GitLab and Discord postings, 2026-09-26; the
 * structure is in test/fixtures/greenhouse-application.html):
 *
 *   - Text questions are `<input>`/`<textarea>` with an id, a `<label for>`
 *     (its required star in a span) and an `aria-label` repeating it. First
 *     name, last name and email carry `autocomplete` tokens.
 *   - EVERY choice question — work authorization, sponsorship, location,
 *     country, the demographic survey — is a react-select combobox: an
 *     `input[role=combobox]` whose options are not in the page until someone
 *     opens it. This adapter reports them and never touches them.
 *   - Resume and cover letter are visually hidden `input[type=file]`s inside a
 *     `role="group"` block whose own label names them; the `<label for>` on
 *     the input just says "Attach".
 *   - The Voluntary Self-Identification survey sits in `.eeoc__container`.
 *
 * Read-only, like the posting adapter: `detect` reads labels and whether a
 * field is empty, never what anyone typed into it.
 */

import { cleanLabel } from "../../extraction/text";
import { isTextControl } from "../../forms/classify";
import type { ControlKind, FieldDescriptor, FormAdapter, FormDetection, FormSection } from "../../forms/types";
import { isVisible } from "../../forms/visibility";
import { isGreenhouseUrl, postingId, SITE_NAME } from "./site";

type Control = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

const FORM = "form#application-form";
const KEY_PREFIX = "id:";

/** Inputs that are not questions: react-select's hidden validation mirror, buttons, hidden values. */
function isQuestionControl(element: Control): boolean {
  if (!element.id) return false;
  if (element instanceof HTMLInputElement) {
    const type = element.type.toLowerCase();
    if (["hidden", "submit", "button", "reset", "image"].includes(type)) return false;
  }
  return true;
}

function controlKind(element: Control): ControlKind {
  if (element instanceof HTMLTextAreaElement) return "textarea";
  if (element instanceof HTMLSelectElement) return "select";
  if (element.getAttribute("role") === "combobox") return "combobox";
  switch (element.type.toLowerCase()) {
    case "text":
    case "search":
      return "text";
    case "email":
      return "email";
    case "tel":
      return "tel";
    case "url":
      return "url";
    case "number":
      return "number";
    case "date":
      return "date";
    case "file":
      return "file";
    case "checkbox":
      return "checkbox";
    case "radio":
      return "radio_group";
    default:
      return "other";
  }
}

function textOf(document: Document, ids: string | null): string {
  if (!ids) return "";
  return cleanLabel(
    ids
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent ?? "")
      .join(" "),
  );
}

/** A label's words without its required star. */
function labelWords(node: Element | null): string {
  if (!node) return "";
  const clone = node.cloneNode(true) as Element;
  clone.querySelectorAll('[aria-hidden="true"], .required').forEach((star) => star.remove());
  return cleanLabel(clone.textContent).replace(/\s*\*$/, "");
}

function labelFor(element: Control): string {
  const document = element.ownerDocument;
  // A file input's name is its upload block's label; its own <label> says "Attach".
  if (element instanceof HTMLInputElement && element.type === "file") {
    const group = element.closest('[role="group"][aria-labelledby]');
    const named = group ? labelWords(document.getElementById(group.getAttribute("aria-labelledby") ?? "")) : "";
    if (named) return named;
  }
  const fromLabel = labelWords(element.labels?.[0] ?? null);
  if (fromLabel) return fromLabel;
  const labelledBy = textOf(document, element.getAttribute("aria-labelledby"));
  if (labelledBy) return labelledBy.replace(/\s*\*$/, "");
  return cleanLabel(element.getAttribute("aria-label"));
}

function sectionOf(element: Control): FormSection {
  if (element.closest(".eeoc__container, #demographic_questions, .demographic-questions")) return "demographic";
  if (/^question_\d+$/.test(element.id)) return "questions";
  return "core";
}

function isFilled(element: Control, control: ControlKind): boolean {
  if (control === "combobox") {
    // react-select shows a chosen value beside the input, not in it.
    return !!element.closest(".select__value-container")?.querySelector(".select__single-value, .select__multi-value");
  }
  if (element instanceof HTMLInputElement && element.type === "file") return (element.files?.length ?? 0) > 0;
  if (element instanceof HTMLInputElement && (element.type === "checkbox" || element.type === "radio")) return element.checked;
  return element.value.trim().length > 0;
}

function describe(element: Control): FieldDescriptor | null {
  if (!isQuestionControl(element)) return null;
  const control = controlKind(element);
  const maxLength = "maxLength" in element && element.maxLength > 0 ? element.maxLength : null;
  return {
    key: `${KEY_PREFIX}${element.id}`,
    control,
    label: labelFor(element),
    required:
      element.getAttribute("aria-required") === "true" ||
      element.required ||
      element.closest('[aria-required="true"]') !== null,
    section: sectionOf(element),
    autocomplete: element.getAttribute("autocomplete") || null,
    filled: isFilled(element, control),
    settable: isTextControl(control) && !element.disabled && !(element as HTMLInputElement).readOnly && isVisible(element),
    maxLength,
  };
}

export const greenhouseApplication: FormAdapter = {
  id: "greenhouse-application",
  siteName: SITE_NAME,

  handles(url) {
    return isGreenhouseUrl(url);
  },

  detect(document, url): FormDetection {
    if (!isGreenhouseUrl(url)) return { status: "unsupported", message: "This is not a Greenhouse job board page." };
    if (postingId(url) === null) {
      return { status: "no_form", site: SITE_NAME, message: "This is a Greenhouse board, not a single posting, so there is no application form here." };
    }
    const form = document.querySelector<HTMLFormElement>(FORM);
    if (!form) {
      return {
        status: "no_form",
        site: SITE_NAME,
        message: "No application form was found on this page. On Greenhouse it sits below the job description; if the page is still loading, try again.",
      };
    }
    const fields = [...form.querySelectorAll<Control>("input, textarea, select")]
      .map(describe)
      .filter((field): field is FieldDescriptor => field !== null);
    return { status: "detected", site: SITE_NAME, fields };
  },

  resolve(document, key) {
    if (!key.startsWith(KEY_PREFIX)) return null;
    const element = document.getElementById(key.slice(KEY_PREFIX.length));
    // Only a control inside the application form: a key cannot reach anything else on the page.
    if (!element || !element.closest(FORM)) return null;
    return element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement
      ? element
      : null;
  },

  describe,
};
