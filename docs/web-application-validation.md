# Web application validation

A production validation pass over the complete user journey — dashboard through
downloaded DOCX — driven in a real browser against the real stack, with the
defects it found and what was done about each.

Run on 2026-09-22/23 against the tree as it stood after Resume Tailoring. No
feature work, no redesign, no Matching V6, no Azure, no extension, no Entra.

---

## How it was run

Three real servers, nothing stubbed: the FastAPI application on `:8100` against
its own `easyapply_e2e` database and its own storage root, and the web app twice
(`:3100` owner, `:3101` stranger) under `next start`, serving a production
build. Resume extraction, job structuring, matching, generation, grounding
validation and DOCX rendering all genuinely execute on their real workers.

`RESUME_AI_ENABLED`, `JOB_AI_ENABLED` and `TAILORING_AI_ENABLED` are all off, so
what was validated is what a clean checkout ships: the deterministic structurer
and the selecting generator.

### The build directory this needed

`next dev` rewrites `.next` continuously, so a production build served out of the
same directory answers every route with a webpack runtime error — which meant the
end-to-end suite could not run at all while a development server was up. The
suite now builds into a directory of its own:

- `apps/web/next.config.ts` — `distDir: process.env.NEXT_DIST_DIR ?? ".next"`.
  Unset, which is every ordinary invocation, this is Next's own default; nothing
  about `npm run dev` or `npm run build` changes.
- `apps/web/e2e/support/env.cjs` — `E2E.distDir`, defaulting to `.next-e2e`.
- `prepare.mjs` builds with it; `playwright.config.ts` serves with it.
- `.gitignore` and `eslint.config.mjs` ignore it, as they already do `.next`.

### Screenshots

`apps/web/e2e/.artifacts/validation-screenshots/` holds the journey as captured
in the browser, `01`-`15` in order, plus `00-bug1-…` and `00-bug2-…` showing the
two stuck states before they were fixed. Gitignored, like everything else under
`.artifacts`.

---

## Defects found and fixed

Five reproduced. Each is stated as what a person does, what they see, and why.

### 1. The tailor page stops working after a generation that finishes quickly

**Reproduction.** Confirm a profile, save a job, open `/jobs/{id}/tailor`, press
Generate resume. The document generates and appears in Previous attempts marked
**Ready** — and the button beside it stays **"Generating…"**, disabled, above a
status line reading **"Queued…"** and the sentence "This page updates itself
while the work runs." It never recovers. Measured at 2s, 5s, 10s, 15s and 20s
after the click: identical. Only a full page reload clears it, and until then the
page's only way to generate again is dead.

**Root cause.** `apps/web/src/app/jobs/[id]/tailor/page.tsx`. The page watches a
run through `useTailoredResume`, which is enabled only while the *listing* says
the newest run is still active. The deterministic generator is fast enough to
finish before anything refetches, so the listing's first refresh already shows
the run terminal, the detail poll is never enabled, and `polled` is empty. The
page then fell back to `start.data` — the POST's own response, a snapshot frozen
at `queued` that is never refetched — with no bound on how long that fallback
applied. A value that can only ever go stale was being read as current state.

An earlier fix had closed the mirror-image case (the poll fresh, the listing
lagging). This is the same sentence failing in the other direction.

**Fix.** Bound the fallback to the gap it exists for: `start.data` stands in only
while the listing has not yet heard of that run at all.

```
const unlisted =
  start.data && !runs.data?.some((run) => run.id === start.data!.id) ? start.data : null;
const pending = polled ?? (unlisted && isTailoring(unlisted) ? unlisted : null);
```

**Regression tests.**
- `apps/web/src/app/jobs/[id]/tailor/page.test.tsx` — "stops calling a run
  ongoing once the listing has caught up with it", plus "still says something
  while the POST is the only thing that knows the run exists", which pins the gap
  the fallback legitimately covers so that closing the bug does not close it too.
- `apps/web/e2e/specs/journey.spec.ts` — "the page is usable again after a
  generation that finished at once". The browser test matters here: the bug needs
  a real worker winning a real race, and it ends by generating a second document
  without the reload the bug forced.

Both fail against the unfixed page and pass against the fixed one.

### 2. The dashboard spins forever when the API is unreachable

**Reproduction.** Point the web app at an API that is not listening and open `/`.
The Profile card reads **"Loading your current version…"**, offers **View
profile**, and stays that way — confirmed at 3s, 10s, 20s and 30s — directly
above an alert that says "The API is unreachable".

