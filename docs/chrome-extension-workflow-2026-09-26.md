# Chrome extension V2 — the authenticated browser workflow (2026-09-26)

Engineering report for the phase that took `apps/extension` from job capture
to the whole browser workflow: posting → save → requirements → match →
tailored resume → application form prepared → **the person submits**. Design
reference: [chrome-extension.md](chrome-extension.md) §9–§18. Every number
below was measured in this session on this machine; nothing is extrapolated.

## 1. Executive summary

- **Built:** requirements, matching, tailoring and application-form
  preparation in the extension popup, for Greenhouse (posting and on-page form)
  and — as the test of the adapter interfaces — Lever (posting and its separate
  `/apply` page). All through the existing web proxy and API; **no backend,
  schema, migration or generated-contract change**.
- **Never submits.** No code path clicks, focuses or submits; the page-context
  bundle's only DOM events are the filler's `input`/`change`
  (`dist/content.js` scanned); the browser suites count submit events (0) and
  record every request to the job sites (all GET).
- **Sensitive questions are never answered**: classified first, refused again
  at fill time, listed as "Review required".
- **Backend truth only.** Verdicts, counts, coverage, evidence, run stages and
  grounding are rendered as stored; the real-stack suite compares the popup
  with what the API holds.
- **Evidence:** extension 442 unit tests (from 175), 14 real-Chromium tests
  against a stub and the same 14 against the real stack (FastAPI + PostgreSQL
  + a production build of the web app in `dev-session`, two accounts); web 501
  unit tests (from 474) and its 16 e2e tests; backend 2676 passed, 0 failed;
  eight deliberate mutations of the safety checks, all caught.
- **One authentication-adjacent change**, justified in
  [chrome-extension.md](chrome-extension.md) §9.2: the proxy's extension rule
  admits two more exact write shapes (match, start tailoring).
- **Not production-ready**, for reasons outside this phase: no real
  authentication (Entra not built), Greenhouse/Lever markup is an external
  dependency, and choices/files remain manual by design (§24, §25).

## 2. Repository audit findings (before any change)

| Area | Finding | Consequence |
|---|---|---|
| Extension V1 | Popup (review) → worker (network, popup-only sender check) → proxy; read-only content script; per-tab save memory carrying the account | Extended, not rewritten; the account-aware memory kept |
| Proxy admission | `isAllowedExtensionWrite`: `POST /jobs` only; GETs pass on the Host check | Matching and tailoring writes were refused → §9.2 |
| API | Requirements, profile versions, matches (create-or-return), tailored runs (open-or-join), artifact route all existed | No backend change needed |
| API errors | 409 is one `conflict` code for "not structured" and "no confirmed version" | The extension reads state instead of parsing messages |
| Jobs | Structuring runs inline in `POST /jobs`, but runs can be queued/processing/failed (reprocess, AI) | Classify the run; bounded polling |
| Tailoring | In-process thread queue; `select_generator` records `deterministic` when AI is off | "AI disabled" is shown from the run's generator |
| Web UX | Never shows `error_message` (can quote the description or the prompt); matching is an explicit action; newest match for the selected version | Same rules in the extension; wording shared (§9.3 of the design doc) |
| Profile | Snapshot has contact, links (personal), experiences, education, skills, projects, certifications — **no** work authorization, sponsorship, EEOC, salary | Those questions can never be answered from the profile |
| Live Greenhouse forms (GitLab, Discord) | Labelled text inputs with `autocomplete`; every choice a react-select combobox; hidden file inputs in labelled groups; EEOC block | Fixture reproduces it; choices never set |
| Live Lever form (Palantir) | Separate `/apply` page; controls keyed by `name`; `.application-label`; native radio/checkbox/select; `type="button"` submit | Second adapter; interface held (§9) |
| Baseline | backend 2676/13 skipped/0 failed; extension 175; web 474; tsc clean | Recorded before any edit; tree snapshot and backend hashes taken |

## 3. Architecture implemented

```
content script (page)          popup (extension page)                     worker                    web app                 API
─────────────────────          ──────────────────────                     ──────                    ───────                 ───
extract ─────────────────────▶ review → save ─────────────────────────▶ POST /jobs ────────────▶ admission ─▶ identity ─▶ jobs
detect-form (no values) ─────▶ job panel ◀── requirements/profile/ ◀─── GET  (closed call list) ─▶ resolveIdentity()      matching
                               match/tailoring (state machine)  ───────▶ POST matches|tailored ─▶ fresh upstream headers tailoring
fill-form (approved only) ◀─── application panel (mapping, review)
```

