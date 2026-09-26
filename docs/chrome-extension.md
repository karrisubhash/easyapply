# Chrome extension — job capture (V1) and the browser workflow (V2)

`apps/extension`, in two slices.

- **V1 (§1–§8):** read one job posting from the page the person is looking at,
  show them exactly what will be saved, and save it through the ingestion API
  that already exists (`POST /api/v1/jobs`, see
  [job-intelligence.md](job-intelligence.md)).
- **V2 (§9–§18, 2026-09-26):** everything after the save, in the same popup:
  the job's structured requirements, the person's match against them, a
  tailored resume, and the application form Greenhouse renders on the same page
  — proposed answers from the confirmed profile, reviewed field by field,
  filled only where approved. **It never submits an application.** And a
  second site, Lever — posting and application page — added once the adapter
  interfaces were in place, as the test of whether they are sufficient (§13.5).

Loading and using it: [apps/extension/README.md](../apps/extension/README.md).

Unchanged by either slice: the matching algorithm, tailoring (prompt,
grounding, reconciliation, rendering), resume intelligence, the API's job
pipeline, ownership rules and the identity seam. Outside `apps/extension`, V1
added one opt-in rule to the web app's proxy (§3); V2 widened that rule by two
routes (§9.2), added one log line to the proxy, and moved the web app's status
and verdict wording into `@easyapply/shared` so both clients use one copy
(§9.3). No API schema, migration or backend file changed.

```
Greenhouse posting (third-party page)
   │  content script: reads the DOM when asked; never writes, never networks
   │  src/content/index.ts → src/extraction/registry.ts → adapters/greenhouse.ts
   ▼
popup — the review screen: editable fields, the exact request body
   │  src/popup/app.ts, src/review/payload.ts          (nothing sent until Save)
   ▼
service worker — one request per posting; remembers the result per tab
   │  src/background/service.ts, src/api/jobs.ts
   │  POST http://localhost:3000/api/backend/jobs
   │       Origin: chrome-extension://jjhciekcmhmngciccfgignmmlgjknakk
   │       Sec-Fetch-Site: none        (no identity, no cookie, no credential)
   ▼
web app proxy — Host check, the extension rule, attaches identity
   │  apps/web/src/app/api/backend/[...path]/route.ts
   │  POST http://127.0.0.1:8000/api/v1/jobs   X-Dev-User-Email: <server's DEV_USER_EMAIL>
   ▼
API — validate → normalize → deduplicate (201 new / 200 existing, unchanged) → structure
```

## 1. Parts and why each exists

| Part | Does | Does not |
|---|---|---|
| Content script (`src/content/index.ts`) | Answers the popup's `easyapply/extract` message with `extractJob(document, location.href)`. Only for messages from its own extension id. | Run anything at page load, write to the DOM, submit or fill a form, touch the network, ask for a save. |
| Adapters (`src/extraction/`) | Turn one site's DOM into an `ExtractionResult`: `extracted`, `not_job_page`, `failed` (naming the missing fields) or `unsupported`. Pure; never throws (the registry turns a throw into `failed`). | Guess. An unrecognised layout is a failure with a reason, not a best effort. |
| Popup (`src/popup/`) | Resolves the tab, asks for the reading, shows it for review, rebuilds the payload on every keystroke, shows the exact JSON body, sends on Save, reports the outcome. | Send anything on open. Render page text as markup. |
| Service worker (`src/background/`) | Sends the POST, joins a duplicate click to the request in flight, keeps the successful outcome per tab in `chrome.storage.session`. | Accept a save from anything but the popup page. |

The save runs in the worker rather than the popup because a popup is destroyed
the moment it loses focus: clicking back into the page mid-save would abort the
request and leave the person not knowing whether it landed. The worker finishes
it and the reopened popup shows the result.

## 2. Supported site: Greenhouse-hosted boards