**Root cause.** `apps/web/src/app/page.tsx`. `useCurrentVersion(profile.isSuccess)`
is gated on the profile query succeeding, so when the profile query *fails* the
current-version query is never dispatched: it stays `isPending` with nothing in
flight, permanently. The card's ternary chain had a branch for the
current-version query failing but none for it never having run, so that case fell
through to the loading sentence.

This is the same class of defect the file's own comment says §11 forbids, on a
path the earlier fix did not cover.

**Fix.** Give the state a branch: `profileFailed = profile.isError && !noProfile`
reads "Your profile could not be loaded." and reuses the same condition that
already governs the error alert below.

**Regression test.** `apps/web/src/app/page.test.tsx` — "reports a failed profile
lookup instead of loading a version it will never ask for". Asserted with a 409
rather than the browser's 502 because `retryUnlessExpected` retries a 502 twice
with backoff first, and the branch under test does not depend on which failure it
was; the comment says so.

### 3. Two WCAG 2 AA contrast failures

**Reproduction.** axe-core over the journey with real data on it.

| Where | Measured | Required |
|---|---|---|
| `/jobs/{id}` — the confidence figure on a requirement's citation | 2.71:1 (`#9d9d9d` on `#ffffff`) | 4.5:1 |
| `/jobs/{id}/match` — the "Not found" badge | 4.35:1 (`#737373` on `#f5f5f5`) | 4.5:1 |

Both are `wcag2aa` / `wcag143`, both `serious`.

**Root cause and fix.**

- `apps/web/src/components/job/job-requirements-review.tsx` — the confidence span
  carried `text-muted-foreground/70`. `muted-foreground` is already the faintest
  token that clears AA on white (4.7:1); at 70% opacity it composited to
  `#9d9d9d`. The opacity modifier is gone, so the one number on the page that
  says how much to trust the rest is no longer the least readable thing on it.
  De-emphasis here is the smaller type, not less ink.
- `apps/web/src/components/match/match-status-badge.tsx` — the `missing` badge
  put `text-muted-foreground` on `bg-muted`. Now `text-neutral-600
  dark:text-neutral-300`: 7.2:1 light and 7.3:1 dark, which is the weight its
  emerald and amber siblings already carry (~6.8:1), and still neutral in hue —
  §13's rule that "missing" must not be styled as a failure is untouched.

**Regression test.** `apps/web/e2e/specs/accessibility.spec.ts` — axe over twelve
screens, scoped to `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, asserting an empty
violation list and naming any violation in the failure message. It runs the whole
journey first, because both violations were on rendered data and neither page has
any until the pipeline has run.

### 4. Every row in a listing offered the same link

**Reproduction.** `/jobs` with two postings saved renders links whose accessible
names are `["View", "View"]`; `/tailored-resumes` renders `["Review", "Review"]`.
The same shape is in `/resume` and `/profile/versions`.

axe does not flag this — it reads a link in its document context, where "View"
beside a job title is unambiguous. A screen reader's *link list* is not that
context: it reads the names alone. WCAG 2.4.4 is about that reading.

**Fix.** The visible label stays short; the accessible name carries the row.

- `src/app/jobs/page.tsx` — `View {job title}`
- `src/app/resume/page.tsx` — `View {filename}`
- `src/app/tailored-resumes/page.tsx` — `Review {job title}`, plus `, attempt N`
  when N > 1, since two attempts at one job are otherwise identical — the same
  rule the visible row already follows
- `src/app/profile/versions/page.tsx` — `Review/View version N`, `Edit version N`

**Regression tests.** `src/app/jobs/page.test.tsx` and
`src/app/tailored-resumes/page.test.tsx` assert the exact names and that they are
distinct; `e2e/specs/accessibility.spec.ts` asserts the same in the browser. Two
existing tests that addressed these links by their old bare names were updated.

### 5. "Honours" read as part of a date

**Reproduction.** Upload the golden resume, which states
`BSc, Computer Science | First Class Honours    2015 - 2018`. The review screen
prints **"Honours 2015 — 2018"** where it shows the dates the resume stated, and
the confirmed profile records the grade as **"First Class"** rather than "First
Class Honours".

Directly:

```
>>> _split_dates("BSc, Computer Science | First Class Honours 2015 - 2018")
('BSc, Computer Science | First Class',
 CandidateDate(raw='Honours 2015', value=date(2015, 1, 1), precision='year'), ...)