The backend stays authoritative; the extension stores only V1's per-tab save
record.

## 4. Extension architecture

`src/adapters/{greenhouse,lever}/{site,job,application}.ts` (site knowledge,
nothing else knows markup) · `src/extraction/` (posting registry, text) ·
`src/forms/` (types, classify, registry, fill, visibility) · `src/profile/mapping.ts` ·
`src/api/` (calls, client, guards, links, jobs, session) ·
`src/workflow/` (job-flow state machine, poll) · `src/jobs/requirements.ts`,
`src/matching/present.ts` (arrangement only) · `src/popup/` (app, job-panel,
job-view, application-panel, describe, dom, port) · `src/background/`
(service, diagnostics) · `src/content/` (handler, index). Detail:
[chrome-extension.md](chrome-extension.md) §9.1.

## 5. Authentication integration

Unchanged seam. Every extension request carries the browser's session cookie
for the web app (`credentials: "include"`) and nothing else that names a
person; `resolveIdentity()` decides who it is for; the proxy builds upstream
headers fresh. The popup shows the account ("Signed in to EasyApply as …",
"Saving as …"). A proxy 401 `session_required` anywhere switches the panel to
"Your EasyApply session has ended" with a sign-in link and stops acting. No
token, key or credential in the extension; no `cookies` permission.

## 6. Job workflow

After save (201/200) or on reopening a remembered save of the same account:
`GET /jobs/<id>/requirements`, classified into ready / processing (polled,
7 re-reads, ~1 min) / still processing (Check again) / failed (shared help by
code; retry on the web page) / unavailable / error. Ready shows required and
preferred items with classification, detail and verified-or-not citation, plus
"a later attempt failed / is running" when `run.id ≠ job.latest_run.id`.

## 7. Matching workflow

Current confirmed version from `GET /profile/versions` (states for no profile,
no versions, drafts only, none current). On opening: newest stored match for
this job **and** version (read-only). **Match my profile** re-reads the
profile, then `POST /jobs/<id>/matches {profile_version_id}`; a version that
changed meanwhile is shown instead of acted on. Rendered: stored counts,
coverage, `has_unmet_required`, each verdict, explanation, evidence with tier
and note, version number, `as_of_date`, `algorithm_version`, and a link to
the full page. Nothing computed.

## 8. Tailoring workflow

