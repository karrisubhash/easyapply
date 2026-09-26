/**
 * What the content script answers, as a function of the page — so the tests'
 * fake Chrome can run exactly what the real content script runs.
 *
 *   extract       the job posting (src/extraction/registry.ts)
 *   detect-form   the application form's fields: labels, kinds, required,
 *                 empty or not — never a value (src/forms/registry.ts)
 *   fill-form     the approved values, into empty text fields only, checked
 *                 again against the page as it is now (src/forms/fill.ts)
 *
 * Anything else, or anything malformed, is not answered.
 */

import { extractJob } from "../extraction/registry";
import { fillForm } from "../forms/fill";
import { detectForm } from "../forms/registry";
import type { FillReport, FormDetection } from "../forms/types";
import type { ExtractionResult } from "../extraction/types";
import { DETECT_FORM, EXTRACT, FILL_FORM, isContentRequest } from "../shared/messages";

export function answerContentMessage(
  message: unknown,
  document: Document,
  href: string,
): ExtractionResult | FormDetection | FillReport | undefined {
  if (!isContentRequest(message)) return undefined;
  switch (message.type) {
    case EXTRACT:
      return extractJob(document, href);
    case DETECT_FORM:
      return detectForm(document, href);
    case FILL_FORM:
      return fillForm(document, href, message.instructions);
  }
}
