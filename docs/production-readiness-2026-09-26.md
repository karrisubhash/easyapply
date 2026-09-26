# Production readiness pass — 2026-09-24 to 2026-09-26

The engineering report for the end-to-end production-completion brief. Every
number in it was produced by a command listed in §22, run against the working
tree as it stands at the end of the pass. Where something was not run or could
not be checked, it says so and is labelled **UNVERIFIED**, **KNOWN ISSUE** or
**EXTERNAL BLOCKER**. No frozen dataset, expectation file or release criterion
was edited (`evaluation/datasets/**`, `ai_release_criteria.json`,
`tailoring_v1/cases.json`), and no evaluation criterion was changed.

The repository has no git history. "Before" means the snapshot taken at the
start of the pass (`scratchpad/baseline/tree`, 2026-09-24 22:45); a second
snapshot (`scratchpad/combined/tree`, 2026-09-26 morning) separates this pass's
work from a concurrent Claude session ("dev-fb") that also edited the repo. Its
changes (mostly `app/services/job/structuring.py` and
`tests/test_job_structuring_real_postings.py`) were reviewed and kept, and
that session was stopped by the user.

---

## 1. Executive summary

- **Three review rounds, more than 120 defects.** Review 1 was a full audit,
  about 60 itemised defects plus domain items. Review 2 was an adversarial
  re-review of review 1's fixes: 42 findings, each probed and voted on by
  skeptics, plus 13 more that the domain agents found when re-probing. Review 3
  covered the frontend and the cross-cutting integration, which no earlier pass
  had covered: 9 findings (7 behaviour, 2 docs), plus one renderer defect found
  by the final test run. Every confirmed defect was fixed with a
  regression test, except those listed in §20.
- **Everything is green.**
  - Backend: **2676 passed, 0 failed, 13 skipped** (before: 1793 passed, 2 failed).
  - Web: **371/371** (before: 296). `tsc` is clean, and eslint has 0 errors (the
    same 6 warnings as before).
  - Browser (e2e): **12/12** in a clean run.
  - Migrations build from empty to head with no model drift.
- **Matching** is now `matching_v5_1`. Every change is either more conservative or
  better evidenced, and all four frozen matching benchmarks produce output
  **byte-identical** to before (version name normalised). `matching_v1`..`v5`
  remain defined, so historical results still resolve.
- **Extraction quality rose on the frozen evaluations:**
  - resume structuring: recall 0.53 → 0.59, precision 0.74 → 0.80, fabricated 16 → 12;
  - job structuring: recall 0.92 → 0.93, precision 0.98 → 0.99, fabricated 2 → 1.
- **AI tailoring is still not releasable on evidence.**
  - The prompt is now `tailoring_ai_v6`, and grounding is much stricter.
  - The deterministic generator meets every release criterion.
  - The only v6 provider measurement is a single smoke call. It delivered with
    0 errors, 0 repairs and selection 0.957.
  - The last full 20-call run (v3) failed the release criteria, mostly on the
    free endpoint's availability.
  - The 20-call run on v6 was not run (it needs your go-ahead).
- **Release recommendation (§23):** not releasable as a multi-user production
  service, because there is no real authentication. The deterministic product is
  ready for a controlled single-user or internal deployment. Keep
  `TAILORING_AI_ENABLED=false`.

## 2. Areas audited

| Area | Scope |
|---|---|
| Matching | `app/services/matching/*` (rules, negation, engine, service), the API, the benchmarks |
| Tailoring | Generation (prompt, fences, deterministic tailor), grounding, reconciliation, rendering and verification, the pipeline and queue, the service, failures, the evaluation harness |
| Resume ingestion | PDF/DOCX extraction, OCR, structuring, mapping, pipeline, retry, failures, API |
| Job intelligence | Structuring (headings, degrees, sections), failures and retry, service, API |
| Profile and versions | Snapshot schema, immutability triggers, make-current, links |
| Security and configuration | Identity seam, operator boundary, Next proxy, API guard, error handling and log hygiene, upload limits, test isolation from real providers |
| Database | Alembic migrations, `alembic check`, constraints, triggers, downgrade path |
| Frontend | Every page under `apps/web/src/app`, profile components, API client and queries, test fixtures, e2e specs |
| Accessibility | Titles, headings, field errors, live regions, link names |
| Docs | README, PROJECT.md, `docs/*.md`, checked against the code |

## 3. Bugs

Severity is the reviewer's, confirmed by an offline probe before fixing.

**Review 1 (audit)**

