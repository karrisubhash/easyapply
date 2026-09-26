# Asynchronous resume processing

Processing used to happen inside the upload request. A resume that needed OCR
and a model call held an HTTP connection open for as long as it took, and a
process restart in the middle lost the work with no record that it had ever
started.

This document describes the seam that replaced it. No broker, no orchestrator,
no cluster — those belong to a later phase, and the point of doing this first is
that introducing them later should be a change of transport rather than a
redesign of the domain.

## The shape

```
POST /resumes                          worker
  validate the bytes                     claim the run (lease)
  store the artifact                     run pipeline.process_run
  create a processing run                record stage events
  enqueue                                create the draft profile version
  return  ──────────────────────────▶    mark completed / failed
                                         release the lease
                                         open a retry if policy allows
```

`pipeline.process_run(db, *, run_id=...)` is unchanged. It always took an
integer and re-read everything else, which is what made it the async seam; the
worker is what finally uses it that way.

## The run table is the queue

There is no queue table. A run that is not terminal and not leased **is**
outstanding work. Anything else would be a second list of what remains to be
done, free to disagree with the first.

That makes enqueueing advisory. Losing a message costs latency, not work — the
run is still in the table for a poll or the reaper to find. It is also why the
run is committed *before* it is enqueued and never the other way round: a worker
handed an id whose row is not yet visible would find nothing and conclude the
work was gone.

## The lease

| Column | Meaning |
|---|---|
| `lease_owner` | Which worker holds it. Host + pid + random, so two replicas of one image cannot collide. |
| `leased_until` | When the claim lapses. A worker that has not renewed by then is treated as gone, whatever it believes. |
| `heartbeat_at` | Last renewal. Kept after release, so "when did this last show a sign of life" outlives the lease. |

Ownership lives in the row because the question *"is anyone still working on
this?"* has to be answerable by a process that is not the one working on it —
and by one that started after the original worker was killed. A mutex or an
in-process set answers the first question and not the second: after a crash they
simply forget, and the run is stuck forever.

Acquisition is one statement:

```sql
UPDATE resume_processing_runs
   SET lease_owner = :owner, leased_until = :deadline, heartbeat_at = :now
 WHERE id = :id
   AND status NOT IN ('completed', 'failed')
   AND (leased_until IS NULL OR leased_until <= :now OR lease_owner = :owner)
```

`rowcount` is the verdict. No read, no gap, no window in which two workers both
believe they are first. The third disjunct makes it re-entrant for the current
owner, so a worker that crashed after claiming can pick its own run back up
rather than waiting out a lease it already holds.

Leases never touch `status`, which the state-machine trigger treats as progress
within a state rather than a transition — so the lease and the state machine do
not have to know about each other.

### Heartbeat

A background thread renews on its own session, because the pipeline is using the
worker's and a SQLAlchemy `Session` is not safe to share. Renewal carries the
owner in its `WHERE` clause, so it cannot renew a lease that has been taken away
— and a failed renewal is the signal that matters: it means the run was reaped
while we were working on it, and this worker no longer gets to decide anything
about it.

Default: a 300-second lease renewed every 60. Comfortably inside, so one missed
beat is survivable.

## The reaper

One question only: which runs have stopped making progress, and what does the
state machine say happens to them? It is not a scheduler and must not grow other
jobs.

Three shapes, recovered differently because they mean different things:

| | Condition | Recovery | Why |
|---|---|---|---|
| **Abandoned** | lease exists and has expired | fail with `worker_abandoned`, then apply the retry policy | A worker claimed it and died. Partial work exists and the attempt has to be closed honestly. |
| **Orphaned** | never leased, no retry time, older than the grace period | re-enqueue, unchanged | The enqueue was lost. Nothing was attempted, so there is nothing to fail and no attempt to burn. |
| **Due** | a scheduled retry whose backoff has elapsed | offer it to the queue, unchanged | It was created un-enqueued on purpose. Noticing by query rather than by timer is what lets a backoff survive a restart. |

The first distinction is why an infrastructure restart does not silently consume
one of a document's attempts. The third is what makes a persisted delay into a
real one: no process holds a scheduled retry, so no process can lose it.

Nothing is written for a **Due** run — the row already says which attempt it is
and when it became eligible, so the sweep only tells the queue it exists.
Re-offering one that is still queued is harmless: the queue deduplicates what it
holds and the lease arbitrates whoever arrives.

**Recoverable states:** every non-terminal status. `completed` and `failed` runs
are the record; a reaper that could rewrite them could rewrite history.

**Recovery never reopens a run.** The abandoned run stays failed forever and a
new attempt starts beside it, so the table says `attempt 1 abandoned, attempt 2
completed` rather than pretending the first never happened.