```

**Root cause.** `services/api/app/services/resume/structuring.py`.
`_MONTH_WORD` was `[A-Za-z]{3,9}\.?` — any short word at all — so the date-range
pattern matched "Honours 2015" as the start date. The *value* came out right,
because the bare year still parsed, but the match consumed the word out of the
line: the remainder handed to the grade rule was one word short, and the date's
`raw` — which the review screen shows deliberately, to display the words the
resume used rather than a normalised date — carried a word that is not a date.

One cause, both symptoms.

**Fix.** Build `_MONTH_WORD` from `_MONTHS` so the pattern matches exactly what
`_month` can resolve — a month name or the three-letter prefix of one, plus any
trailing letters, which is what makes "Sept" and "Jan." work:

```python
_MONTH_WORD = rf"(?:{'|'.join(_MONTH_ALIASES)})[A-Za-z]*\.?"
```

Verified to leave every other shape alone: `Mar 2022 - Present`,
`Jul 2019 - Feb 2022`, `May - Aug 2026`, `Sept 2019 - Jan. 2021`, `2019 - 2021`.
After the fix the grade reads "First Class Honours" and the date's raw reads
"2015".

**Regression test.** `services/api/tests/test_resume_golden_profile.py` already
asserts the grade one fact at a time against the frozen `expected_profile.json`
(`grade_contains: "First Class"`, which the richer reading still satisfies), and
`e2e/specs/resume-profile.spec.ts:80` asserts `/First Class Honours/` in the
browser — an assertion that was in the file and had never been able to run. The
full backend suite passes.

---

## Two existing tests that were wrong about the product

Neither is a product defect; both were corrected rather than deleted.

**`e2e/support/flows.ts` waited on a heading that has never existed.**
`createProfile` expected `Your profile`; the page's heading is `My Profile`, and
its unit test has always said so. Every spec needing a profile died here before
reaching what it was about — which is why four of the five pre-existing browser
tests were failing at the start of this pass, and why the drifted assertions
below had never been evaluated.

**`e2e/specs/resume-profile.spec.ts` asserted a project's technologies in the
document's order.** `project_technologies` is a child table with no ordinal
column, and `models/project.py` orders the relationship by name, so an
alphabetical read is what the schema promises and the resume's own order is not
retained by design. The assertion now names all three technologies in the order
the model guarantees (`Celery, PostgreSQL, Python`); asserting the document's
order would have asserted something the schema is deliberately built not to keep.
Changing that would be a schema change, which this pass is not.

---

## Verified and found correct

Everything below reproduced as designed and needed no change.

**Navigation.** Every nav link, every row link, and the eleven-step journey.
Back and forward across dashboard → jobs → job → match land on the page they name
with their data, not on a re-entered shell.

**Loading states.** Each collection distinguishes "not loaded yet" from "empty".
Resume processing reports the stage the server is actually in (`Queued…`,
`Ready to review`) rather than one spinner standing in for all of them.

**Error states.** With the API stopped, every page of the journey renders "The
API is unreachable" with the backend's own `api_unreachable` code and no white
screen or unhandled rejection. `/jobs/9999`, `/tailored-resumes/9999`,
`/resume/9999`, `/profile/versions/9999` and `/jobs/abc` are each their own empty
state; `/nope` is the framework 404.

**Empty states.** Every empty collection says it is empty and carries the action
that would fill it.

**Downloads and the binary proxy.** The DOCX arrives as
`Acme-Corp-Backend-Engineer-v1.docx`, ~37 KB, magic bytes `PK`. Through the proxy
directly, `content-type` is `…wordprocessingml.document` and `content-disposition`
names the `.docx` — the API's own headers, forwarded rather than re-derived.
Downloading twice works twice. A stranger's browser gets 404 and no bytes
(`identity.spec.ts`).

**Refresh.** Reloading a job page — a genuinely different path from a client-side
navigation — re-renders it with its requirements. Reloading a tailored resume
keeps its verdict.

**Duplicate submissions.** Two Save job clicks as fast as the browser dispatches
them produce exactly one POST and one job. Generate is disabled while its POST is
open. A confirmed version offers no Confirm button. Uploading the same document
twice routes to the existing document rather than creating a second.

**Upload rejection.** A `.txt` and a `.pdf` that is not a PDF are both refused
with the server's own `unsupported_file_type`.

**Slow worker.** With no resume worker running, the document stays `Queued…`
under "Waiting for a worker to pick this up. You can leave this page; processing
continues without it." — a true statement, not a fake spinner. A tailoring run
that stopped responding renders "Stopped responding" with "Nothing was saved.
Generating again is safe." and a working Generate again.

---

## Known, not fixed

**`heading-order` fires on every card.** `CardTitle` renders an `h3` under the
page's `h1`. axe tags this `best-practice`, not WCAG — it is not an A or AA
failure. Fixing it means changing the heading level the design system emits
across the whole product, which is a deliberate design decision rather than
something a validation pass should make on the way past. The accessibility spec
is scoped to the WCAG tags for exactly this reason, and says so.

---

## The two gate defects, fixed in a follow-up pass

Both were listed here as known-and-not-fixed and were then fixed on request.
Neither touches application behaviour.

### The lint gate was red on three false positives

`eslint .` reported `no-require-imports` on `e2e/support/env.cjs:15` and
`rules-of-hooks` on `e2e/support/fixtures.ts:57,63`. Both rules were being
applied to files they do not describe:

- `env.cjs` is CommonJS on purpose — its own docstring explains that bare Node
  ESM, Playwright's config loader and the compiled specs all read it, and CJS is
  the one format all three can load. `require` is not a lapse there, it is the
  only thing that works.
- `use` in `fixtures.ts` is the fixture argument Playwright hands to
  `test.extend`. The rule matches on the name and has no way to know there is no
  React anywhere in `e2e/`.

`eslint.config.mjs` now has two scoped blocks: `no-require-imports` off for
`**/*.cjs`, and `rules-of-hooks` off for `e2e/**`. Scoped by extension and by
directory rather than by file, so the exemption states the reason rather than
naming today's offenders — and verified with `eslint --print-config` that both
rules remain errors in `src/`, and that `fixtures.ts` keeps `no-require-imports`.

Result: 0 errors, 6 pre-existing warnings.

### The backend suite leaked database connections

`Engine.dispose()` closes what is in the pool. It cannot close a connection that
is still **checked out** — and the concurrency suites abandon threads on purpose,
with `thread.join(timeout=...)` returning while the thread is alive, so that a
deadlock fails the suite instead of hanging it. An abandoned thread's session is
never closed, so its connection outlives the process, usually `idle in
transaction`.

It stayed invisible because the next session's `DROP DATABASE … WITH (FORCE)`
cleared it. What FORCE also does is terminate the connections of any pytest
session running at that moment, which is where the `AdminShutdown` failures in
unrelated lease and retry tests came from.

`tests/conftest.py` now stamps every connection it opens with
`application_name = easyapply-pytest-<pid>` and, at session teardown, closes
anything still open under that name. Filtered on the test database as well as the
name, and the victim list is chosen inside a `MATERIALIZED` CTE — a `WHERE`
clause gives no guarantee about the order its conjuncts run in, so
`WHERE application_name = :name AND pg_terminate_backend(pid)` is free to
terminate first and filter afterwards, against every backend on the server.

Measured, with a probe that abandons a non-daemon thread the way the real suites
can:

| | connection after teardown |
|---|---|
| before | `idle in transaction` for the thread's whole life (30s in the probe; unbounded for a genuinely stuck thread) |
| after | gone within one poll of teardown |

Three consecutive full runs afterwards left `0` connections behind each time.

---

## Release checklist

Measured 2026-09-22/23. The backend rows below were green when taken and are
**not green as of 2026-09-23 17:41**: `services/api` is under active edit, and
`tests/test_resume_golden_profile.py::test_links_reach_the_profile` and
`::test_links_keep_the_words_they_were_written_as` now fail because a project's
links no longer reach the profile snapshot. That regression postdates this pass
— it reproduces identically with every change made here reverted — and it fails
the browser suite too, since every spec uploads a resume first. Re-take these
rows once the tree settles.

| | Check | Result |
|---|---|---|
| ☑ | `npm run test --workspace @easyapply/web` | 296 passed, 20 files |
| ☑ | `npm run typecheck --workspace @easyapply/web` | clean |
| ☑ | `npm run test:e2e --workspace @easyapply/web` | 11 passed, 4 spec files |
| ☑ | `cd services/api && pytest` | passes; the connection leak behind the old flake is fixed |
| ☑ | Production build | compiles; the e2e suite serves it under `next start` |
| ☑ | WCAG 2 A/AA across the journey | no violations |
| ☑ | Journey verified in a browser end to end | dashboard → DOCX |
| ☑ | `npm run lint --workspace @easyapply/web` | 0 errors, 6 warnings |
| ☐ | Authentication | still the `X-Dev-User-Email` seam; out of scope here |
| ☐ | Deployment | not in scope |

Ordinary invocations are unchanged: `distDir` defaults to `.next`, so `npm run
dev` and `npm run build` behave exactly as before. To run the browser suite while
a development server is up, nothing extra is needed — `npm run test:e2e` now
builds into `.next-e2e` on its own.
