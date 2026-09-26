# Job Intelligence — the domain and API

This is the first Job Intelligence milestone: a job posting as a domain, and
the API that lets a Chrome extension submit one (built since: see
[chrome-extension.md](chrome-extension.md)). Nothing
here matches a job against a profile, tailors anything, or fills out a form.
The point of this milestone is a Job domain strong enough that those can be
built on top of it without redesigning it.

```
Chrome Extension (apps/extension)
      ↓ raw scrape
POST /api/v1/jobs
      ↓ validation                 — reject malformed payloads at the edge
      ↓ normalization               — deterministic, no AI
      ↓ canonical Job               — immutable, deduplicated
      ↓ processing run              — append-only history
      ↓ structuring (deterministic today)
      ↓ JobRequirements             — validated, provenance on every item
```

## Architecture review

Before writing anything, the resume and profile domains were re-read for what
to reuse rather than reinvent: the error-handling convention in
`app/api/errors.py` (constraint violations mapped by `(sqlstate,
constraint_name)`, never by message text), the transaction convention (commit
inside the service layer, never in a dependency teardown), the
raw/normalized/structured separation resume draws between an uploaded file, its
extracted text, and its AI reading, the provenance shape resume's `Provenance`
schema already defines, and the append-only processing-run pattern
`resume_processing_runs` established. All five are reused directly. Nothing
about the existing resume or profile schema changed to make room for this.

## The domain model

Four tables, three conceptual layers:

```
jobs                    canonical entity — raw_payload + normalized columns
job_processing_runs     one attempt at structuring, append-only
job_processing_events   per-stage timing (structure, materialise)
job_requirements        the validated structured reading, one row per run
```

`raw_payload` on `Job` is not the unvalidated HTTP body — every field in it
already passed type, length and URL-scheme checks — but it is untouched by
normalization: `employment_type` in `raw_payload` might read `"Full-Time"`
while the column of the same name next to it reads `full_time`. That gap is
`app/services/job/normalization.py`'s job, and keeping the two visible side by
side is what makes a later audit answer "what did the source actually say?"

## Job identity and deduplication

Not "the same URL." A URL alone conflates two real risks in opposite
directions: tracking parameters make two fetches of the identical posting look
like different jobs, while a generic careers-page URL that renders many
postings behind client-side routing would make different jobs look identical.

**Tier 1** — `(user_id, source_type, external_job_id)`, when the source gave
one. The strongest signal: a site's own id for a posting means the same thing
regardless of which query string fetched it.

**Tier 2** — `(user_id, source_type, canonical_url)`, when no external id
exists. `canonicalize_url` lowercases the host, strips known tracking
parameters (`utm_*`, `ref`, `trk`, `gh_src`, …), drops the fragment, and keeps
everything else — so two fetches of one posting through different marketing
links still converge.

Both are partial unique indexes, and both are scoped per user — the same
reasoning `resume_documents.uq_resume_documents_user_id_content_sha256` uses: a
global index would let one user's save collide with a stranger's, which is an
existence oracle wearing a conflict's clothes.

**`content_hash` is indexed but deliberately not a uniqueness tier.** Two
genuinely different postings can share boilerplate text, and a content-based
merge is exactly the cross-source conflation the domain must not do: "the same
job may legitimately appear on multiple sources" means LinkedIn and Indeed
copies of one real-world opening are two rows, not one, because they are two
different facts — two different pages, two different ids, potentially two
different fates if the user applies through one and not the other.

**Re-ingestion of an existing identity returns the first-seen version,
unchanged.** If the underlying posting text is edited after a user saves it,
resubmitting does not overwrite what was saved. This is the same choice
`ResumeDocument` makes for identical content, applied to identity rather than
bytes, and it is a real limitation — see below.

## Raw vs. normalized vs. structured

- **Raw** — `JobIngestionRequest`, the untrusted input schema. Validated but
  not interpreted: `employment_type` is free text here because the source
  site's own phrasing varies ("Full-time", "FULL_TIME", "Contract - W2").
- **Normalized** — `app/services/job/normalization.py`. Pure, deterministic
  functions: alias tables map site phrasing onto `Job`'s closed vocabulary,
  `canonicalize_url` strips tracking parameters, `compute_content_hash`
  fingerprints for future duplicate heuristics. No network call, no database
  read, no AI — and no interpretation: normalization decides what a field
  *means* only in the sense of recognizing a known spelling, never in the
  sense of inferring a fact the text does not state.