Several reapers may run at once. Each recovery goes through the same atomic
acquisition a worker uses, so two racing over one run end with exactly one
recovering it.

## Retry policy

A retry is only worth making when the same inputs could plausibly produce a
different outcome. `app/services/resume/failures.py` is the one place that split
is written down.

**Transient — retried.** The document was fine and something around it was not.

`stored_object_missing`, `worker_abandoned`, `unexpected_error`,
`draft_creation_failed`, `ai_provider_unavailable`, `ai_timeout`,
`ai_rate_limited`, `ai_provider_error`, `ai_output_malformed`.

**Permanent — not retried.** The document, or our reading of it, is the problem,
and the same bytes through the same code reach the same conclusion.

`empty_file`, `file_too_large`, `unsupported_file_type`, `extension_mismatch`,
`media_type_mismatch`, `missing_filename`, `filename_too_long`,
`unsafe_filename`, `malformed_pdf`, `malformed_docx`, `document_encrypted`,
`empty_document`, `unsupported_media_type`, `no_text_extracted`,
`document_too_large`, `ocr_unavailable`, `rasterise_failed`, `ocr_failed`,
`result_schema_invalid`,
`ai_not_configured`, `ai_unauthorized`, `ai_request_invalid`,
`ai_content_rejected`, `ai_output_invalid`, `structuring_failed`.

**Unrecognised codes are treated as permanent.** That is the deliberate
direction to be wrong in: a new failure mode retried three times is three times
the damage and no new information, whereas one that is not retried is visible,
carries a code, and is reclassified in one line.

Two failures worth explaining because they look alike:

- `ai_output_malformed` — the model could not produce parseable JSON, usually a
  truncation. Sampling makes the next attempt genuinely different. **Transient.**
- `ai_output_invalid` — it produced JSON that does not fit the schema. That is
  the model disagreeing with the schema, and it will disagree again.
  **Permanent**, and a signal for a person to look at the prompt.

### Backoff

A retry does not run immediately. `schedule_retry` creates the successor with
its own `next_retry_at`, and that column is the entire mechanism: no sleeping
worker, no delayed message, no timer anywhere.

```
delay  = min(BASE * MULTIPLIER ** (attempt - 2), MAX)
waited = delay * (1 - JITTER) + random(0, delay * JITTER)
```

| Setting | Default | |
|---|---|---|
| `RESUME_RETRY_BACKOFF_BASE_SECONDS` | `30` | what the first retry waits |
| `RESUME_RETRY_BACKOFF_MULTIPLIER` | `2.0` | |
| `RESUME_RETRY_BACKOFF_MAX_SECONDS` | `900` | the ceiling |
| `RESUME_RETRY_JITTER` | `0.5` | fraction of the delay that is random |

Jitter is **subtractive**, so the formula is a ceiling rather than a midpoint:
the delay always lands in `[delay x (1 - JITTER), delay]`. That makes the bound
assertable instead of merely probable, and means turning jitter up can never
turn a 15-minute maximum into a 30-minute one. `0.0` is deterministic, `1.0` is
full jitter.

Jitter exists because the failures worth retrying are usually *shared*: one
provider outage fails every in-flight resume at the same instant, and without
jitter they would all wake at the same instant, hit the recovering provider
together, and fail together.

The lifecycle, with no new processing status:

```
failure
  |- transient? --no--> skipped     (permanent, or unclassified)
  |    yes
  |    |- budget left? --no--> exhausted   (needs a person)
  |         yes
  |         |- scheduled   run N+1 created, next_retry_at = now + backoff
  |              |- eligible    the reaper offers it once that passes
  |                   |- the attempt runs
```

A run that is `uploaded` with a future `next_retry_at` **is** "queued, not yet
due". Inventing a state to say so would put the same fact in two places.

Three queries enforce it, and all three had to change together - a claim path
that forgot the check is a retry that runs early:

* `lease.acquire` - the statement that actually decides. Anything holding a run
  id may call it, including a stale message that outlived a restart, so the row
  itself does the refusing.
* `lease.claimable_run_ids` - the queue read, so a polling worker does not spin
  on runs it will then be refused.
* `lease.unclaimed_runs` - excludes anything with a retry time, so the orphan
  grace period cannot silently override a configured backoff.

An expired lease is not a shortcut past the wait: recovery fails the abandoned
run and asks the retry policy for a successor, and `worker_abandoned` is a
transient code like any other.

### Retry safety