`job-boards.greenhouse.io` and `job-boards.eu.greenhouse.io`. Chosen because
the posting is server-rendered (in the HTML before any script runs), public (no
sign-in, so nothing about the person's own account is on the page), carries the
posting's id in its URL, and every company shares one template — so one adapter
is honestly "Greenhouse", not "the companies it was tried on".

Checked against live pages on 2026-09-26 before the adapter was written: GitLab,
Figma, Discord and Cloudflare postings share `.job__title h1`, `.job__location`,
`.job__description` (which contains the pay-range block and excludes the
application form), the title `Job Application for <title> at <company>` and the
logo alt `<company> Logo`. The legacy `boards.greenhouse.io/<board>/jobs/<id>`
301-redirects to `job-boards.greenhouse.io`. A closed or unknown posting
redirects to `/<board>?error=true`, which the adapter reports as closed. The
built extension was then run against live GitLab and Figma postings in
Chromium and read every field correctly (the GitLab description: 10,975
characters).

| Field | Source | Required |
|---|---|---|
| `job_title` | `.job__title h1`, whitespace collapsed | yes |
| `description_text` | `.job__description`, as structured text (§4) | yes |
| `company_name` | document title, else logo alt text | no — warned |
| `location_raw` | `.job__location` (the SVG icon is skipped) | no — warned |
| `external_job_id` | `/<board>/jobs/(\d+)` | — (always present on a posting URL) |
| `source_url` | `location.href` without the fragment | — |
| `source_type` | `"greenhouse"` | — |

Not supported: company career sites that embed a Greenhouse board in an iframe
(`careers.example.com/?gh_jid=…`, e.g. Airbnb, Databricks). The top-level page
is the company's; reading a cross-origin frame is a different adapter.
Employment type, workplace type and salary are not extracted as fields — the pay
range stays in the description text, where the structurer can see it.

## 3. Identity: the extension sends none, the web app attaches it

> **Superseded in part, 2026-09-26:** the extension now sends the browser's
> session cookie for the web app (`credentials: "include"`), so it saves as
> whoever the browser is signed in as, and the review screen names that
> account. The open question at the end of this section is answered in
> [authentication.md](authentication.md). The rest of this section — why the
> proxy and not the API, the proxy's extension rule, the pinned id — stands.

**The decision.** The extension posts to the web app's proxy
(`/api/backend/jobs`), not to the API. The proxy is where identity is attached
server-side for the web app already, and the extension attaches nothing — no
user id (the schema has no such field and `extra="forbid"` refuses one), no
email, no `X-Dev-User-Email`. (Originally no cookie either, `credentials:
"omit"`; it now presents the browser's session cookie — see the note above.)
There is nothing in its requests a page, another extension or a tampered build
could change to act as someone else.

**Why not the API directly.** Two findings on this machine, 2026-09-26:

1. The API's own fallback identity is `DEV_USER_EMAIL=dev@easyapply.local` (its
   default; `services/api/.env` does not set it), while `apps/web/.env.local`
   sets `DEV_USER_EMAIL=newuser@easyapply.local`. An extension calling the API
   without a header would have saved jobs into an account the web app does not
   show — "Open in EasyApply" would 404.
2. The API's `BrowserRequestGuard` (`services/api/app/main.py`) refuses a write
   whose `Origin` is not the API's own host, so a direct call would have needed
   an API change anyway — to the component every other defence leans on.

**What Chrome sends.** Measured, not assumed: a `fetch` from an MV3 service
worker with host permission for the target, in Playwright's Chromium
(`chromium-1243`), carries `Origin: chrome-extension://<id>` and
`Sec-Fetch-Site: none`. The browser suite asserts the same on every run
(`e2e/capture.spec.ts`), so a Chrome change to either would fail it.

**The proxy rule** (`isAllowedExtensionWrite` in `route.ts`). A state-changing
request that is not same-origin is let through only if all of these hold:

- it is `POST /jobs` — nothing else. Confirming a profile, uploading a resume,
  reprocessing, matching and `/internal/*` stay same-origin only; *(V2 widened
  this to `POST /jobs/<id>/matches` and `POST /jobs/<id>/tailored-resumes` as
  well — §9.2 — and nothing else);*
- `Origin` is exactly `chrome-extension://<32 letters a–p>`, and that id is in
  `PROXY_ALLOWED_EXTENSION_IDS` (comma-separated; **empty by default**, so no
  extension can write until a developer names one);
- `Sec-Fetch-Site` is `none` or absent. A request a web page causes is
  `same-origin`, `same-site` or `cross-site`, never `none`.

The Host check still runs first. Page script cannot forge `Origin`, and a
content script's requests carry the page's origin, not the extension's — which
is also why the content script never makes the request. Something that is not a
browser can send any headers, exactly as it could already send a same-origin
`Origin`: the proxy was never an authorization boundary, and this rule does not
make it one.

**A pinned id.** `src/manifest.json` carries a public `key`, which makes the
unpacked extension's id `jjhciekcmhmngciccfgignmmlgjknakk` on every machine, so
the one line of configuration can be documented exactly. The private key was
never kept: loading unpacked does not need it. `test/manifest.test.ts` derives
the id from the key and fails if they drift apart.

**Real sign-in (Entra External ID, 2026-09-26).** The extension presents the
web app's session under real sign-in exactly as under the development modes —
it holds no token and stores none. What it learned: signed in is
`authenticated` alone (an Entra account may have no email; the label is email,
else name, else "your EasyApply account"); the session's account tag is sent as
`X-EasyApply-Session` on every call and keys its in-flight call joining and its
memory of saves; a 409 `session_changed` clears everything of the previous
account and offers Reload; and before Fill it asks the web app who is signed in
and fills only for the same account. See [authentication.md](authentication.md)
§1 and §6.

## 4. From page to payload

**Text.** `blockText` (`src/extraction/text.ts`) walks the DOM rather than
reading `innerText`, which depends on layout (jsdom has none; a background tab
can differ). It writes the shape the job structurer already reads: headings on
their own line, a blank line between paragraphs, `- ` or `1. ` before each list
item (the `_BULLET_PREFIX` of `services/api/app/services/job/structuring.py`),
nested items indented. Scripts, styles, SVG, forms, buttons and hidden elements
are skipped. One-line fields go through `cleanLabel`, the same rule as the API's
`_clean_label`, so the review screen shows the value the API will store.

**Review.** `buildPayload` (`src/review/payload.ts`) is the only way a draft
becomes a `JobIngestionRequest`, and the popup's "Exact request" panel prints
its result — the body sent is `JSON.stringify` of that same object. The identity
fields (`source_type`, `source_url`, `external_job_id`) are read-only: they are
what the API deduplicates on. Nothing is truncated — an over-long field is an
error and Save waits, matching the API's own refusal to cut a description.
Lengths are counted in code points, as Pydantic counts them, not UTF-16 units.
An empty title blocks; an empty description warns (the API accepts it, but
matching would have nothing to read).

**Contract.** Types come from `@easyapply/shared` (generated from the API's
OpenAPI), so a renamed field is a compile error. The limits a type cannot carry
are pinned by `test/contract.test.ts` against `packages/shared/openapi.json`,
and the URL limit against `_MAX_URL_LENGTH` in `services/api/app/schemas/job.py`.

## 5. Duplicates

The API is the guard. Every Greenhouse payload carries `external_job_id`, so it
lands in the API's tier-1 identity, `(user, source_type, external_job_id)`: the
same posting reached through any link — tracking parameters included — answers
**200 with the job already saved, unchanged**. The popup reports that as
"Already in EasyApply", distinct from "Saved", and lists any reviewed field the
stored job does not have (the first-saved version wins, so an edit made now was
not applied).

The extension only stops itself from asking twice: the Save button is disabled
from the click until the answer; the worker joins a second request for the same
posting to the one in flight; and a successful save is remembered per tab in
`chrome.storage.session`, so reopening the popup on that posting shows "Saved"
with no button to press.

## 6. Security posture

- **Posting text is data.** Everything from the page reaches the popup through
  `textContent` or an input's `value`; nothing uses `innerHTML`. A flow test
  feeds a title containing `<img onerror=…>` and asserts no element is created
  and nothing runs.
- **Only the popup can cause a save.** The worker checks the sender is this
  extension's `popup.html`; a content script (living inside a third-party page)
  is refused.
- **Nothing is submitted to the job site.** The browser suite records every
  request to Greenhouse and asserts all are `GET`, and that the page's HTML is
  byte-identical before and after capture.
- **Permissions:** `storage`, plus host access to the two Greenhouse hosts
  (and, since V2, `jobs.lever.co`) and the one web-app origin the build was
  made for. No `tabs`, `scripting`,
  `cookies` or all-sites access; no `web_accessible_resources` or
  `externally_connectable`. Asserted by `test/manifest.test.ts`.
- **Requests:** `credentials: "include"` — the browser's session cookie for
  the web app, never read by the extension (authentication.md) — `redirect: "error"` (a redirect would be
  somebody else answering), a 30-second timeout whose message says a retry is
  safe because the API is idempotent.

## 7. Tests

| Suite | What | Count |
|---|---|---|
| `apps/extension` Vitest (`npm run test --workspace @easyapply/extension`) | text reader; Greenhouse adapter against a fixture whose markup is copied from live pages with invented words (expected description written by hand from the markup before the reader ran); registry; payload rules; the OpenAPI contract; the API client's every outcome; the worker's sender check, in-flight join and memory; outcome wording; manifest; the content-script entry; and the capture → review → save flow through the real worker and popup entry points against a Chrome fake and a fake web app that deduplicates like the API | 146 |
| `apps/extension` Playwright (`npm run test:browser …`) | the built extension in Chromium: content script injected on Greenhouse and not elsewhere, review, edit, save, the wire request (origin, `Sec-Fetch-Site`, no identity, body = preview), "Open in EasyApply", reopen, same posting from a second tab, board page, unreadable page | 5 |
| `apps/web` route tests | the proxy's extension rule: refused by default, allowed only for the named id, only `POST /jobs`, only `Sec-Fetch-Site: none`, look-alike origins refused, Host check first | 21 new (47 in the file) |

The browser suite can also run against a real web app and API
(`EXTENSION_E2E_WEB_URL`); see the README. It was run that way on 2026-09-26 —
API on :8300 against the `easyapply_e2e` database, an "owner" web server on
:3300 with the extension allowed, a "stranger" one on :3301 without — and passed
5/5, with these checked directly afterwards:

- the owner's account held the two saved jobs; the stranger's server listed
  none of them and answered 404 for the owner's job id;
- the database held one row per posting, although one was saved twice;
- the stranger's server (no `PROXY_ALLOWED_EXTENSION_IDS`) refused an
  extension-shaped `POST /jobs` with 403 `cross_site_request`; the owner's
  server refused the same headers on `POST /profile`, and on `POST /jobs` with
  `Sec-Fetch-Site: cross-site`;
- a request that sent its own `X-Dev-User-Email` naming the stranger was saved
  to the owner: the proxy replaces the header rather than forwarding it.

## 8. Known limitations

- **One site.** Greenhouse-hosted boards only; embedded Greenhouse boards on
  company domains, LinkedIn, Indeed, Lever and Workday are reported as
  unsupported.
- **The Greenhouse template is an external dependency.** If Greenhouse changes
  the class names, capture fails explicitly ("Couldn't read this job") rather
  than saving something wrong — but it fails until the adapter is updated.
- **Company name is inferred** from the document title or the logo's alt text;
  the page has no element that is just the company. Both were present on every
  posting checked; a board with neither saves no company (with a warning).
- **Job ids are assumed unique across Greenhouse boards.** Tier-1 identity is
  `("greenhouse", <id>)`, not including the board. Greenhouse ids appear to be
  global, but that is not verified; if two boards ever shared one, the second
  posting would be reported "Already in EasyApply" as the first.
- **Edits are lost if the popup closes before Save.** Only a successful save is
  remembered.
- **Identity is the web app's session** (§3): development, `dev-session`, or real
  sign-in with Entra External ID (verified against a local provider only), and
  the extension saves as whichever account the browser presents
  ([authentication.md](authentication.md)).
- **The web app must be running**, at the address fixed at build time.
- **A Greenhouse tab opened before install or reload** has no content script
  until it is reloaded; the popup says so.
- **Chrome only**, 120+. Other Chromium browsers are untested. The manifest's
  `key` must be removed before publishing to the Chrome Web Store, which assigns
  its own id — and that id then has to be the one the proxy allows.
- **No icons** — Chrome shows its default letter icon.
- **No committed lint configuration** for the extension; it was checked once
  with the typescript-eslint recommended rules (clean) and is type-checked with
  `strict` and `noUncheckedIndexedAccess`.

---

# V2 — the browser workflow (2026-09-26)

```
Greenhouse posting + its application form (one page)
   │  content script: reads when asked; writes ONLY approved values into empty text fields
   │  src/content/handler.ts → extraction/registry.ts | forms/registry.ts | forms/fill.ts
   ▼
popup — review → save → the job in EasyApply → the application form
   │  src/popup/app.ts          capture and review (V1)
   │  src/popup/job-panel.ts    requirements, match, tailored resume  (state: src/workflow/job-flow.ts)
   │  src/popup/application-panel.ts   proposals, review, fill        (mapping: src/profile/mapping.ts)
   ▼
service worker — one closed list of calls (src/api/calls.ts), joined when identical, logged without PII
   │  GET  /api/backend/jobs/:id/requirements       GET  /api/backend/profile/versions[/current]
   │  GET  /api/backend/jobs/:id/matches            GET  /api/backend/matches/:id
   │  GET  /api/backend/jobs/:id/tailored-resumes   GET  /api/backend/tailored-resumes/:id
   │  POST /api/backend/jobs/:id/matches            POST /api/backend/jobs/:id/tailored-resumes
   ▼
web app proxy — Host, admission (§9.2), resolveIdentity(), fresh upstream headers
   ▼
API — the existing job, matching and tailoring services, unchanged
```

## 9. What changed, and where

### 9.1 Parts

| Part | Does | Does not |
|---|---|---|
| `src/api/calls.ts` | The closed list of backend calls the popup may ask the worker for, as data (`{op: "createMatch", jobId, profileVersionId}`). `isApiCall` refuses an unknown op, an extra key, or anything that is not a positive safe integer; `prepare` builds the one path and body from the checked ids. | Take a path, a header or a URL from the popup. No call carries identity: the bodies are the API's own `MatchRequest`/`TailoringRequest` (`extra="forbid"`). |
| `src/api/client.ts`, `guards.ts` | One transport (`credentials: "include"`, `redirect: "error"`, a timeout) and a failure taxonomy the popup can act on: `session_required`, `not_found`, `conflict`, `refused`, `api_unreachable`, `network`, `timeout`, `unexpected_response`. Shallow run-time shape checks of every answer, over the generated types. | Parse an error sentence to decide anything. The API's 409 means several things under one code; the popup finds out which by reading state (§10, §11). |
| `src/workflow/job-flow.ts` | The explicit state machine for one saved job (§14). Pure. | Compute anything the backend decided. |
| `src/workflow/poll.ts` | Bounded, backing-off, cancellable re-reads. | Poll forever, or call a run finished because it stopped looking. |
| `src/popup/job-panel.ts`, `job-view.ts` | Load, poll, act (match, tailor), render. | Match or tailor on opening: both are explicit actions, as on the web app. |
| `src/jobs/requirements.ts`, `src/matching/present.ts` | Arrange the API's requirement lists and match results for display: required/preferred as the API splits them, the web app's category order, the shared labels. | Merge, drop, re-score or re-label a verdict. A category or status this build does not know is shown under the backend's own word. |
| `src/adapters/greenhouse/` | `site.ts` (hosts, match patterns, the posting path — shared), `job.ts` (the V1 posting adapter, moved here unchanged), `application.ts` (the application form). | Duplicate a Greenhouse rule between the two adapters. |
| `src/forms/` | `types.ts` (descriptors, instructions, reports), `classify.ts` (sensitive first, then which profile fact), `registry.ts` (the one entry point), `fill.ts` (the filler). | Read a value a person typed; set anything but an empty, visible text field; click, focus or submit. |
| `src/profile/mapping.ts` | Descriptors + the current confirmed snapshot → proposals with confidence and a reason. | Keep the profile, define a second profile schema, or infer a fact the profile does not state. |
| `src/background/diagnostics.ts` | One structured line per request (§16). | Log a body, an email, a requirement or a profile value. |

### 9.2 The proxy rule: why it had to widen, and by exactly how much

This is a change to authentication-adjacent code, so it is recorded the way
the brief asks for such changes.

1. **What was wrong.** Matching (`POST /jobs/<id>/matches`) and tailoring
   (`POST /jobs/<id>/tailored-resumes`) are writes. The proxy lets a
   cross-origin write through only by the extension rule, and that rule was
   `POST /jobs` and nothing else (§3). Every read the extension needs is a GET,
   which admission has always passed on the Host check alone — but the two
   actions the workflow exists for were refused with 403
   `cross_site_request`.
2. **Why the design could not support it otherwise.** The alternatives were
   worse: calling the API directly (refused by the API's own
   `BrowserRequestGuard`, and it would attach no identity — §3), a token of the
   extension's own (a second credential and identity path —
   authentication.md §1), or opening the web app in a tab to press the button
   there (not a workflow).