| Domain | Defects |
|---|---|
| Matching | M1 CRITICAL: negation holes ("unfamiliar with", "didn't use", "never had the chance to use", "lacks X experience", …) were read as MATCHED. M2 HIGH: one generic word confirmed a field, and one role credited all years. M4 HIGH: a requirement key over 128 characters caused a 500. M5 MEDIUM: a future end date counted years that had not elapsed. M6 MEDIUM: a degree in progress counted as held. M7 MEDIUM: evidence was stored as `qualifies=True` under UNCERTAIN/MISSING. M9 MEDIUM: a corroborating role flipped stated years to MISSING. M10 MEDIUM: "5+ years with Kubernetes and Go" checked only the first technology. M11 LOW: single-letter technologies were used as duration subjects. M12 LOW: duration evidence had no quote. |
| Tailoring | C1 CRITICAL: a number was licensed by the whole row, not by the line's quote. C2 CRITICAL: skill group categories were never validated. C3 CRITICAL: invented names were only warnings. H1 HIGH: a stalled run blocked Generate forever. H2 HIGH: career integrity (duplicate, missing or reordered jobs, bulletless jobs) was not enforced. H3 HIGH: a single-date education rendered "– Present". H4 HIGH: contact links were not hyperlinks. M1: contact omission passed. M2: duplicate or empty lines passed. M3: core properties said "python-docx". M4: provider metadata was dropped. M5: stage statuses were never committed. L1: a match from another version was accepted. L2–L8: worker asserts, failed stage after rollback, per-token warnings, control characters crashing the render, spacer paragraph, concurrent-attempt 500, untruncated claims. D8: a `</job_posting>` breakout in the prompt. |
| Security | S1: dev identity header not guarded by environment, and a blank or overlong header caused a 500. S2: `next dev` bound all interfaces. S3: CSRF through the proxy on body-less or multipart POSTs. S4: the immutability trigger missed the contact and summary columns and `profile_links`. S5: no decompression or page limits. S6: tests did not pin the AI switches. S7: upload size was enforced only after reading the full body. S9: SQL parameters and personal data in logs. S10: the operator API failed open, and a non-ASCII token caused a 500. |
| Frontend | D1 HIGH: the match page showed the newest match, not the selected version's. D2: the version was lost between Match and Tailor. D3: a versions error was shown as "no confirmed version". D4: an unticked "make current" was a dead end. D5: every page had the same `<title>`. D6–D21: silent reprocess error; New draft did not navigate; field errors were not announced; a job without a description could not be fixed; stale "Draft ready" card; error and empty state shown together; no retry; wrong copy; completion not announced; link names collided; the informed-by link went to the wrong match; raw stage codes; missing h1; "Needs review" badge; id fallbacks; `revokeObjectURL` timing. |
| Resume and jobs | Review-1 items fixed by the domain agents, including the two golden-profile link tests that were failing before this pass (a project "Demo" link was not extracted). |

**Review 2 (re-review of the fixes): 42 findings**

- **Job structuring (10):**
  - HIGH: short degree forms were filed as required skills; an optional heading moved its items into REQUIRED.
  - MEDIUM: duty and values headings; loose company headings dropped sections; "Bonus" in a pay block; typographic apostrophes.
  - LOW: REST/TS guard; BA/MS guards; a vacuous test; false docs.
- **Matching (10):**
  - HIGH: "HTML/CSS" and similar lists were read as alternatives (a false MATCHED).
  - MEDIUM: new negations fired on subordinate clauses; the short-subject guard was bypassed ("R&D", "C-level") or dropped "k6"; a qualifier-only field could never be confirmed; one shared word confirmed a field.
  - LOW: joiners ("and/or", "(or X)", ".NET"); an UNCERTAIN any-of explanation was cut off; false version notes; a future-start degree counted as held.
- **Resume ingestion (7):**
  - HIGH: two-column headings dropped every section; common heading forms were lost.
  - MEDIUM: LinkedIn certifications; invented issuers; DOCX table-cell links; an agency name read as the candidate's name.
  - LOW: "Company | Title | Dates" swapped.
- **Security and versions (4):**
  - MEDIUM: the CSRF and DNS-rebinding defences existed only in the proxy, while the API on :8000 accepted any Host and Origin.
  - LOW: false README and ENVIRONMENT comments; a draft's link row could point at a confirmed version's project.
- **Tailoring grounding (10):**
  - MEDIUM: duplicate-line repair removed the grounded copy; a summary repeating a bullet withheld the document; numbers were compared as raw tokens ("four" vs "4"); "zero downtime" was read as an inversion.
  - LOW: a moved negation; the CJK rule; DeterministicTailor failed the new checks; punctuation-only lines; false reason text; a vacuous test.