- **Structured** — `app/services/job/structuring.py`. `JobCandidate`, the
  requirements schema, produced by a `JobStructurer`. This is where "5+ years
  of backend experience" becomes a claim with a minimum, a citation, and a
  required/preferred classification — genuine interpretation, kept out of
  normalization on purpose.

An AI structurer's raw output, when one exists, will never become a database
row directly: it validates against `JobCandidate` first, exactly as resume
validates a model's output against `ResumeCandidate` before any row is
written from it.

## Job requirements and provenance

Nine categories, matching the domain's own list rather than collapsing into
one bag of keywords: `required_skills`, `preferred_skills`,
`required_experience`, `preferred_experience`, `education`, `certifications`,
`responsibilities`, `technologies`, `qualifications`. Only skills and
experience split into separate required/preferred lists at the top level —
that split is what the spec names explicitly. Everything else carries an
`is_required` flag per item, because a posting saying "Bachelor's required,
Master's preferred" is one section making two claims, not two sections.

### Where a location statement goes

`qualifications` carries them — "Based in Berlin", "Remote within Germany",
"Hybrid from Frankfurt", and anything under a `Location:` heading.

This needed saying out loud because a location line used to end up somewhere
much worse. It named nothing the classifier recognized, and the catch-all only
takes an unrecognized line when it has a comma, an " and ", 128+ characters or
a modal lead verb — so "Based in Berlin" failed every test and was filed as a
required **skill**. The matcher then answered *"No skill, project technology or
description in this profile version mentions based in berlin"* and the job lost
a required-coverage point. A confident miss, in incoherent words, on something
that was never a skill.

`_LOCATION_LEAD` in `app/services/job/structuring.py` now recognises the
phrasings, and `_LOCATION_HEADINGS` recognises the section — the latter
because "Location:" is heading-shaped, so the unknown-heading rule used to
close the open section and drop every line beneath it.

The recogniser demands a **locative continuation**: "remote within", "hybrid
from", "based in". A bare `^remote` or `^hybrid` would swallow "Remote
debugging experience" and "Hybrid cloud architecture", which are ordinary
competency statements that happen to start with the same word — trading a
visible bug for an invisible one.

**Workplace, eligibility and engagement lines go there too.** "On-site in
Munich 3 days a week", "Remote (US only)", "Right to work in the UK", "Visa
sponsorship is not available", "US citizenship required", "Active TS/SCI
clearance", "Must be at least 18 years of age", "6-month contract" all used to
reach the catch-all and become required skills, answered MISSING. Two further
recognisers file them as qualifications: `_WORKPLACE_LEAD` (anchored, with the
same locative-continuation discipline — "Remote-first culture" and "On-site
customer support" stay skills) and `_CONDITION_OF_EMPLOYMENT` (work
authorisation, clearance, age, screening, contract terms, travel, on-call,
in-office days; each entry is listed in the source with the competency it must
not swallow). They are separate from `_LOCATION_LEAD` on purpose: that pattern
is held identical to `matching.geography.LOCATION_LEAD`
(`tests/test_matching_location.py` asserts it), and what reaches the matcher's
location judge is the matcher's decision. Everything else in `qualifications`
the matcher answers UNCERTAIN, "needs a person to read" — honest, where the
skill filing was confidently wrong. The widening direction is deliberate: a
skill misfiled as a qualification becomes a visible UNCERTAIN; a condition of
employment misfiled as a skill becomes an invisible wrong MISSING.

Location lives in `qualifications` rather than a list of its own because
`engine.evaluate` iterates a fixed set of `JobCandidate` lists: a new
`locations` list it did not read would make those requirements vanish from a
match result entirely, shrinking the denominator, which is the one way a
coverage score misleads without any individual verdict being wrong. A
structured location item belongs with the engine branch that reads it.

As of `matching_v4` that branch exists: `engine.evaluate` tells a location
qualification apart from a work-authorisation one using
`geography.is_location_requirement` — a local copy of the same lead-ins that
filed it — and routes it to `rules.judge_location`. The representation did not
have to change for that, which is why it did not. See `docs/matching.md` for
the supported semantics and the longer list of what stays deferred.