* **Two workers cannot schedule the same retry.** The successor's number is
  `failed_run.attempt + 1`, not `max()+1`, so two processes reacting to one
  failure compute the same number and `UNIQUE(resume_document_id, attempt)`
  lets exactly one insert win. `max()+1` would let them compute 2 and 3 and both
  succeed - two retries for one failure, and an attempt silently burned.
* **Two workers cannot execute the same retry.** Unchanged: one atomic UPDATE.
* **A restart loses nothing.** The delay is a row. Nothing holds it in memory,
  so nothing can drop it.
* **The ceiling holds under races**, because it is counted from the rows.

### Attempts

`RESUME_MAX_ATTEMPTS` (default 3) bounds **automatic** retries, counted from the
document's existing runs rather than a counter that could drift.

A person clicking "try again" is not bounded by that ceiling. They have
information the system does not — the outage is over, the provider is back — and
one click is not a runaway. What they cannot do is retry a failure the same
bytes will reproduce; the API refuses that with a 409 and the UI does not offer
the button, because a retry that cannot succeed is worse than an honest refusal.

A permanent failure the **deployment** caused is not one of those.
`failures.DEPLOYMENT_ERROR_CODES` — `ocr_unavailable`, `ai_not_configured`,
`ai_unauthorized`, `ai_authentication_failed`, `ai_billing_blocked`, and the
codes that mean a bug of ours (`structuring_failed`, `result_schema_invalid`,
`ai_request_invalid`, `ai_output_invalid`) — stay permanent to the automatic
policy, since nothing about them changes on its own, but a person may retry
them (`may_retry_manually`, and `can_retry` on the run). Refusing them wedged
the document: re-uploading the same bytes resolves to the same document, so
"upload a corrected file" had no file to correct once the engine was installed
or the key rotated.

## Running it

### Default — in-process pool

```powershell
uvicorn app.main:app --reload     # RESUME_QUEUE_BACKEND=thread
```

The API returns as soon as the run is committed and a pool thread picks it up.
Asynchronous without any infrastructure.

### Separate worker

```powershell
# terminal 1 — the API queues and nothing else
$env:RESUME_QUEUE_BACKEND = "external"; uvicorn app.main:app

# terminal 2
python -m app.worker
```

The API does not import the worker, the worker does not import the API, and the
only thing they share is a table. This is the deployment shape a hosted queue
arrives into: `_claim_and_run` becomes a message handler and nothing else
changes.

## AI provider boundary

```
ResumeService → pipeline → ResumeStructurer → StructuredOutputProvider → vendor SDK
```

`ProviderResumeStructurer` owns the prompt and the schema it asks for. The
adapter owns the vendor. Neither knows the other's job, so swapping providers is
a new class in `providers/` and one configuration value.

Free-form output is not a mode this boundary offers: `complete()` takes the model
the response must satisfy and returns a validated instance or raises. There is
no path that hands unparsed text to a caller.

Vendor exceptions are translated into the codes above. They are never passed
through, because a vendor's message quotes the request — and the request is the
candidate's resume.

### Configuration

| Setting | Default | Notes |
|---|---|---|
| `RESUME_AI_ENABLED` | `false` | The master switch. Off means the deterministic structurer runs. |
| `RESUME_AI_PROVIDER` | `anthropic` | Which adapter satisfies the protocol. |
| `RESUME_AI_MODEL` | `claude-opus-5` | |
| `RESUME_AI_MAX_TOKENS` | `16000` | |
| `RESUME_AI_TIMEOUT_SECONDS` | `120` | |
| `RESUME_AI_MAX_RETRIES` | `2` | SDK-level transport retries, beneath our own policy. |
| `RESUME_AI_THINKING` | `true` | Adaptive thinking. |
| `ANTHROPIC_API_KEY` | — | Server-side only. |

There is deliberately **no temperature setting**. The Claude models this adapter
targets reject `temperature`, `top_p` and `top_k` outright, so offering the knob
would be offering a 400. Depth is controlled by adaptive thinking instead.

The key is read only by the API process. It is not in `apps/web`, not forwarded
by the Next proxy, and not present in any response or any generated type.

### Auditability

Every structured result records `structurer`, `provider`, `model`,
`prompt_version`, `schema_version` and `provider_metadata` — token counts, stop
reason, latency, response id. Enough to answer "what produced this, and would
the same inputs produce it again?" long after the model has been retired.

`provider_metadata` holds counts, ids and timings only. Never the prompt, never
the completion, never a credential — it is read by whoever audits a result, and
it is the obvious place for a secret to end up by accident.

## Evaluation

`evaluation/` exists before any prompt tuning, on purpose: changing a prompt
without a measurement is how a change that helps two resumes and breaks five
gets shipped feeling like progress. See `evaluation/README.md`.

## Observability

Structured events, every one carrying `resume_run_id` and
`resume_document_id`:

`resume.queue.enqueued` · `resume.queue.duplicate` · `resume.run.created` ·
`resume.run.acquired` · `resume.run.claim_refused` · `resume.run.heartbeat` ·
`resume.stage` (started / succeeded / failed) · `resume.run.completed` ·
`resume.run.failed` · `resume.run.lease_lost` · `resume.run.recovered`

The four retry events, each carrying `resume_document_id`, `resume_run_id`,
`attempt` and `failure_code`:

| Event | Means |
|---|---|
| `resume.retry.scheduled` | a successor exists; also carries `next_retry_at` and `delay_seconds` |
| `resume.retry.eligible` | its wait is over and it has been offered to the queue |
| `resume.retry.skipped` | not retryable, or another process already opened this attempt |
| `resume.retry.exhausted` | worth retrying, budget spent - **this one needs a person** |

Stage events are rows in `resume_processing_events`, not log lines, because the
questions they answer — which stage failed, how long each took — are queries.
Failure events carry the classification, so "was this worth retrying?" is
answerable from the log without re-deriving it.

**No resume text, no prompts, no model output ever reaches a log.** Error
messages come from a fixed vocabulary written by us; parser and vendor libraries
quote document content in theirs, and that content is the user's personal data.
Verbatim text lives in exactly one table, `resume_document_pages`, so a routine
read of a run cannot drag it into a trace.


## Operator view

`/api/v1/internal/resume-processing` answers one question: which resume
processing needs a person.

Every state is **derived** from `resume_processing_runs`. There is no operator
status column and no job keeping one in step, because the runs already record
every fact the view needs and a second copy of a conclusion is a second place
for it to be wrong.

| State | Means | Action |
|---|---|---|
| `completed` | produced a draft | none |
| `processing` | running, or queued and due | none |
| `waiting_retry` | scheduled, backoff not elapsed | none - this is the system working |
| `exhausted` | last failure was retryable, budget spent | **a person** |
| `failed_permanent` | the document itself cannot be processed | tell the user |
| `stalled` | non-terminal, past due, nobody holding it | check the workers |

`exhausted` versus `failed_permanent` is the distinction that earns its keep.
Both are "failed and not retrying", and conflating them sends an operator to
hunt a provider outage when the real answer is that someone uploaded a
photograph of their CV.

Three endpoints: `/attention` (filtered to the three actionable states, with
counts across all six), `/documents/{id}` (every attempt), `/runs/{id}` (one
attempt with stage timings). A minimal page at `/internal/resume-processing`
renders them; it is not in the navigation, because a visible link would imply an
access control that does not exist yet.

### The authorization boundary

`app/api/operator_identity.py` is one named seam, the way `dev_identity.py` is
for user identity. **It is not production-secure and does not pretend to be.**

| `OPERATOR_API_ENABLED` | `OPERATOR_API_TOKEN` | Behaviour |
|---|---|---|
| `false` | - | 404 on every operator route - a disabled admin surface should not confirm it exists |
| `true` | set | `X-Operator-Token` must match, compared in constant time, else 403 |
| `true` | unset, `ENVIRONMENT=development` *(dev default)* | **open**, and every request logs `operator.unauthenticated_access` |
| `true` | unset, any other `ENVIRONMENT` | **403** on every request, logged as `operator.access_denied` with the missing setting — a deployment that forgot the token fails closed |

What it does not do: there are no operator accounts, so a token is a password
shared by everyone holding it, with no identity, no revocation and no answer to
"who looked at this?". The Next proxy attaches the token server-side for
`/api/backend/internal/*` only - that keeps it out of browser JavaScript, but
anyone who can load the web app can still call that path and have it attached
for them. **The web app is not a second authorization boundary.**

Because of that, the endpoints expose ids, timestamps, counts and error codes
and nothing else. The data minimisation is doing the work the authorization
cannot do yet, and it should stay that way after real authorization lands.

Absent by construction, not by filtering: `original_filename` (routinely the
candidate's own name), `storage_key`, `content_sha256` (a content fingerprint
is an existence oracle across users), `users.email`, page text,
`ResumeStructuredResult.payload` - which is a *second* verbatim copy of the
resume, inside every provenance quote - `unmapped`, `error_message` (an
unconstrained Text column, server-authored only by convention), and the lease
owner. `provider_metadata` is projected through an allowlist of token counts,
latency and stop reason; `response_id` is excluded because it is a handle into
the vendor's own record of a request whose body was the resume.

None of the operator schemas sets `from_attributes`, and each response is built
field by field. With it, a field later named `pages` or `payload` would
lazy-load and serialise resume text with no query anywhere saying so.