**Tailor my resume** → `POST /jobs/<id>/tailored-resumes` (with the match on
screen when it is this version's) → followed with 12 re-reads over ~3 min
through Queued / Writing / Checking against your profile / Preparing the file.
Ready only when completed + grounding passed + artifact; "Not approved — no
file" when grounding failed; "file isn't available" when passed without one;
failure help by code, **Try again** only when `can_retry`. Download is a link
to the proxy's artifact route (the extension never fetches the bytes). The
generator is the server's; with AI off the popup says the rule-based writer
wrote it. Real stack: both runs `completed`, `deterministic`, grounding
`passed`, artifact present (queried in `easyapply_ext_e2e`).

## 9. Application detection architecture

`FormAdapter { handles, detect, resolve, describe }`; one registry. Greenhouse:
`form#application-form` beside the posting, keys `id:<id>`, labels from
`<label for>` (files: their upload group), react-select comboboxes, EEOC
section. Lever: `/apply` page, keys `name:<name>`, labels from
`.application-label`, native radio/checkbox groups (reported once) and
selects, hidden tokens excluded. Adding Lever needed **no interface change**;
one additive extraction result, `application_page`, lets a form-only page say
so and still be prepared. Detection reports kind, label, required, section,
autocomplete, filled (never the value), settable, maxlength.

## 10. Profile mapping architecture

Current confirmed snapshot, read only on **Prepare answers**, used once, not
kept. Confidence `exact` (verbatim into a certain field; ticked), `derived`
(a transformation that can be wrong; ticked only where it cannot plausibly
misfire — a two-word name split), `none` (with the reason and what to do).
Personal links only, host-exact; ambiguity is refused, not resolved. Table:
[chrome-extension.md](chrome-extension.md) §13.2.

## 11. Sensitive-field policy

Classified before identity; never filled; re-checked in the page at fill time.
Categories: work authorization, sponsorship, visa/citizenship, demographics
(and everything in a demographic section), veteran, disability/accommodations,
criminal history, clearance, relocation, pay, notice/start date, years of
experience, legal attestations/consent, age, prior employment with the
company, location eligibility. Deliberately broad. Tested against 37 real
Greenhouse/Lever question wordings plus look-alikes ("Email address of your
immigration lawyer").

## 12. Security model

- Admission first (Host; same-origin or the extension's three writes),
  identity second (`resolveIdentity`), upstream headers built fresh.
- Closed call list with integer ids (`isApiCall`); bounded content messages;
  shape-checked answers; worker answers only its popup, content script only
  its extension; form keys resolve only inside the application form.
- Page text is data (`textContent`/`value` only); no action is taken on page
  content; the matcher and tailor are deterministic server-side.
- Proven in real Chromium on the real stack: a claimed `X-Dev-User-Email`,
  `Authorization` or `X-User-Id` from the extension is ignored; the other
  account's job, requirements, matches, match, run and artifact are 404
  through the web app and from the extension itself; a malformed cookie is no
  session; a session ending mid-flow sends nothing further.

## 13. Data flow

Posting fields (reviewed, exact request shown) → `POST /jobs`. Reads: job
requirements, profile version list, current snapshot (only on request),
matches, runs. Writes: match, start tailoring. To the page: approved values
only. Diagnostics: route template, status, timing, ids — never bodies,
emails or profile values (worker and proxy both tested).

## 14. State machine

`JobFlow` — requirements / profile / match / tailoring / session, keyed by
account + tab + job, match and tailoring also by profile version; stale
answers dropped, version change resets and aborts version-scoped polls.
Application panel: found → loading → review → filling → filled | error.
Diagram: [chrome-extension.md](chrome-extension.md) §14.

## 15. API contracts used / changed

Used (generated types from `@easyapply/shared`): `JobIngestionRequest`,
`JobDetail`, `JobRequirementsResult`, `ProfileVersionSummary`,
`ProfileVersion`, `MatchRequest`, `Match`, `MatchDetail`, `TailoringRequest`,
`TailoringRun`, `TailoringRunDetail`, `WebSession`. **Changed: none.**
`openapi.json` and `api-schema.ts` are byte-identical to the baseline. Added
to the shared package: `presentation.ts` (wording both clients use — not a
contract) and its re-export.

## 16. Database changes

None. Migration head unchanged (`d4a7c2e9b815`). The real-stack harness
creates `easyapply_ext_e2e` from the existing migrations.

## 17. Files changed

47 added, 38 modified, 1 moved (`src/extraction/adapters/greenhouse.ts` →
`src/adapters/greenhouse/job.ts`). Outside `apps/extension`:
`apps/web/src/server/request-guard.ts` (admission), `…/[...path]/route.ts`
(log line), `…/route.test.ts`, five web components/lib files (read shared
wording), `apps/web/eslint.config.mjs` (ignore the new build dir),
`packages/shared/src/{index,presentation}.ts`, `docs/{authentication,
chrome-extension}.md`, this report, `README.md`, `.gitignore`. Line endings
match each file's baseline; no control bytes (scripted check).
`services/api` is byte-identical (hash comparison over app, alembic,
evaluation).

## 18. Tests added

| File | Covers |
|---|---|
| `client.test.ts`, `calls.test.ts` | Transport outcomes and failure taxonomy; the closed call list (unknown ops, extra keys, bad ids, identity fields refused); paths and bodies |
| `poll.test.ts` | Stops when final; bounded; reports progress; abort; policy budgets |
| `job-flow.test.ts` | Every classification; stale answers (account, tab, job, version) dropped; version change resets; session expiry |
| `background-api.test.ts` | Popup-only; malformed calls refused unsent; in-flight join; diagnostics without PII; bounded |
| `workflow.test.ts` | Save → requirements (processing, still processing, failed without message) → match (explicit, exact verdicts, surprising verdicts, unknown category, no profile/draft/no current, version changed mid-flow, session expired) → tailoring (stages, not approved, failed by code, budget, double press) → injection as text → two accounts |
| `classify.test.ts`, `greenhouse-application.test.ts`, `mapping.test.ts`, `fill.test.ts`, `application.test.ts`, `lever.test.ts` | Sensitive categories, identities, anchoring; detection on live-structure fixtures; proposals and confidence; every fill guard; never submits; malformed fill messages; only approved values reach the page; Lever posting, form and apply-page flow |
| `e2e/workflow.spec.ts` | Real Chromium: acceptance flow; two accounts; claimed identity; session expiry; malformed cookie; injection; Lever |
| web `route.test.ts` (+27) | The two new routes admitted; 24 neighbours refused; default-off; page-caused refused; claimed identity replaced; 401 unforwarded; the log line |

## 19. Full test results (final, sequential, quiet machine)

| Suite | Result |
|---|---|
| Backend `pytest` | 2676 passed, 13 skipped, 0 failed |
| Web `tsc` / `vitest` / `eslint` | clean / 501 passed / 0 errors, 6 warnings (pre-existing, none in changed files) |
| Web e2e (`npm run test:e2e`, real stack) | 16 passed |
| Shared `tsc` | clean |
| Extension `tsc` / `vitest` / lint (typescript-eslint recommended) / build | clean / 442 passed / 0 problems / built, secret scan clean |
| Extension browser, stub | 14 passed |
| Extension browser, real stack (fresh production build) | 14 passed |
| Mutations of safety checks | 8 of 8 caught |

**Two intermittent failures observed and not hidden.** (1) Backend
`test_the_look_ahead_adds_no_per_line_cost` failed once while a whole-app
ESLint ran beside it; it compares two wall-clock timings, passed 5/5 alone and
in the final full run, and the backend code is byte-identical to the baseline.
Pre-existing and load-sensitive. (2) The first popup-flow test of three files
(V1's `flow.test.ts` included) timed out at Vitest's 5 s default under load
(measured 5.1–5.9 s with a second suite running; ~0.6 s idle). Fixed at the
cause as far as possible (the module graph is imported at collection) and
given a documented 15 s budget; 6 of 6 double-loaded runs then passed.

## 20. Browser E2E results

Real stack (API :8300, web :3300 `dev-session`, DB `easyapply_ext_e2e`):
steps 1–21 of the acceptance flow pass in one test; the popup's requirements
equal the API's stored candidate; the rendered verdicts equal the stored
`requirement_results`; the counts line equals the stored numbers; the
tailored resume is `completed`/`deterministic`/`passed` with a `.docx`
(`PK`) downloadable with the browser's session and viewable in the web app;
the form gets exactly the six approved values; radios, checkboxes, comboboxes
and sensitive text fields stay empty; submit events 0; job-site requests all
GET. The proxy logged every extension request; the API logged 201s for the
workflow writes and 404s for the cross-account ones.

## 21. Multi-user isolation results

Two accounts in one browser (dev-session): the second never sees the first's
saved state, panel, match or resume; saving the same posting creates their
own job row (DB: jobs 5 and 6, same external id, different users); their
proposals come from their own profile; the first's job, requirements, matches,
match, run and artifact are 404 via the web app; the extension's own GET and
POST attempts on them are 404/422; a claimed identity header changes nothing.
Unit-level: stale answers across accounts, tabs, jobs and versions dropped.

## 22. Performance considerations

Bounded, backing-off polling (≤ 7 and ≤ 12 re-reads), aborted on version
change or popup close; identical calls joined in the worker; 20 s request
timeout; the content script reads only on request (no observers, no
scraping); proxy-side latency per extension request in the log (tens of ms on
this machine).

## 23. Privacy considerations

Descriptors, not values, leave the page; the profile snapshot is fetched only
on request, used once, not stored; only approved values reach the page; no
`error_message` is shown; diagnostics carry no personal data (tested); the
proxy log carries no identity or body (tested); nothing new in
`chrome.storage`.

## 24. Known limitations

Two sites only; choices and files are the person's; on a Lever `/apply` page
no tailored-resume link (no posting-identity lookup); a job saved elsewhere
shows the review first (Save answers "Already in EasyApply"); retry of a
failed reading is on the web page; Greenhouse/Lever templates are external
dependencies; only `jobs.lever.co` is claimed; the extension id is pinned by a
public key (to remove before a Web Store release). Details:
[chrome-extension.md](chrome-extension.md) §18.

## 25. Remaining production blockers

1. **Real authentication** (Entra External ID) — `dev-session` is not
   authentication; the API believes `X-Dev-User-Email` outside production.
2. **Store distribution**: own extension id, `PROXY_ALLOWED_EXTENSION_IDS` for
   it, an https web origin fixed at build time.
3. **Adapter maintenance**: a monitored check of the live Greenhouse and Lever
   templates, since a markup change fails capture/detection (explicitly, not
   silently) until the adapter is updated.
4. Pre-existing and out of this phase: tailoring AI not released (evaluation
   gate), per-stage tailoring commits.

## 26. Recommended next phase

Entra sign-in through the existing seam (authentication.md §7), then a
read-only "is this posting saved?" API (by source and external id) so every
page — including Lever `/apply` — can show its job, match and resume without a
save; then, only with evidence from real use, a third adapter.
