import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { confirmedVersion, draftVersion, profile } from "./fixtures";
import { job, jobDetail, jobRequirementsResult } from "./job-fixtures";
import { matchDetail, matchSummary } from "./match-fixtures";
import { processingResult, resumeDetail, resumeDocument } from "./resume-fixtures";
import { tailoringRun, tailoringRunDetail } from "./tailoring-fixtures";
import { attentionList, operatorDocumentDetail } from "./operations-fixtures";

const BASE = "/api/backend";

/** The happy path. Individual tests override what they need with server.use(). */
export const handlers = [
  http.get(`${BASE}/profile`, () => HttpResponse.json(profile)),
  http.post(`${BASE}/profile`, () => HttpResponse.json(profile, { status: 201 })),
  // A confirmed version AND a draft on top of it — the ordinary state of an
  // account that has used the product once. A list containing only a draft
  // contradicted `/profile/versions/current` returning a confirmed one, and any
  // screen that reads both would have seen a user who cannot exist.
  http.get(`${BASE}/profile/versions`, () =>
    HttpResponse.json([
      confirmedVersion({ id: 102, version_number: 1, is_current: true }),
      draftVersion({ id: 103, version_number: 2 }),
    ]),
  ),
  http.post(`${BASE}/profile/versions`, () => HttpResponse.json(draftVersion(), { status: 201 })),
  http.get(`${BASE}/profile/versions/current`, () => HttpResponse.json(confirmedVersion())),
  http.get(`${BASE}/profile/versions/:n`, () => HttpResponse.json(draftVersion())),
  http.put(`${BASE}/profile/versions/:n/snapshot`, () => HttpResponse.json(draftVersion())),
  http.post(`${BASE}/profile/versions/:n/confirm`, () => HttpResponse.json(confirmedVersion())),

  http.get(`${BASE}/resumes`, () => HttpResponse.json([resumeDocument()])),
  http.post(`${BASE}/resumes`, () => HttpResponse.json(resumeDocument(), { status: 201 })),
  http.get(`${BASE}/resumes/:id`, () => HttpResponse.json(resumeDetail())),
  http.get(`${BASE}/resumes/:id/result`, () => HttpResponse.json(processingResult())),
  http.post(`${BASE}/resumes/:id/reprocess`, () => HttpResponse.json(resumeDocument())),

  // Internal operations. Registered against the proxy path like everything
  // else — the Next route handler never runs under vitest.
  http.get(`${BASE}/internal/resume-processing/attention`, () =>
    HttpResponse.json(attentionList()),
  ),
  http.get(`${BASE}/internal/resume-processing/documents/:id`, () =>
    HttpResponse.json(operatorDocumentDetail()),
  ),

  http.get(`${BASE}/jobs`, () => HttpResponse.json([job()])),
  http.get(`${BASE}/jobs/:id`, () => HttpResponse.json(jobDetail())),
  http.get(`${BASE}/jobs/:id/requirements`, () => HttpResponse.json(jobRequirementsResult())),
  http.post(`${BASE}/jobs/:id/reprocess`, () => HttpResponse.json(jobDetail())),

  // Matching. The POST answers 201 for a new evaluation; a test that is about
  // the repeat case overrides this with a 200, the way the server does.
  http.get(`${BASE}/jobs/:id/matches`, () => HttpResponse.json([matchSummary()])),
  http.post(`${BASE}/jobs/:id/matches`, () => HttpResponse.json(matchDetail(), { status: 201 })),
  http.get(`${BASE}/matches/:id`, () => HttpResponse.json(matchDetail())),

  // Tailoring. The POST answers 201 for a new run; a test about the
  // already-in-flight case overrides this with a 200, the way the server does.
  http.get(`${BASE}/tailored-resumes`, () => HttpResponse.json([tailoringRun()])),
  http.get(`${BASE}/jobs/:id/tailored-resumes`, () => HttpResponse.json([tailoringRun()])),
  http.post(`${BASE}/jobs/:id/tailored-resumes`, () =>
    HttpResponse.json(tailoringRunDetail(), { status: 201 }),
  ),
  http.get(`${BASE}/tailored-resumes/:id`, () => HttpResponse.json(tailoringRunDetail())),
  // The artifact is not JSON. A DOCX is a zip, so the stub answers bytes with
  // the headers the real route sets — anything that parsed this as JSON would
  // be testing a path the browser never takes.
  http.get(`${BASE}/tailored-resumes/:id/artifact`, () =>
    HttpResponse.arrayBuffer(new TextEncoder().encode("PK stub").buffer, {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition":
          'attachment; filename="Acme-Corp-Senior-Backend-Engineer-v1.docx"',
      },
    }),
  ),

  http.post(`${BASE}/jobs`, () => HttpResponse.json(jobDetail(), { status: 201 })),
];

export const server = setupServer(...handlers);

/** The API's own error body shape — see services/api/app/api/errors.py. */
export function apiError(status: number, detail: string, code: string) {
  return HttpResponse.json({ detail, code }, { status });
}

/** FastAPI's request-validation body, which has a different shape. */
export function validationError(loc: (string | number)[], msg: string) {
  return HttpResponse.json({ detail: [{ loc, msg, type: "value_error" }] }, { status: 422 });
}
