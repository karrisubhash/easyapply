/**
 * Lever's application form, on its own page: jobs.lever.co/<company>/<uuid>/apply.
 *
 * What the live form looks like (2026-09-26; the structure is in
 * test/fixtures/lever-apply.html) and how it differs from Greenhouse's — the
 * reason this is an adapter of its own and not a variation of that one:
 *
 *   - Controls are identified by NAME (`name`, `email`, `urls[LinkedIn]`,
 *     `cards[<uuid>][field0]`), not by id. Keys here are `name:<name>`.
 *   - A question is an `li.application-question` whose `.application-label`
 *     holds the question (in a `.text` child for custom questions), with the
 *     required mark in `span.required`. The `<label>` wraps the whole block,
 *     field included, so its own text is not the question.
 *   - Choices are NATIVE: radio groups and checkbox groups sharing a name, and
 *     `<select>`s. Each group is reported once. None is ever set.
 *   - Lever adds hidden inputs (tokens, templates, captcha); they are not
 *     questions and are not reported.
 *   - The submit is a `type="button"` wired by Lever's script. Nothing here
 *     touches it.
 *
 * Read-only: whether a field holds something, never what.
 */

import { cleanLabel } from "../../extraction/text";
import { isTextControl } from "../../forms/classify";
import type { ControlKind, FieldDescriptor, FormAdapter, FormDetection, FormSection } from "../../forms/types";
import { isVisible } from "../../forms/visibility";
import { isLeverUrl, postingId, postingOfApplication, SITE_NAME } from "./site";

type Control = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

const FORM = "form#application-form";
const KEY_PREFIX = "name:";

function formOf(document: Document): HTMLFormElement | null {
  return document.querySelector<HTMLFormElement>(FORM);
}

function isControl(element: Element): element is Control {
  return element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement;
}

function isQuestionControl(element: Control): boolean {
  if (!element.name) return false;
  if (element instanceof HTMLInputElement && ["hidden", "submit", "button", "reset", "image"].includes(element.type.toLowerCase())) {
    return false;
  }
  return true;
}

/** Every visible-kind control of the form that shares this one's name. */
function group(element: Control): Control[] {
  const form = element.form;
  if (!form) return [element];
  return [...form.elements].filter((other): other is Control => isControl(other) && isQuestionControl(other) && other.name === element.name);
}

function controlKind(element: Control, members: Control[]): ControlKind {
  if (element instanceof HTMLTextAreaElement) return "textarea";
  if (element instanceof HTMLSelectElement) return "select";
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
    case "radio":
      return "radio_group";
    case "checkbox":
      return members.length > 1 ? "checkbox_group" : "checkbox";
    default:
      return "other";
  }
}

/** The question's words: `.application-label` (its `.text` child when it has one), without the required mark. */
function labelFor(element: Control): string {
  const question = element.closest("li.application-question");
  const label = question?.querySelector(".application-label .text") ?? question?.querySelector(".application-label");
  if (label) {
    const clone = label.cloneNode(true) as Element;
    clone.querySelectorAll(".required").forEach((mark) => mark.remove());
    const words = cleanLabel(clone.textContent);
    if (words) return words;
  }
  return cleanLabel(element.getAttribute("aria-label") ?? element.getAttribute("placeholder"));
}

function sectionOf(element: Control): FormSection {
  // Lever's equal-opportunity survey posts as eeo[...]; its own container when present.
  if (/^eeo\[/i.test(element.name) || element.closest(".eeo-section, [data-qa='eeo']")) return "demographic";
  if (/^cards\[/.test(element.name)) return "questions";
  return "core";
}

function isFilled(members: Control[], control: ControlKind): boolean {
  const first = members[0];
  if (!first) return false;
  if (control === "radio_group" || control === "checkbox_group" || control === "checkbox") {
    return members.some((member) => (member as HTMLInputElement).checked);
  }
  if (control === "file") return ((first as HTMLInputElement).files?.length ?? 0) > 0;
  return first.value.trim().length > 0;
}

function describe(element: Control): FieldDescriptor | null {
  if (!isQuestionControl(element)) return null;
  const members = group(element);
  const control = controlKind(element, members);
  const question = element.closest("li.application-question");
  return {
    key: `${KEY_PREFIX}${element.name}`,
    control,
    label: labelFor(element),
    required: members.some((member) => member.required) || !!question?.querySelector(".application-label .required"),
    section: sectionOf(element),
    autocomplete: element.getAttribute("autocomplete") || null,
    filled: isFilled(members, control),
    settable:
      isTextControl(control) && members.length === 1 && !element.disabled && !(element as HTMLInputElement).readOnly && isVisible(element),
    maxLength: "maxLength" in element && element.maxLength > 0 ? element.maxLength : null,
  };
}

export const leverApplication: FormAdapter = {
  id: "lever-application",
  siteName: SITE_NAME,

  handles: isLeverUrl,

  detect(document, url): FormDetection {
    if (!isLeverUrl(url)) return { status: "unsupported", message: "This is not a Lever job page." };
    if (!postingOfApplication(url)) {
      return {
        status: "no_form",
        site: SITE_NAME,
        message:
          postingId(url) !== null
            ? "On Lever the application form is on its own page. Press Apply on the posting to open it."
            : "This is a Lever board, not an application form.",
      };
    }
    const form = formOf(document);
    if (!form) {
      return { status: "no_form", site: SITE_NAME, message: "No application form was found on this page. If it is still loading, try again." };
    }
    const seen = new Set<string>();
    const fields: FieldDescriptor[] = [];
    for (const element of form.elements) {
      if (!isControl(element) || !isQuestionControl(element) || seen.has(element.name)) continue;
      seen.add(element.name);
      const field = describe(element);
      if (field) fields.push(field);
    }
    return { status: "detected", site: SITE_NAME, fields };
  },

  resolve(document, key) {
    if (!key.startsWith(KEY_PREFIX)) return null;
    const name = key.slice(KEY_PREFIX.length);
    const form = formOf(document);
    // Only the form's own controls: `form.elements` holds nothing from elsewhere on the page.
    const found = form ? [...form.elements].find((element): element is Control => isControl(element) && isQuestionControl(element) && element.name === name) : undefined;
    return found ?? null;
  },

  describe,
};