### How the deterministic structurer finds sections

A known heading opens a section (`_REQUIRED_HEADINGS`, `_PREFERRED_HEADINGS`,
`_RESPONSIBILITY_HEADINGS`, `_LOCATION_HEADINGS`) or closes one
(`_OTHER_HEADINGS`: About …, Why …, Our values, Benefits, What we offer, Pay
transparency, Base salary, Total rewards, Tech stack, EEO, How to apply,
Physical requirements, Schedule, …). The required and preferred vocabularies
cover Amazon's "Basic Qualifications", "Required Skills", "Must-haves", "What
you'll bring", "Your profile", "Essential"/"Desirable", "Nice-to-haves", "Good
/ Great to have", "Desired", "Bonus points", "Bonus skills", "Would be nice",
"Even better if you have", "Stand out if you have", "Preferred but not
required"; the duties vocabulary covers "(Key/Main/Your/Essential)
Responsibilities/Duties", "What you'll be doing/working on", "Your role", "A
day in the life". Hyphen- and plural-tolerant, with typographic dashes and
apostrophes ("What you’ll bring") folded to ASCII before comparing. A missing
required heading is the expensive miss: it drops the required half and leaves
the preferred half, so the posting reads as asking only for its optional
items.

An UNRECOGNISED heading-shaped line (short, letters and spaces) no longer
closes a section by shape alone. Scraped `<li>` text has no bullet glyphs, so
under the old rule "Requirements:\nPython\nDjango\n…" was closed by "Python"
and produced nothing. It now closes the section only when it carries a
heading mark (`_closes_section`): a trailing colon; a leading `#`; capitals
("TOOLS OF THE TRADE", never a lone acronym, "CI/CD" or "AWS GCP"); a change
of list style (the item before it is bulleted and this line is not, or the
next line is bulleted and this one is not — for bulleted postings, exactly the
old rule, even after an unbulleted lead-in sentence); or a blank line after
two or more packed items. Otherwise it is an item. A wrongly fired mark costs
what the old rule cost — lines dropped, never misfiled.