3. **The minimal change.** `isAllowedExtensionWrite`
   (`apps/web/src/server/request-guard.ts`) now admits exactly three shapes —
   `POST /jobs`, `POST /jobs/<id>/matches`, `POST /jobs/<id>/tailored-resumes`
   — where `<id>` is `^[1-9]\d{0,17}$` (no sign, no leading zero, no encoded
   slash), with every other condition unchanged: `Origin` exactly
   `chrome-extension://<id>` for an id in `PROXY_ALLOWED_EXTENSION_IDS` (still
   empty by default), `Sec-Fetch-Site` `none` or absent, the Host check first.
   Each admitted write creates a record in the caller's own account from things
   already in it; none edits, confirms or deletes anything. Identity is
   untouched: `resolveIdentity()` decides who every request is for, exactly as
   before, and the API resolves `<id>` inside that account (a foreign id is a
   404).
4. **What protects it.** `route.test.ts`: both new routes admitted for the
   named extension with the server's identity and the body intact; refused by
   default and when `Sec-Fetch-Site` says a page caused it; 24 neighbouring
   writes refused (`reprocess`, `confirm`, `make-current`, `/profile`,
   `/resumes`, `jobs/07/…`, `jobs/0/…`, `jobs/-7/…`, `jobs/7x/…`,
   `jobs/7%2F…`, an extra segment, `/matches/7`, `/tailored-resumes/7[/artifact]`,
   `PUT` on any of them); in `dev-session`, a claimed `X-Dev-User-Email` or
   `Authorization` on these routes is replaced by the session's person, and
   no session is a 401 that is never forwarded.

