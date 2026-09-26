/**
 * Filling the fields a person approved — and nothing else.
 *
 * Runs in the content script, on the job site's page. Every instruction is
 * checked again here, against the page as it is NOW, because the page may have
 * changed since the popup read it and because this is the last line before a
 * value reaches a third party's form:
 *
 *   - the field must still be there, inside the application form (the
 *     adapter's `resolve`), and still carry the label the person approved;
 *   - it must not read as a sensitive question (src/forms/classify.ts), even
 *     if the popup somehow asked;
 *   - it must be a visible, enabled text field — never a choice, a checkbox, a
 *     file, a button;
 *   - it must be empty: what someone already typed is never overwritten;
 *   - the value must fit: nothing is cut to fit the page's limit.
 *
 * Setting a value goes through the element's native setter and then an `input`
 * and a `change` event, which is how a React form (Greenhouse's is one) learns
 * the value changed. Nothing is clicked, focused or submitted: the form's
 * submit button is not touched, no `submit` event is dispatched, and filling
 * never completes an application. The person reviews the page and submits it
 * themselves, or not at all.
 */

import { classifyField, isTextControl } from "./classify";
import { findFormAdapter } from "./registry";
import type { FillInstruction, FillReport } from "./types";

function codePoints(value: string): number {
  return Array.from(value).length;
}

function setNativeValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  if (setter) setter.call(element, value);
  else element.value = value;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

export function fillForm(document: Document, href: string, instructions: readonly FillInstruction[]): FillReport {
  const report: FillReport = { filled: [], skipped: [] };
  const adapter = findFormAdapter(href);
  const seen = new Set<string>();

  for (const instruction of instructions) {
    const skip = (reason: FillReport["skipped"][number]["reason"]) => report.skipped.push({ key: instruction.key, reason });
    // One value per field, whatever the list says.
    if (seen.has(instruction.key)) continue;
    seen.add(instruction.key);

    const element = adapter?.resolve(document, instruction.key) ?? null;
    const field = element ? adapter?.describe(element) : null;
    if (!element || !field) {
      skip("gone");
      continue;
    }
    if (field.label !== instruction.label) {
      skip("changed");
      continue;
    }
    if (classifyField(field).sensitive !== null) {
      skip("sensitive");
      continue;
    }
    if (!isTextControl(field.control) || !field.settable || element instanceof HTMLSelectElement) {
      skip("not_settable");
      continue;
    }
    if (field.filled) {
      skip("has_value");
      continue;
    }
    if (field.maxLength !== null && codePoints(instruction.value) > field.maxLength) {
      skip("too_long");
      continue;
    }
    setNativeValue(element, instruction.value);
    report.filled.push(instruction.key);
  }
  return report;
}