That default has one dangerous case, found by review: an optional heading the
vocabulary does not know ("Bonus Skills", "Nice extras", "The Ideal
Candidate"), unmarked inside an unbulleted requirements list, became a
required item with every nice-to-have beneath it REQUIRED. So inside a
required or preferred section, a heading-shaped line whose FIRST word says
"optional" (`_OPTIONAL_CUE`: bonus, optional, nice, extra, ideal(ly),
preferred, desirable, desired, even better, stand out) opens PREFERRED, marked
or not; so do the bare "Plus", "Pluses", "Bonus", "Optional", "Extras" there
and under duties. Its worst case is
a required line read as preferred, never the reverse. First word only:
"Kubernetes a plus" and "Python preferred" are items carrying their own
verdict. If the next line is pay ("Bonus" then "Equity", "401(k) matching"),
the section closes instead. And a line that states pay ("The base pay range
for this role is $150,000 - $180,000.", "Pay range: $150k-$180k") ends the
section rather than being filed as a requirement (`_PAY_STATEMENT`).

`_OTHER_HEADINGS` is matched before any shape check, so its open-ended forms
("About …", "Why …", "Life at …") take name words only — letters, no digits,
a "." only in a web domain — and a line of 60 characters at most. The first
version took any word, and the requirement lines "Working with Node.js and
React" and "About 3 years of Django." closed their section and dropped what
followed. "Working with …" is only "Working with us".

Classification guards, each from an audited misfiling: an age ("18 years of
age") is not experience; a degree needs degree context ("Certified Scrum
Master", "Developer - Associate", "10 ms", "MS Office", "Scrum Master in an
agile team" are not degrees). Dotted B.S./M.S./B.A./M.A. stand alone; undotted
BS/MS are matched case-sensitively and only when followed by "in", "degree",
"(Hons)", a field of study ("BS Computer Science", "MS CS", "BS: Computer
Science"), a verdict word ("required", "preferred", "a plus"), or punctuation
or a joiner ending the mention ("BS/MS", "MS or equivalent"); after ", " MS
needs the explicit part of that context ("Jackson, MS" is Mississippi).
Undotted BA — also a business analyst — needs a field, "degree", "(Hons)",
"or equivalent" or another degree beside it, never "in" alone ("a BA in a
fintech team") and never after "as (a)". Undotted MA is never a degree
("Boston, MA"). "ts" is no longer a TypeScript alias ("TS/SCI" is Top
Secret); "rest", "react", "node" match only in their technology casing; "js"
does not match the suffix of "Next.js"/"Vue.js".

Every item carries `provenance: list[JobProvenance]` — `source_section` (the
heading it was found under), `quote` (verbatim, ≤400 chars), `confidence`, and
`verified` (server-set, never trusted from the structurer). `verify_job_provenance`
checks every quote against the job's actual description after structuring —
meaningless for the deterministic structurer, which can only quote text it
already read, but built now, before it is needed, exactly as resume verifies
its own AI's provenance rather than trusting it.

**Stored as JSONB on `JobRequirements`, not exploded into six child tables.**
Nothing in this milestone queries a single requirement row in isolation, and
the matching system this exists to feed will read the whole structure at once
to compare it against a profile — six tables today would be schema nobody uses
until a real query pattern justifies it. This is the one place this milestone
deliberately does *less* relational modeling than the resume domain, and the
justification is symmetry with resume's own `ResumeStructuredResult.payload`,
not an oversight.

## Processing state machine

Deliberately smaller than resume's: `queued → processing → {completed,
failed}`. Resume's extra states (validating, extracting, ocr_required,
ocr_processing, validating_result) exist because a binary file goes through
several genuinely failure-prone stages before structuring even starts. A job
posting arrives as text an extension already read out of the DOM — there is no
extraction step to fail — so the graph collapses to two real stages,
`structure` and `materialise`, each timed into `job_processing_events`.

**Processing is synchronous in this milestone.** `POST /jobs` runs the pipeline
inline, in the same request, and returns a job whose requirements are already
attached. No lease, no heartbeat, no reaper, no worker — none of resume's
async machinery exists here, on purpose: deterministic structuring is a regex
pass over text already in memory, sub-millisecond, with no external I/O to
wait on. Introducing a queue for that would be solving a latency problem that
does not exist yet.

`JobProcessingRun` exists anyway, with the exact shape a lease would attach to
— attempts, timestamps, failure codes, full history — because when a real AI
structurer makes this slow enough to move off the request thread, the
resume-domain pattern (lease, heartbeat, reaper, backoff) can be adapted onto
this table without changing `Job` or `JobProcessingRun` first. The column that
does not exist yet is `next_retry_at`; nothing about today's schema has to
change to add one.

`POST /jobs/{id}/reprocess` opens a new attempt (`attempt = max()+1`,
`UNIQUE(job_id, attempt)` as the backstop) and runs it inline. Two concurrent
reprocess calls race on that insert; the loser is told so —
`open_next_attempt` returns `(run, created=False)` — and reports the winner's
run rather than also driving it, because with no lease to arbitrate between
them, letting both call `pipeline.process_run` on the same row would let their
writes interleave with nothing stopping them.

A reprocess after a permanent failure is refused (409) — except for the
failures this deployment's configuration caused, `failures.CONFIGURATION_ERROR_CODES`
(`ai_not_configured`, `ai_unauthorized`, `ai_authentication_failed`,
`ai_billing_blocked`). Those are still permanent and never retried
automatically, but an operator can cure them, so a person asking is allowed to
find out whether they have; `can_retry` reports the same rule
(`is_user_retryable`) so the button and the endpoint agree.

After a failed reprocess, `GET /jobs/{id}/requirements` returns the newest
reading that exists — the same row matching and tailoring use — with `run`
set to the attempt that produced it. `job.latest_run` in the same response
still reports the failed attempt; `run.id != job.latest_run.id` is the signal
that a newer attempt did not replace the reading. The web job page fetches
requirements when `latest_run` is completed or failed, and on that signal
shows the older reading under a "Showing the last reading that succeeded"
notice — the same reading matching and tailoring use.

## API

| Method | Path | |
|---|---|---|
| `POST` | `/api/v1/jobs` | Ingest. 201 new, 200 existing (idempotent). |
| `GET` | `/api/v1/jobs` | List the caller's jobs. |
| `GET` | `/api/v1/jobs/{id}` | One job, including its description. |
| `GET` | `/api/v1/jobs/{id}/requirements` | The structured reading, or `null` if none yet. |
| `POST` | `/api/v1/jobs/{id}/reprocess` | A new attempt. |

Every route takes `CurrentUser` and nothing else for identity — the same seam
`app/api/dev_identity.py` already is for the rest of the API. No route accepts
a caller-supplied status or user id; `JobIngestionRequest` has no such fields,
and `extra="forbid"` rejects any payload that tries to add them, which is what
makes "never trust the client for identity or status" a fact about the schema
rather than a check someone has to remember to write in a handler.

## Validation and normalization

Everything crosses one edge: `JobIngestionRequest`. `source_type` is a closed
vocabulary we define (`linkedin`, `indeed`, `greenhouse`, `lever`, `workday`,
`company_careers`, `other`) and an unrecognized value is 422 — this is our own
fact, not the source site's. `source_url` must be `http`/`https`, non-empty
host, ≤2048 chars, no control characters — `javascript:`, `data:`, `file:` and
schemeless strings are all refused. `description_text` is capped at 50,000
characters, rejected outright over the limit rather than truncated, the same
posture resume takes on upload size: reject cleanly rather than truncate and
pretend the rest was never there. `job_title` is required and cannot be blank
after whitespace collapse. Everything else — company, location, employment
type, workplace type, salary — is optional; **unknown stays unknown**, never
guessed at.

Employment type, workplace type and salary period arrive as the source site's
own free text and are mapped onto closed vocabularies by
`normalize_employment_type` / `normalize_workplace_type` /
`normalize_salary_period`. An unrecognized phrase becomes `None`, never a
guess — "Contingent Worker Category 7" does not become `contract` because it
sounds vaguely like one.

## Security

A job posting is untrusted external content, treated the same way resume
treats a candidate's uploaded file:

- **Oversized payloads** are rejected at the schema boundary before
  normalization or structuring ever run.
- **Malicious HTML / script content** is never rendered as markup anywhere in
  this application — not sanitized, because there is nothing to sanitize:
  `description_text` is stored and returned as plain text, always, and the
  frontend renders it inside a `<pre>` block, never `dangerouslySetInnerHTML`.
  A `<script>` tag in a description is just characters that do not match any
  heading pattern to the deterministic structurer.
- **Control characters** are stripped — aggressively from short labels (title,
  company, location), narrowly (NUL and other C0 controls only) from the
  description, which needs to keep reading as the posting it came from.
- **Path traversal** does not apply: there is no filesystem storage in this
  domain. `raw_payload` lives in JSONB.
- **Prompt injection** is documented now, ahead of needing it: no AI reads a
  job description in this milestone, and `structuring.py`'s module docstring
  states the rule a future provider's prompt must follow — a job description
  is DATA, never instructions, placed in a clearly-delimited field, never
  concatenated into anything the model could read as a command. A job posting
  authored specifically to be scraped by an application like this one is a
  more plausible adversary than a résumé ever was, and the boundary is written
  down before there is a prompt to audit against it. The provider prompt
  (`PROMPT_VERSION` 2026-09-25.1) also neutralises closing tags of its own
  fences inside the data — "</job_description>" in a posting becomes
  "<\/job_description>" — so the posting cannot end its fence early and put
  text where the prompt's own words are (`_fence_safe`, the same rule
  tailoring applies to its fences).

## What this milestone does not do

- **No AI structurer.** `get_job_structurer()` always returns
  `DeterministicJobStructurer`. The seam — `JobStructurer` Protocol,
  `JobStructuringResult`, `verify_job_provenance` — is built and tested; a
  provider is the next milestone's work, not this one's.
- **No re-ingestion merge.** A job resubmitted under the same identity with
  updated text does not update the stored row. Revisiting this needs a
  versioning decision (keep every version? diff and flag what changed?) that
  is out of scope here.
- **No async processing.** Correct today because structuring is fast and
  local; wrong the day a real model call is on the critical path. The run
  table is shaped for that migration; the queue/lease/worker code is not
  written yet.
- **No matching.** `JobRequirements` is built to be read by a matching system,
  not to contain one.