### 9.3 One copy of the words

The web app never shows a run's `error_message`: a structuring error can quote
the job description and a tailoring provider error can quote the prompt, which
holds the person's career history. It maps `error_code` to sentences instead.
The extension must say the same things for the same reasons, so those tables —
job and tailoring stage labels, failure help, "failed while …" phrases, verdict
labels, evidence-tier labels, match category order and labels — moved verbatim
into `packages/shared/src/presentation.ts`, and the web components now import
them. The web's rendered output is unchanged (its page tests pass unmodified).
No generated contract changed: `openapi.json` and `api-schema.ts` are
byte-identical.

## 10. Job intelligence

After a save (201 or 200) — or on reopening a tab whose save is remembered for
the same account — the popup reads `GET /jobs/<id>/requirements` and classifies
what the backend said, never assuming the answer:

| Backend says | Popup shows |
|---|---|
| a stored reading (`requirements` present) | "Requirements are ready." Required and preferred lists, each item with its classification (skill, technology, experience, education, certification, qualification), the structurer's detail (a skill's category, "5+ years", a degree level), and its citation — section and quote — flagged when the server could not verify the quote. Responsibilities are listed separately as "not requirements". If a newer attempt is running or failed, it says these are from the last completed reading (a `run.id` different from `job.latest_run.id`). |
| nothing stored, newest run `queued`/`processing` | "Job saved. EasyApply is extracting requirements." Re-read on `REQUIREMENTS_POLL` (7 re-reads, 1 s → 20 s, about a minute). |
| still running when the budget is spent | "EasyApply is still reading this job." with **Check again** and a link to the job. Not "failed", not "done". |
| nothing stored, newest run `failed` | "Processing failed while …", the shared help for its `error_code`, and whether a retry is possible (from `can_retry`) — retry itself is on the job's web page. The `error_message` is never shown. |
| no run at all | "EasyApply has no requirements for this job yet." |
| an error | The failure category in words (§15), and **Try again**. |