- **Tailoring pipeline (6, all LOW):** control characters in link URLs; empty entry-header paragraph; `display_url` scheme stripping; template timestamps in core properties; closing-tag lookalikes in the fence; faithful copies of neutralised fence tags.

The domain agents' re-probes of review-1 items found more:
- **Resume:** oversized pages were skipped by OCR; skills wrapping glued lines together; fence neutralisation leaked into quotes; an owner-password PDF gave the wrong page count; unsupported encryption was misreported; a broken header reference; a dropped third pipe column; an empty page 1 still completed; the web fixture's `can_retry`.
- **Jobs:** a lead-in sentence disabled the list style; missing company, pay and benefits headings; an O(n²) look-ahead; the web fixture's permanent codes.

**Review 3 (frontend and integration) plus the final run: 9 findings and 1 renderer defect**

| # | Severity | Defect |
|---|---|---|
| F1 | MEDIUM | After a failed reprocess the tailor page said "This job has not been read yet". The server was still tailoring against the earlier reading, so the user hit a dead end. |
| F2 | MEDIUM-LOW | The match page's "View the match from version N" link could not override an earlier picker choice or a released pin (the page is not remounted on a search-param change). |
| F3 | LOW | On the profile page and dashboard, a failed versions request was reported as "No confirmed version yet". |
| F4 | LOW | The project-links parser split only on a space. A tab, a reversed "URL label" or a label alone was saved as the URL and rendered as a link to nowhere. |
| F5 | LOW | "New draft" on Version History did not navigate (the profile page's did). |
| F6 | LOW | The tailor page said a match "only changes the order", which is false under the AI generator. |
| F7 | LOW (plausible) | A retried job save whose first response was lost was told "nothing you entered here was saved". |
| F8, F9 | LOW (docs) | The README said the proxy guarded "POST or PUT" (it guards every non-GET/HEAD method); PROJECT.md listed algorithm versions only up to `matching_v4`. |
| R1 | LOW | Two renders of one document were not byte-identical: python-docx stamps each zip entry with the current time at 2-second resolution, and artifacts are keyed by SHA-256. Found as an intermittent failure in the full suite. |

## 4. Root causes

1. **Rules written for one phrasing.** Headings, negations, degree forms and joiners were matched against the examples at hand, and near variants fell through. That covers typographic apostrophes, two columns on one line, "(continued)", "didn't use", "BS Computer Science", "and/or" and "X or .NET". Most of review 2 was this class, often introduced by review 1's own tightening.
2. **Checks against the wrong span of text.** Numbers and seniority were licensed by the whole row instead of the line's own quote. The field check accepted one shared word. Page counts came from `/Count` instead of the page tree.
3. **Defences at one layer only.** CSRF and Host checks lived only in the Next proxy. Snapshot immutability lived in the service, not the database, for columns added later. Career integrity was enforced in evaluation but not in production.
4. **State that outlives its meaning.** This covers the frontend (a picker choice surviving a new link, a POST snapshot shown as current), a stalled run counted as in-flight, and a failed reprocess hiding a valid earlier reading.
5. **Fail-open defaults.** The operator API was open without a token, the dev identity was accepted in every environment, and the AI switches were unpinned in tests.
6. **Tests that could not fail.** An assertion held a 0x08 byte from a shell escape, a heading test passed without the patterns it protected, and a negation test never exercised a negation. Each was rewritten, and each new test was checked to fail against the pre-fix code.

## 5. Fixes

Each defect got the smallest change that removes the cause, plus a regression test. The test was confirmed to fail on the pre-fix code, either by a recorded run or by temporarily bypassing the fix, as for F2 and R1. The domain docs record each rule and its trade-off:
- `docs/matching.md` (v5_1 section);
- `docs/resume-tailoring.md` (§3, second review);
- `docs/llm-tailoring.md` (v4–v6 rows, rendering);
- `docs/resume-intelligence.md` (§4–§8);
- `docs/job-intelligence.md`;
- `docs/async-resume-processing.md` (operator table).

The main fixes, by kind:
- **Vocabulary widened with guards.** Closed lists plus shape checks; for example, "MS" after a comma needs explicit degree context, so "Jackson, MS" stays a place.
- **Checks moved to the right span.** Number and seniority licensing uses the line's quote, and a number is licensed by its quantity ("four" ≡ "4", "doubled" ≡ "2x"). The field check needs a multi-word phrase or a word that stands on its own. Pages are counted by walking the tree.
- **Defence in depth.**
  - A new pure-ASGI `BrowserRequestGuard` in the API.
  - Database triggers covering every snapshot column and `profile_links`, plus a same-version check on project links.
  - Career integrity enforced by grounding in production.
  - Every rendered DOCX is re-opened and verified.
- **Fail closed.**
  - The operator API is open only when `ENVIRONMENT=development` and no token is set; otherwise it answers 403.
  - The dev identity is guarded, and a blank or overlong header gives 401 instead of 500.
  - Tests pin every AI switch.
- **Reproducibility.** Fixed zip timestamps (R1) and no template core-property stamps.
- **Frontend.** The review-3 fixes F1–F9 are listed in §9.

## 6. Files changed

125 files changed and 25 were added, relative to the pre-session snapshot:
- the new files are 19 backend test files (one written by the other session), 2 migrations, 3 web files and this report;
- generated artifacts, e2e screenshots and lock files are not counted.

By area:

| Area | Files |
|---|---|
| Matching | `app/services/matching/{rules,negation,engine,service}.py`, `app/models/match.py` |
| Tailoring | `app/services/tailoring/{generation,grounding,reconciliation,rendering,pipeline,queue,service,failures}.py`, `app/models/tailoring.py`, `evaluation/tailoring_repairs.py` |
| Resume | `app/services/resume/{extraction,ocr,structuring,mapping,pipeline,retry,service,failures}.py`, `app/schemas/resume.py` |
| Jobs | `app/services/job/{structuring,service,failures}.py`, `app/schemas/job.py`, `app/api/v1/jobs.py` |
| Profile | `app/api/v1/profile.py`, `app/services/profile_service.py`, `app/schemas/snapshot.py` |
| Security and config | `app/main.py`, `app/core/config.py`, `app/api/{dev_identity,operator_identity,errors}.py`, `app/db/session.py`, `apps/web/src/app/api/backend/[...path]/route.ts`, `apps/web/package.json` (`next dev -H 127.0.0.1`) |
| Migrations | `alembic/versions/c3d9e1f47a20_complete_confirmed_version_immutability.py`, `alembic/versions/d4a7c2e9b815_tailoring_run_provider_metadata.py` |
| Web | 32 changed pages, components and libraries under `apps/web/src` (the proxy `route.ts` included) plus the new `src/lib/use-page-title.ts`; generated `packages/shared/src/api-schema.ts` |
| Tests | 19 new backend test files (`test_matching_v5_1.py`, `test_security_*.py`, `test_tailoring_*_{hardening,negation,review2,concurrency}.py`, `test_resume_*regressions.py`, `test_job_review_regressions.py`, …); 24 backend test files extended (`conftest.py` included); 14 web test files and 4 web fixtures extended; 3 e2e specs updated; the new `src/app/profile/versions/page.test.tsx` and `route.test.ts` |
| Docs | README.md, PROJECT.md (version list), `docs/{matching,resume-tailoring,llm-tailoring,resume-intelligence,job-intelligence,async-resume-processing}.md`, this report |

Every changed file keeps its original line-ending style, and no source file contains a stray control byte. `apps/web/src/test/server.ts` holds 0x03/0x04, but it is unchanged from before the pass.

## 7. DB and migration changes

| Revision | Change |
|---|---|
| `c3d9e1f47a20` | Re-creates `easyapply_profile_version_immutable()` so it also covers `full_name`, `email`, `phone`, `location` and `summary`. Adds `trg_profile_links_immutable` (the snapshot-child guard), plus `easyapply_profile_link_project_same_version()` and its trigger, so a project link must name a project of its own version. Downgrade restores the old guard. |
| `d4a7c2e9b815` | Adds `tailoring_runs.provider` and `provider_metadata` (nullable), so the provider, tokens and latency of an AI run are stored the way the job and resume pipelines store theirs. |

Verification: a fresh database upgraded from empty to head `d4a7c2e9b815`, and `alembic check` reported "No new upgrade operations detected". A downgrade of three revisions followed by a re-upgrade succeeded. The dev database is also at head. It was three revisions behind earlier in the pass and was upgraded outside this work; nothing here touched it.

## 8. API changes

- **New:** `POST /api/v1/profile/versions/{n}/make-current`. It answers 409 for a draft.
- **New refusals (all 403 with a stable `code`):**
  - `host_not_allowed` for a Host that is not loopback, `testserver` or in `API_ALLOWED_HOSTS`;
  - `cross_site_request` for a non-GET/HEAD request whose `Origin` or `Sec-Fetch-Site` is cross-site (at both the API and the proxy);
  - the operator API outside development without a token.
- **Tailoring:** 409 when `match_result_id` belongs to another profile version or requirements row.
- **Identity:** a blank or overlong `X-Dev-User-Email` gives 401 (it was a 500). A concurrent first-sight request no longer gives a 500.
- **Uploads:** size is refused at the edge with 413, before the whole body is read.
- **Schema:**
  - `ProjectItem.technologies` items are bounded at 128 characters.
  - The `JobRequirementsResponse` docstring now documents that `requirements` is the newest reading, and that `run` can be an earlier attempt than `job.latest_run`.
  - Provider metadata is stored on the run (§7) but not exposed in any response.
  - `api-schema.ts` was regenerated from the live OpenAPI; the last regeneration's only change was that docstring.
- **Settings:** new `API_ALLOWED_HOSTS` (API) and `PROXY_ALLOWED_HOSTS` (web). `ENVIRONMENT` (development/test/production) decides the dev identity and the operator boundary.

## 9. Frontend changes

- **Match and Tailor carry the version and the match.**
  - The match page shows the selected version's own match, and supports `?match=` and `?version=`.
  - The tailor page pre-selects the match's version, drops the match on a version change (and says so), and shows the server's 409.
  - F2: the "View the match from version N" link now resets the earlier pick.
- **Tailoring after a failed reprocess (F1).** The tailor page fetches the requirements when the latest attempt failed, offers Generate against the earlier reading, and says it is using it.
- **Profile and versions.**
  - A make-current button, and "no current version" copy that differs from "nothing confirmed".
  - New draft opens the editor on both the profile and Version History pages (F5).
  - A failed versions request shows the error and Retry, never "nothing confirmed" (F3, including the dashboard).
- **Snapshot editor.**
  - Project links are carried through as one line per link ("label address").
  - They are split at any whitespace and refused with a field error when a line does not end in an address (F4). Accepted forms: `https:`, `mailto:`, `www.`, or a bare host.
- **Jobs.**
  - The description is required, and the form says when a posting was already saved.
  - A retried save of exactly what was entered opens the job (F7).
  - The job page keeps showing the last good reading after a failed reprocess.
- **Everywhere.** Retry on every query error, readable failure-stage labels, "Untitled job", a "Not approved — no file" badge, delayed `revokeObjectURL`, and a visible reprocess error.
- **Copy.** The tailor page now says a match "never lets the resume claim more than the version holds" (F6).

## 10. AI and LLM changes

- **Prompt `tailoring_ai_v3` → `v6`** (history in `generation.py` and `docs/llm-tailoring.md`):
  - v3: bare skill names under category headings.
  - v4: the `- ` requirement marker is explained.
  - v5: every untrusted block is fenced, with closing-tag neutralisation; a `<requirement_support>` block comes from match verdicts; skill, headline and duration rules.
  - v6: every job keeps at least one bullet; closing-tag lookalikes are neutralised (spacing, zero-width characters, fullwidth `＜／`, dash variants).
- **OpenRouter** is an evaluation-only second provider (`TAILORING_AI_PROVIDER=openrouter`). Model: `nvidia/nemotron-3-super-120b-a12b:free`.
- **Provider metadata** (model, upstream provider, tokens, latency, cost) is persisted per run.
- **Tests never call a provider.** `conftest.py` pins every AI switch, and `test_security_test_isolation.py` holds that.
- **Unchanged:** `TAILORING_AI_ENABLED` is still `false`, and so is every other AI switch.

## 11. Matching changes

`matching_v5_1` (`ALGORITHM_V5_1`). `matching_v1`..`matching_v5` stay defined, and `matching_v6` is reserved for the declared-only redesign.

- **Negation:** 10 patterns, 4 of them new. The new ones yield when a subordinator ("which", "that", "because", "before", …) precedes them in the span, so paid work described in a subordinate clause is not demoted.
- **Field of work:** confirmed by a multi-word phrase or a word that stands on its own, in an affirmed clause or title. There are two-word forms for frontend, backend and fullstack, and a fallback for postings that name only qualifier-dependent words. Total employment is still counted from all roles; restricting it to in-field roles cost three correct `matching_real_v1` verdicts and was reverted.
- **Dates:**
  - A future end date is clamped, and a role that has not started counts 0 years.
  - A degree is in progress (UNCERTAIN) if its end or start date is later than the as-of date.
- **Subjects:**
  - "X and Y" requires both.
  - Any-of is read only when a word ("or", "and/or") closes the list, so "HTML/CSS, JavaScript" is all-of. "(or X)" and "X or .NET" are understood.
  - Short subjects (R, C, Go) need to stand as subjects; "k6" is exempt.
  - Stated years that conflict with the roles give UNCERTAIN.
- **Evidence:**
  - Duration evidence quotes its span.
  - `qualifies` is true only under MATCHED.
  - An any-of MATCHED cites only the matched evidence.
  - Labels, keys and values are truncated to their columns, so there are no 500s.

Result: `matching_real_v1`, `declared_only_v1`, `non_doing_v1` and `non_doing_v2` are byte-identical to before. On `matching_real_v1`: 4 false matches (rate 0.044), 3 false missing, required agreement 0.928, preferred agreement 0.864.

## 12. Grounding and reconciliation changes

Grounding raises errors for:
- a number or seniority word not licensed by the line's own quote (by quantity, so rewordings pass);
- an invented name, including a line-initial technology, a lowercase technology, a non-ASCII name or a number word;
- a skill under an unknown heading, or under a stored category it does not belong to (repaired by refiling);
- a headline that is not a stored title;
- career order, duplicate or missing jobs, and bulletless jobs;
- a contact omission;
- duplicate lines, where the summary copy is removed and never the bullet, and lines already doomed are not counted;
- empty or punctuation-only lines;
- an introduced negation ("zero" counts as a negator).

Reconciliation still only removes, restores or permutes; it never writes text. A missing or bulletless job cannot be repaired, so that document is withheld.

Replaying the 17 captured AI documents (run2 and run3) under the final rules: all 17 are still delivered, and the error and repair counts are unchanged from the review-2 replay.

## 13. Security findings

| Finding | Status |
|---|---|
| The API accepted any Host (DNS rebinding) and cross-site writes | **Fixed.** `BrowserRequestGuard` in the API plus the proxy guard (14 + route tests) |
| CSRF through the proxy on body-less and multipart POSTs | **Fixed.** Same-origin required for every non-GET/HEAD method |
| Dev identity accepted in every environment; blank or overlong header caused a 500 | **Fixed.** Guarded by `ENVIRONMENT`; 401 |
| Operator API failed open; non-ASCII token caused a 500 | **Fixed.** Open only in development with no token; byte compare |
| `next dev` listened on all interfaces | **Fixed.** `-H 127.0.0.1`. Your running :3000 server picks this up on restart |
| Snapshot immutability incomplete in the database | **Fixed.** Migration `c3d9e1f47a20` |
| No decompression, page or render limits; upload size read in full first | **Fixed.** DOCX central-directory check, pypdf decompression bound, `MAX_RENDER_PIXELS`, edge limit |
| Tests could call a paid provider with the real key | **Fixed.** `conftest.py` pins the AI switches |
| SQL parameters in logs | **Fixed.** `hide_parameters`. **KNOWN ISSUE:** PostgreSQL's own DETAIL ("Failing row contains …") still quotes row values in logs, so treat logs as sensitive (`app/api/errors.py`) |
| Prompt injection through a posting (`</job_posting>`) | **Mitigated.** Fences plus v6 lookalike neutralisation. Letter-for-letter homoglyphs are not caught; grounding is the defence that does not depend on spelling |
| No real authentication | **KNOWN ISSUE / release blocker.** The dev identity seam stands in until Entra External ID |

`.env` and `.env.*` are ignored by version control. Keys are never printed by the smoke script or logged by this code.

## 14. Accessibility findings

All of these were fixed:
- a `<title>` per route (WCAG 2.4.2);
- one h1 in every state, including loading and error (1.3.1, 2.4.6);
- field errors tied by `aria-invalid` and `aria-describedby`, including the new project-links error (3.3.1);
- the not-saved summary beside Save;
- focus moving to a rejected save's reason;
- a live region announcing tailoring completion, always in the DOM and changing only on completion (4.1.3);
- distinct accessible names for repeated links, each starting with its visible text (2.4.4, 2.5.3);
- readable failure-stage labels instead of codes.

The e2e accessibility spec passes. Not done: no screen-reader session and no manual audit beyond what the specs and unit tests assert (**UNVERIFIED**).

## 15. Tests run

- The backend suite, on its own test database;
- web unit tests (vitest), TypeScript check (`tsc --noEmit`), eslint;
- the Playwright e2e suite, with its own API, web servers and database;
- the migration build, `alembic check`, and downgrade/upgrade;
- four matching benchmarks, the job and resume structuring evaluations, and the deterministic tailoring evaluation;
- an offline replay of the captured AI documents;
- one AI smoke call;
- a slow-input (ReDoS) probe of the new job-structuring patterns: 20–100k-character adversarial inputs take ≤ 0.3 s.

## 16. Test counts

| Suite | Before | After |
|---|---|---|
| Backend (pytest) | 1808 collected: 1793 passed, **2 failed**, 13 skipped | 2689 collected: **2676 passed, 0 failed**, 13 skipped |
| Web unit (vitest) | 296/296 | **371/371** (22 files) |
| TypeScript | clean | clean |
| eslint | 0 errors, 6 warnings | 0 errors, the same 6 warnings (pre-existing) |
| E2E (Playwright) | 12/12 | **12/12** |

The 13 skips are all in `test_tailoring_evaluation.py`: parametrised checks for a kind of expectation (exclusion, leading bullet, named bullet, certification, metric) that the frozen case does not state.

## 17. E2E results

`npm run test:e2e` gave **12 passed (2.0 min)**. It was a clean run: no orphaned servers before it, its own ports (API 8100, web 3100/3101) and database (`easyapply_e2e`), and a fresh log (`scratchpad/e2e_r3.log`). Afterwards no process remained on the e2e ports, and `apps/web/next-env.d.ts` (which the e2e build rewrites) was restored. Your servers on :3000 and :8000 were not touched.

## 18. AI eval results

| Run | Prompt | Result |
|---|---|---|
| Deterministic generator, `tailoring_runner --tailor deterministic` | n/a | **Meets every release criterion.** Delivered 1.00, selection 1.00, 0 repairs, 0 safety violations |
| Run 3, 20 calls (2026-09-24) | v3 | **Fails.** Delivered 0.30, selection 0.751, 0.67 repairs per document |
| Smoke (2026-09-25), 1 call | v5 | Delivered, 0 errors, 0 repairs, selection 0.957, ~45 s |
| Smoke (2026-09-26), 1 call | **v6** | Delivered, schema-valid on attempt 1, 0 errors, 0 repairs, selection 0.957 (missed `no-project:Observability Starter Kit`), ~86 s, 9262 output tokens (8359 reasoning), cost $0 |
| 20-call runner on v5 or v6 | — | **UNVERIFIED.** Not run: it needs your go-ahead and a new `--capture` path |

One sample is not a distribution. v6's quality is unmeasured beyond "the path works once".

## 19. Provider reliability

On the free OpenRouter endpoint:
- Run 1: 6 of 20 calls unavailable.
- Run 2: 11 `ai_provider_unavailable`.
- Run 3: 12 unavailable and 2 malformed out of 20.
- Both smokes succeeded on the first attempt.
- Latency is 45–86 s per call, dominated by reasoning tokens.

That availability alone keeps "delivered ≥ 0.95" out of reach on this endpoint, whatever the prompt. Anthropic was not usable (see §21).

## 20. Known issues

1. **Tailoring pipeline transactions (review-1 M5), not fixed.** Stages are flushed, not committed, so the database transaction stays open across the provider call (up to ~86 s), and other sessions cannot see the intermediate stage. It only matters with AI tailoring on; the default deterministic generator is fast.
2. **The AI 20-call evaluation has not been run on v5 or v6** (UNVERIFIED, §18).
3. **Matching limits** (`docs/matching.md`):
   - one discipline word ("software", "data", "web") still confirms a field, so a "Software Sales Executive" confirms "software development";
   - total employment counts all roles;
   - a technology offered as an alternative is still credited by span;
   - a single clause credits a whole role's duration (documented intent; a product decision).
4. **Grounding limits** (`docs/resume-tailoring.md` §3):
   - an invented non-technology proper noun at the start of a line is only a warning;
   - a summary quote joined from two rows;
   - a negation moved rather than added;
   - unspaced scripts (CJK) cannot be reworded;
   - `targeted_requirements` naming an eligibility requirement;
   - a profile line containing one of the prompt's fence tags cannot be quoted verbatim.
5. **Resume limits** (`docs/resume-intelligence.md`):
   - "EXPERIENCE AWS Certified", where the column gap is under three spaces, stays content;
   - "Acme | Barista" is read title-first;
   - a bare organisation under a certification becomes its issuer;
   - without OCR, an empty trailing page fails the document as `ocr_unavailable` (as before; retry allowed);
   - an agency header's email can become the contact email;
   - a DOCX `w:br` splits a line (python-docx does the same);
   - a multi-paragraph table cell hides an opening heading.
6. **Job limit:** the REST/TypeScript guards (review-2 #7) were left unchanged; both skeptics judged it not a defect.
7. **Logs are sensitive** (PostgreSQL DETAIL quotes row values; §13).
8. **Docs:** PROJECT.md's "Current status" paragraph still describes an earlier milestone (`matching_v4`). Only its version lists were corrected.
9. **Coordination:** another Claude session edited the repository during this pass. Its changes were reviewed and kept; there is no git history to attribute them further.

## 21. External blockers

- **EXTERNAL BLOCKER: Anthropic account out of credit** ("credit balance is too low"). The production provider cannot be evaluated.
- **EXTERNAL BLOCKER: OpenRouter free-endpoint availability** (§19). A paid endpoint or another provider is needed for a meaningful delivered rate.
- **EXTERNAL BLOCKER: Tesseract not installed** (no binary on PATH). Scanned or image-only resumes fail as `ocr_unavailable`, and the OCR paths are covered only by unit tests with fakes.
- **EXTERNAL BLOCKER: `cryptography` not installed** in the API venv, so AES-encrypted PDFs cannot be decrypted, even those that open without a password.
- **No real authentication:** Microsoft Entra External ID is not integrated. It is a product dependency rather than a code defect, but it blocks multi-user production.

## 22. Exact verification commands

From `services/api` (Git Bash):

```sh
# Backend suite: its own database; the conftest refuses the dev DB
TEST_DATABASE_URL=postgresql+psycopg://postgres:subhash@localhost:5432/easyapply_test_m \
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -p no:cacheprovider -o addopts="" -q

# Migrations: fresh DB -> head, drift check, downgrade and upgrade
DATABASE_URL=postgresql+psycopg://postgres:subhash@localhost:5432/<fresh_db> .venv/Scripts/python.exe -m alembic upgrade head
DATABASE_URL=postgresql+psycopg://postgres:subhash@localhost:5432/<fresh_db> .venv/Scripts/python.exe -m alembic check
DATABASE_URL=postgresql+psycopg://postgres:subhash@localhost:5432/<fresh_db> .venv/Scripts/python.exe -m alembic downgrade a3f19c7d4e52
DATABASE_URL=postgresql+psycopg://postgres:subhash@localhost:5432/<fresh_db> .venv/Scripts/python.exe -m alembic upgrade head

# Benchmarks and evaluations (no provider calls)
export PYTHONPATH=. PYTHONIOENCODING=utf-8
.venv/Scripts/python.exe -m evaluation.benchmark.runner --dataset matching_real_v1
.venv/Scripts/python.exe -m evaluation.benchmark.runner --dataset declared_only_v1
.venv/Scripts/python.exe -m evaluation.nondoing.runner --dataset non_doing_v1
.venv/Scripts/python.exe -m evaluation.nondoing.runner --dataset non_doing_v2
.venv/Scripts/python.exe -m evaluation.job_runner
.venv/Scripts/python.exe -m evaluation.runner
.venv/Scripts/python.exe -m evaluation.tailoring_runner --tailor deterministic

# ONE real provider call (OpenRouter free model)
.venv/Scripts/python.exe -m evaluation.tailoring_smoke
```

From `apps/web`:

```sh
npx vitest run
npx tsc --noEmit
npx eslint .
npm run test:e2e        # then restore apps/web/next-env.d.ts to ./.next/types
```

From the repo root, after any Pydantic change: `npm run api:types`. It needs the API's venv Python on PATH as `python`, or run `services/api/.venv/Scripts/python.exe services/api/export_openapi.py` and then `npm run generate --workspace @easyapply/shared`.

## 23. Release recommendation

Based only on the evidence above:

- **Multi-user production: NO.** There is no real authentication, and the dev identity seam is not an access control. That alone blocks it, whatever the test results.
- **The deterministic product** (upload, structure, confirm, match, tailor with the deterministic generator, download): **ready for a controlled single-user or internal deployment.**
  - Every suite is green, and the browser run is clean.
  - Benchmarks show no regression, and extraction quality improved.
  - The database is protected by triggers.
  - The known limits are documented and are precision trade-offs, not safety holes.
- **AI tailoring: keep `TAILORING_AI_ENABLED=false`. The evidence is insufficient.**
  - v6 has one successful sample.
  - The last full measurement (v3) failed release, and the endpoint's availability alone fails "delivered".
  - Before enabling it for anyone:
    1. fund the Anthropic account, or choose a reliable paid endpoint;
    2. run `tailoring_runner --tailor ai --samples 5 --capture var/tailoring-eval/<new>.jsonl`;
    3. replay the captures with `tailoring_repairs`;
    4. read the result against the unchanged `ai_release_criteria.json`.
  - Also fix known issue 1 (per-stage commits) first, so a provider call does not hold a transaction open.