Structuring runs inline in the save today, so the common case is ready on the
first read; the polling covers AI structuring and a reprocess seen mid-run.

## 11. Matching

- **Which profile version.** The current confirmed one, from
  `GET /profile/versions` (`is_current` and `status == "confirmed"` — both are
  checked, so a draft is never matched even if one were flagged). Each other
  case is its own state with a link to fix it: no profile (404), no versions,
  only drafts ("Confirm your profile to match it"), confirmed versions but none
  current.
- **What is shown on opening.** The newest stored match for *this job and this
  version* (`GET /jobs/<id>/matches`, then `GET /matches/<id>`) — the rule the
  web match page uses. A stored match for another version is never shown as
  this one. No match is created by opening the popup.
- **Matching is an explicit action.** **Match my profile** sends
  `POST /jobs/<id>/matches {profile_version_id}`; the API answers 201 (new) or
  200 (the stored evaluation for the same inputs). Before sending, the popup
  reads the profile again: if another tab made a different version current in
  the meantime, it shows that version's state and does not act.
- **What is shown.** Exactly the stored result: the counts per axis
  ("1 of 3 matched · 1 not found · 1 uncertain"), the stored coverage as a
  percentage (the web app's rounding), `has_unmet_required`, and per
  requirement the verdict (Matched / Not found / Uncertain — the shared
  labels), the explanation, and each piece of evidence with its tier, note and
  quote (evidence that fell short is shown, dashed). The version number,
  `as_of_date` and `algorithm_version` are printed with it, and a link opens
  the full match page. Nothing is computed: no score, no alias, no vocabulary,
  no geography, no re-reading of a verdict.

## 12. Tailoring

- **Tailor my resume** sends `POST /jobs/<id>/tailored-resumes` with the
  current version and the match on screen (if it is that version's). The API
  opens a run (201) or returns the one in flight (200) — a double press joins
  the same run (also joined in the worker).
- The popup follows the run with `TAILORING_POLL` (12 re-reads, 1.5 s → 30 s,
  about three minutes): Queued → Writing → Checking against your profile
  (grounding) → Preparing the file. `is_stalled` adds "taking longer than
  usual". Past the budget: "Still working on your resume" and **Check again**.
- **Ready** only when `completed`, grounding `passed` and `has_artifact`: then
  "Open in EasyApply" (`/tailored-resumes/<id>`) and "Download (.docx)" — a
  link to the proxy's artifact route, which the browser downloads with the
  web app's own session; the extension never fetches the bytes. Completed with
  grounding failed is "Not approved — no file" (no download offered). Passed
  without a file is "This resume's file isn't available". Failed shows
  "Generation failed while …" and the shared help for its code, never its
  message, and **Try again** only when `can_retry`.
- **AI policy.** The generator is the server's choice (`select_generator`),
  recorded on the run. With AI off — the production setting — the popup says
  the rule-based writer wrote it and that AI tailoring isn't enabled for the
  account. The extension has no way to ask for the AI generator and holds no
  provider credential.

## 13. Application forms

### 13.1 Detection (Greenhouse)

Greenhouse renders the application form on the posting's own page
(`form#application-form`). Its structure was read from live GitLab and Discord
postings on 2026-09-26 and reproduced in
`test/fixtures/greenhouse-application.html` (invented words, generated by a
script so every field repeats the live markup):

- text questions: `<input>`/`<textarea>` with `<label for>` (required star in
  a span) and `aria-label`; name and email carry `autocomplete` tokens;
- **every choice question is a react-select combobox** —
  `input[role=combobox]`, options not in the page until opened — including
  work authorization, sponsorship, location, relocation and the whole
  demographic survey;
- resume and cover letter: visually hidden `input[type=file]` inside a
  `role="group"` block that names them (their `<label for>` says "Attach");
- the Voluntary Self-Identification survey in `.eeoc__container`.

`detect` returns a descriptor per field — key, control kind, label, required,
section, `autocomplete`, whether it is filled (never what it holds),
whether it is settable (a visible, enabled text control), `maxlength`. No
live board checked had native checkboxes or radio groups on the form, so the
fixture has none; they are classified by standard HTML semantics and never
filled.

The adapter interface (`FormAdapter`: `handles`, `detect`, `resolve`,
`describe`) is what a second site would implement; nothing outside
`src/adapters/greenhouse/` knows a Greenhouse class name.

### 13.2 Mapping and confidence

`src/profile/mapping.ts`, over the current confirmed version's snapshot
(`GET /profile/versions/current`, read only when the person presses
**Prepare answers from my profile**, used once, not kept):

| Field | Value | Confidence | Ticked |
|---|---|---|---|
| Email, phone, full name, location | the contact value, verbatim | exact | yes |
| LinkedIn / GitHub | the one personal link on that host (a look-alike host does not count; project links never do) | exact | yes |
| GitHub link to a repository | that link | derived | no |
| Website | the one other personal link, or the one labelled as a website/portfolio | exact if so labelled, else derived | only if labelled |
| First / last name | a two-word full name, split | derived | yes |
| First / last name, 1 or 3+ words | nothing: "which part is your first name isn't something EasyApply can know" | none | — |
| City | the location, as the closest fact | derived | no |
| Skills | skill names, comma-separated | derived | no |
| Preferred name, free-text answers, unknown questions | nothing | none | — |
| Resume / cover letter | nothing: "Attach your resume yourself" (+ the tailored resume's download link when ready) | none | — |
| A choice (combobox, select, radio, checkbox) | never filled; a profile fact that fits is shown as a hint ("Your profile says: London, United Kingdom") | none | — |

### 13.3 Sensitive questions

`src/forms/classify.ts` asks *is it sensitive?* before *what is it?*, and a
sensitive field is never filled whatever else it looks like ("Email address
of your immigration lawyer" is not an email field). Categories: work
authorization, sponsorship, visa/citizenship, demographics (and everything in
the demographic section, whatever its wording), veteran status,
disability/accommodations, criminal history, security clearance, relocation,
pay, notice period/start date, years of experience, legal attestations and
consents, age, previous employment with the company, where the person lives
or can work. The profile holds none of these facts; where it holds something
near one (a location, dated jobs) deriving an answer is exactly the guess the
person must make. Each is shown as "Review required" with the category named.
Broad on purpose: a false positive costs the person one answer; a false
negative could put a wrong answer to a legal question on their application.

### 13.4 Filling

**Fill N selected fields** sends the content script only the ticked values
(`{key, value, label}`), and `src/forms/fill.ts` checks each against the page
as it is *now*: still present inside the form, still the label the person
approved, not sensitive (re-classified), a visible enabled text control,
empty, and within `maxlength` — otherwise skipped with a reason the report
names. A value is set through the element's native setter plus `input` and
`change` events (how a React form learns of it). Nothing is clicked, focused
or submitted. The report says what was filled and what was left, and: "Review
the whole form on the page … and submit it yourself when you're ready.
EasyApply hasn't submitted anything, and won't."

### 13.5 The second site: Lever, and whether the interfaces held

Lever (`jobs.lever.co`, checked live on 2026-09-26; fixtures
`test/fixtures/lever-job.html` and `lever-apply.html`) differs from Greenhouse
in every way an adapter interface could be caught out by:

| | Greenhouse | Lever |
|---|---|---|
| Where the form is | beside the posting, same page | its own page, `/<company>/<uuid>/apply` |
| How a control is found | `id` | `name` (`urls[LinkedIn]`, `cards[<uuid>][field0]`) |
| Where a question's words are | `<label for>` (file: its upload group) | `.application-label` inside a `<label>` that wraps the field |
| Choices | react-select comboboxes | native radio groups, checkbox groups, `<select>` |
| Submit | `type="submit"` | `type="button"` driven by Lever's script |

What it took, all inside `src/adapters/lever/` (`site.ts`, `job.ts`,
`application.ts`) plus one line in each registry and the manifest's host
lists: **no change to `FormAdapter`, `SiteAdapter`, the classifier, the
mapping, the filler, the popup's panels or the worker.** One addition was
needed in the popup's vocabulary: an `application_page` extraction result, so
a page that is a form but not a posting says so, links its posting, and still
offers the form — rather than "No single job here". The visibility check the
two form adapters share moved to `src/forms/visibility.ts` instead of being
copied.

Only `jobs.lever.co` is claimed; Lever's other hosts were not checked. The
posting adapter reads the headline, the location chip, the description
sections (not the Apply block), the company from the title or logo, and the
posting's uuid; the application page is reported as such and never saved as
a posting (it has the title but not the description).

## 14. State

`JobFlow` (src/workflow/job-flow.ts):

```
requirements  loading ─▶ processing ─▶ ready | failed | unavailable | error
                            └──────▶ still_processing
profile       idle ─▶ loading ─▶ current | no_profile | no_versions | drafts_only | no_current | error
match         idle ─▶ loading ─▶ none ─▶ matching ─▶ matched | error
tailoring     idle ─▶ loading ─▶ none ─▶ starting ─▶ running ─▶ ready | not_approved | no_file | failed
                                                         └──▶ still_running
session       active ─▶ expired      (any proxy 401 `session_required`)
                     └─▶ changed      (a proxy 409 `session_changed`: another account signed in)
```

Every answer is dispatched with the key it was asked under — **account (its
tag), tab, job** — and match/tailoring answers also with the **profile version**; an
answer for any other key is dropped unread. When the current version changes,
match and tailoring are reset and re-read, and their in-flight polls are
aborted. The application panel has its own small machine (`found → loading →
review → filling → filled`, or `error`).

**Caching.** The only thing stored is V1's per-tab record of a successful save
(`chrome.storage.session`), which carries the account it was made by; a
different account in the same browser never sees it (tested in unit and
browser suites). Everything else is read from the backend each time the popup
opens. The backend is authoritative even so: an id from a stale view is a 404
in another account.

## 15. Security and privacy

- **Identity is the web app's.** Every request carries the browser's session
  cookie and nothing else that names a person; the proxy builds upstream
  headers from `resolveIdentity()` alone. Tested: a claimed
  `X-Dev-User-Email`/`Authorization` from the extension is replaced (route
  tests) and changes nothing on the real stack (browser suite).
- **Untrusted input.** Calls are a closed list with integer ids; content
  messages are shape- and size-checked (≤ 200 instructions, bounded strings);
  answers are shape-checked; the worker answers only its own popup; the
  content script answers only its own extension.
- **Page text is data.** Everything reaches the popup through `textContent`
  or `value`, never markup (unit and browser tests with `<img onerror>` and an
  instruction-shaped posting). The extension never acts on anything a page
  says; the backend's matcher and tailor are deterministic and the verdicts
  shown are the stored ones.
- **Nothing submitted.** No code path clicks, focuses, submits or calls
  `requestSubmit`; the browser suite counts submit events on the page (0) and
  records every request to the job site (all GET).
- **Minimal data.** The content script sends descriptors, never field values;
  the profile snapshot is fetched only on request, used once in the popup, and
  only approved values reach the page. No credential, key or token is in the
  extension; permissions are still `storage` plus the job-site and web-app
  hosts.
- **Failures are explicit** (§10–§12): each category has its own sentence and
  next step; a session that ends mid-flow replaces the panel with sign-in and
  nothing is sent on its behalf.

## 16. Observability

- **Extension worker** (`src/background/diagnostics.ts`): one line per
  request, `easyapply.api {"at","route":"POST /jobs/:id/matches","outcome","status","ms","ids":{"jobId":..,"profileVersionId":..}}`,
  and the last 50 kept in memory. No body, email, requirement or profile value
  (tested).
- **Web proxy**: one line per request from an extension origin, admitted or
  refused, `proxy.extension {"extension","method","route":"/jobs/:id/matches","status","ms"}`.
  Never the identity or the body (tested).

## 17. Tests (V2)

| Suite | Count |
|---|---|
| `apps/extension` Vitest | 442 (175 before V2) |
| `apps/extension` Playwright, stub web app | 14 (7 before) |
| `apps/extension` Playwright, real stack (`npm run test:browser:real`) | 14 |
| `apps/web` proxy route tests | 83 (56 before) |

See the V2 report (`docs/chrome-extension-workflow-2026-09-26.md`) for what
each covers and the full regression results.

## 18. Known limitations (V2)

- **Two sites.** Greenhouse-hosted boards (posting and form) and
  `jobs.lever.co` (posting, and its separate application page). LinkedIn,
  Indeed, Workday, embedded boards and company career sites are reported as
  unsupported.
- **On a Lever application page the tailored resume is not linked**: that page
  is not a posting, so the popup does not know which saved job it belongs to
  (there is no read-only lookup of a posting by identity — below). Open the
  posting to see its tailored resume.
- **Choices are never set.** Every Greenhouse choice question is a
  react-select combobox; setting one means simulating a person opening a list
  and picking — fragile, and for these questions (authorization, sponsorship,
  demographics) exactly what must be the person's own act. They are listed as
  "Review required", with a hint where the profile has a fitting fact.
- **Files are never attached.** The resume field links the tailored resume's
  download; attaching it is the person's step.
- **A job saved in another tab or on another day** shows the review screen
  first; pressing Save answers "Already in EasyApply" and then the job panel.
  There is no read-only lookup of a posting by its identity (`JobSummary`
  carries no external id, and duplicating the API's identity rule in the
  client was declined).
- **Retrying a failed reading** is on the job's web page, not in the popup:
  reprocessing is a write the proxy does not admit from the extension.
- **No real authentication** — unchanged from V1 (authentication.md).
