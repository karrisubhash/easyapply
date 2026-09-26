# The LLM tailoring layer

What it takes for a model to write a resume this application is willing to hand
over, and why the flag that switches it on is still off.

The companion to `docs/resume-tailoring.md`, which describes what stops a
tailored resume saying something false, and `docs/tailoring-quality.md`, which
describes what stops it being useless. This one is about the generator neither of
those documents was written for: `ProviderTailor`, wired since the domain
shipped, never measured, and — as this work found — not actually able to succeed.

---

## Where it started

`ProviderTailor` existed. It had a prompt, an adapter, an error vocabulary, and
one test asserting it returns what the provider gave it. Both companion documents
end with the same line: *the AI generator is still unmeasured and still off*.

Three things were missing, and only the first is the one anybody expects.

### It was unmeasured

Nothing had ever run a model against this domain and counted anything. "Should we
enable it" therefore had no answerable form, and the flag stayed off by default
— correctly, but for want of evidence rather than because of any.

### It could not really pass

The prompt told the model to cite the profile text each line came from and to
"name the field it came from in `source_field`". It never said what the legal
field names are.

`grounding._check_line` resolves `source_field` against a dict of exact column
names — `description`, `job_title`, `company`, `location` on an experience;
`description`, `name`, `role` on a project; `summary` on the version. A model
writing `"experience.description"`, or `"bullet"`, or `"profile"` hands over a
**correct quotation the validator cannot find a haystack for**, and it is
reported as `REASON_QUOTE_NOT_FOUND` — a fabricated citation. The honest failure
and the dishonest one were indistinguishable, and only one of them was likely.

The profile block made it worse. It printed a job's title and company in an
unlabelled pipe-separated header:

```
  source_id=101 | Senior Backend Engineer | Northwind Logistics
```

So a model quoting a job title had nothing to copy a field name from. The rule
the prompt states — *`source_field` is the label the text was printed under* — was
not true of the data it was stated about.

Two smaller gaps in the same place: `targeted_requirements` is a field on the
schema, checked by grounding and displayed on the review screen, and the prompt
never mentioned it; and experience dates were shown without their precision,
which the renderer reads directly and replaces with a month when it finds
nothing.

### One bad line cost the whole document

The pipeline's verdict is all-or-nothing by design, and for the deterministic
generator that is exactly right: it copies clauses out of the snapshot, so a
grounding error from it is a bug in our selection code.

A model does not fail that way. It fails on the twenty-ninth line out of thirty.
Under the pipeline as it stood, twenty-eight verbatim bullets and one invented
number produced **no file at all** — and the feature the user asked for was
withheld over a sentence that could simply have been deleted.

---

## The design

Four decisions, in the order they matter.

### 1. A narrowing pass between the model and the verdict

`app/services/tailoring/reconciliation.py` is new. Its entire contract is one
sentence:

> It can only ever remove a claim, or restore a copied field to exactly what the
> snapshot says. It can never add one.

That is asserted rather than argued.
`test_a_repair_can_only_ever_remove` walks the repaired document and checks every
line, skill, technology, entry and link against the original, and checks that
every transcribed field that changed changed *into* the snapshot's value.

The two halves are different on purpose:

- **Remove** what cannot be supported: a line whose citation is not in the
  profile, a line stating a number or naming a thing the snapshot does not
  contain, a skill the profile does not list, a technology not on that project, a
  link the version does not hold, an entry whose `source_id` resolves to nothing.
- **Restore** what was transcribed wrongly: a company name, a job title, a
  degree, a date, a date's precision, a contact detail. These are not the
  generator's to write, and the snapshot holds the right answer. Dropping the job
  because its name was typed with "Ltd" on the end would remove a true thing from
  the resume to punish a transcription error.

It is **driven by grounding's own findings**, not by a second opinion: it reads
the ERROR findings `validate` produced and addresses exactly those. A repairer
with its own notion of "grounded" is a second validator that will eventually
disagree with the first, and the disagreement would always resolve in the
document's favour, because this is the one that runs last.

Grounding then runs again, unchanged, on the narrowed document and still decides.
So an address the repairer does not understand is harmless — the finding survives
re-validation and the document is withheld. The parser fails in the only
direction it may.

### 2. A repair that does not produce a sendable document is not applied

If the second validation still fails, the repair leaves no trace: the stored
document is what the model wrote and the findings are the reasons it was refused.

A run that is going to withhold the artifact anyway should show the user what was
actually written. A half-repaired version of a document nobody can send explains
neither what was written nor why it was refused, and it would break the one
property worth keeping in both branches: **the stored payload is the document the
verdict is about.**

### 3. Every repair is a warning the user sees

Each removal and restoration becomes a `TailoringIssue` with severity `warning`,
stored beside the grounding findings and counted in `warning_count`. A document
six lines shorter than the model wrote is a document the user should be told
about, in the same list and the same shape as everything else found wrong with
it.

There is deliberately **no threshold** on how many repairs are too many. A
document whose every claim traces to the profile is a document we stand behind
however much narrowing it took, and a number chosen without evidence is a number
that will be wrong. The evidence goes in the evaluation instead, where the
release criteria cap the mean at three per document.

### 4. Nothing is repaired for the deterministic generator

`reconciliation.settle(..., repair_allowed=run.generator == "ai")`. The asymmetry
is the point: a grounding error from the generator that copies verbatim is a
defect in our own code, and repairing it would hide a bug from ourselves.

---

## Smaller changes, each at its own layer

| Layer | Change |
|---|---|
| prompt | `PROMPT_VERSION_AI` → `tailoring_ai_v2`: the legal `source_field` values named exactly; `targeted_requirements` explained; the shape of a tailored resume stated (keep every job, the bullet caps, no sentence printed twice, the skills group the renderer and the dataset both look for); "write fewer lines rather than weaker ones" |
| prompt data | `_profile_block` prints every quotable value under its own label, so "quote the label" is true of the data; experience dates carry their precision |
| prompt data | `tailoring_ai_v3`: skills are listed under their category as a heading, each bare name on its own `- ` line, and a contact link is a separate `url:` and `label:`. v2 printed `Python (Languages)` and `url (LinkedIn)`, the model copied both verbatim, and in the run2 evaluation 88 of 92 repairs were valid skills removed for carrying their category (3 more were labels restored from `(LinkedIn)`) |
| prompt data | `tailoring_ai_v4`: the requirement list says what its `- ` is, as the SKILLS header already did, and the `targeted_requirements` instruction says to copy the text after it. The labels were always printed as `- Kubernetes`; in the run3 evaluation two of six documents copied the marker into every targeted requirement (12 of 31), which grounding flags as a requirement the posting does not list. The labels, grounding and scoring are unchanged |
| prompt | `tailoring_ai_v5`: everything taken from the posting sits inside a fenced block marked untrusted, and a closing tag of one of our own fence names inside data is neutralised (`generation._fence_safe`) — a posting containing `</job_posting>` used to close the fence early, and the match-derived emphasis labels used to follow the fence as if they were instructions. A supplied match now arrives as a fenced `<requirement_support>` block (evidenced / uncertain / not evidenced) with rules: lead with the evidenced, never claim or target the not-evidenced. The prompt now states that the posting describes the employer's wants, not the candidate; that a skill is a SKILLS name or a project technology, never a word from prose; the three legal skill-group headings; that the headline is a stored job title copied exactly; and that a duration is answered only by an experience or project line. The deterministic generator ignores all of it |
| pipeline | a run the service classifies as stalled, whose row no worker holds (`SELECT … FOR UPDATE SKIP LOCKED`), is failed as `worker_abandoned` instead of blocking Generate for that job and version forever; a match whose profile version or requirements row differs from the run's is a 409; the worker checks that version, job, requirements and match all belong to the run; the attempt-number race is resolved instead of a 500; the failed stage is read before rollback |
| prompt | `tailoring_ai_v6`: "Every job keeps at least one bullet" — v5 gave an irrelevant job an upper bound only, and a model obeying it with zero bullets tripped grounding's unrepairable no-bullets error, withholding the document; the fence neutraliser also catches closing tags written with a space or an invisible character inside, or with full-width `<`/`/`. Letter-for-letter homoglyphs are not caught; grounding is the defence that does not depend on spelling |
| rendering | contact links are real hyperlinks (an external relationship to the full URL) whose visible text is the address; a single education or certification date prints as itself rather than "– Present"; XML-illegal control characters are cleaned; core properties carry the candidate's name, with the template's text fields cleared and its 2013 created/modified stamps removed (the template's app.xml and thumbnail still ship); a link whose url holds a control character is printed but not linked, and an entry header with nothing printable is not written — both used to make an approved document a permanent `render_failed`; the spacer paragraph is gone; every zip entry carries one fixed timestamp, so rendering one document twice gives the same bytes (and the same content-addressed artifact key) whenever it runs. Every rendered file is re-opened and verified before it is stored (`rendering.verify`), and a file that fails is `render_failed` |
| persistence | `tailoring_runs.provider` and `provider_metadata` (counts, ids and timings only), mirroring the job and resume runs — migration `d4a7c2e9b815` |
| grounding | date precision is checked as a copied field where omitting it is an alteration too — the renderer prints a month when given none, so an unstated precision is a month nobody wrote. Experience precision was not checked at all |
| generation | `get_tailor(generator)` takes the run's recorded generator instead of re-reading the flag; `GenerationResult` carries the provider's token counts and latency; a provider returning the wrong type is a `GenerationError` rather than an unvalidated document |
| service | `select_generator(user_id)` is the whole rollout policy, called once per run and recorded on the row |
| pipeline | the validate stage is `reconciliation.settle`; `generator` is never written back from the generator's own self-description |

### The run's generator column is now obeyed

It used to be advisory: `start_run` wrote it from the flag, and the pipeline
re-read the flag when the worker picked the run up and overwrote the column with
whatever had actually run. A flag flipped in between produced a row that
disagreed with its own document — in the one column a reader consults to find out
how a document was produced.

Now the column is written once by policy and obeyed by everything downstream:
`get_tailor` chooses from it, `settle` decides from it whether repair applies,
and nothing writes it back.

---

## How it is measured

`evaluation/tailoring_runner.py`, against the existing `tailoring_v1` dataset —
one real profile read by the real resume pipeline, four real postings the
candidate stands in a different relation to.

```
python -m evaluation.tailoring_runner                        # the baseline
python -m evaluation.tailoring_runner --tailor ai --samples 5
```

It runs `reconciliation.settle`, the same sequence the pipeline runs, rather than
a reimplementation of it: a measurement of a slightly different sequence is a
measurement of nothing. Five things are counted per document, and they are not
equally weighted — `delivered`, `safety` (counted, never rated), `selection`
against the frozen per-case expectations, `repairs`, and `shape`.

A model is sampled, so this is a script that reports numbers for a person to
read, not a test. The deterministic path stays a test, because it is
reproducible.

**The harness checks itself first.** `--tailor deterministic` must score 1.0 on
every measure — that generator selects verbatim and is already asserted case by
case in `test_tailoring_evaluation.py`, so anything less means the harness is
measuring the wrong thing and no number it reports about a model means anything.
It does:

```
case                 n  deliv  errs  rep  select  shape  bad
--------------------------------------------------------------
backend_senior       1   1.00   0.0  0.0    1.00   1.00    0
frontend_react       1   1.00   0.0  0.0    1.00   1.00    0
platform_k8s         1   1.00   0.0  0.0    1.00   1.00    0
sre_health           1   1.00   0.0  0.0    1.00   1.00    0
```

### The release criteria

`evaluation/datasets/tailoring_v1/ai_release_criteria.json`, authored **before**
any live run and not to be edited to match one — the same discipline `cases.json`
is held to. If a run disagrees with a line in it, the question is whether the line
was wrong about what a sendable resume needs, never whether the number can move.

| | | |
|---|---|---|
| forbidden strings | 0 | counted, not rated: `frontend_react` is a backend engineer against React, JavaScript and Jest |
| fabricated numbers | 0 | every digit-bearing token in a delivered document appears in the profile |
| wrong contact details | 0 | the repair pass restores these, so one miss means that pass is broken |
| delivered | ≥ 0.95 | below this, "we could not produce one" more than once in twenty is an experiment, not a default |
| career whole and in order | 1.0 | a missing job is a gap the reader asks about |
| no duplicate lines | 1.0 | cheap for any generator to avoid |
| selection expectations held | ≥ 0.9 | the deterministic generator holds all of them; better wording does not buy back worse selection |
| repairs per delivered document | ≤ 3.0 | above this the user is receiving our narrowing of the model's output rather than the model's output |

### The runs, 2026-09-23

Both halves were run at `--samples 5`. One of them produced numbers.

**Deterministic — 20 documents, meets every criterion.**

```
case                 n  deliv  errs  rep  select  shape  bad
--------------------------------------------------------------
backend_senior       5   1.00   0.0  0.0    1.00   1.00    0
frontend_react       5   1.00   0.0  0.0    1.00   1.00    0
platform_k8s         5   1.00   0.0  0.0    1.00   1.00    0
sre_health           5   1.00   0.0  0.0    1.00   1.00    0
```

| per job | selection checks | shape checks | repairs | violations |
|---|---|---|---|---|
| `backend_senior` | 23/23 | 3/3 | 0 | 0 |
| `frontend_react` | 8/8 | 3/3 | 0 | 0 |
| `platform_k8s` | 19/19 | 3/3 | 0 | 0 |
| `sre_health` | 10/10 | 3/3 | 0 | 0 |
| **total** | **300/300** | **60/60** | **0** | **0** |

Identical across all five samples per case, which is what a generator that
selects verbatim should be. The harness self-check the criteria demand is
therefore satisfied: any number it later reports about a model means something.

**AI — not measured. No provider credentials are configured.**

```
  generator failed 20x: ai_not_configured
```

All 20 attempts stopped in `AnthropicProvider._get_client`, which checks
`settings.ANTHROPIC_API_KEY` before constructing a client. No HTTP request was
made and nothing was spent. There is no `ANTHROPIC_API_KEY` in the environment or
in `.env`, no `ant` CLI on this machine and no stored credential profile.

The gate reports `delivered 0.000` and `nothing measured` for the four rates that
need a delivered document — which is the correct reading of a run that produced
none, and is not evidence about a model either way.

**So `TAILORING_AI_ENABLED` stays off**, for the same reason as before and with
one less unknown: the harness is now known to be sound, and the only thing
missing is a key. Set `ANTHROPIC_API_KEY` and re-run the second command above to
fill in the AI half.

---

## How to switch it on

Two switches, and the narrow one exists because of what the wide one costs to be
wrong about.

```
TAILORING_AI_ENABLED=true          # everybody, every run
TAILORING_AI_USER_IDS=7,9          # these users, while the flag stays off
```

`TAILORING_AI_USER_IDS` is empty by default, which is identical to not having it.
It exists for the interval between "the layer is built" and "we know how often it
works": the alternatives are to turn the flag on globally to try it — an
experiment run on every user at once — or never to try it outside the evaluation
dataset, which makes the first real profile a model sees everyone's.

It is **not an authorization boundary** and nothing may treat it as one. It
decides which generator writes a document, never who may read one.

Both are read in exactly one place, `service.select_generator`, once per run.

---

## What is still true, and what is not done

- **The default configuration still cannot fabricate a claim.** The deterministic
  generator selects verbatim, the flag is off, and the rollout list is empty.
- **Grounding is unchanged in what it will accept.** The one change to it was to
  make a missing date precision an error, which is strictly stricter and which
  the deterministic generator already satisfied.
- **The AI path has no live measurement yet.** The harness exists and the criteria
  are frozen; the run has not been made. This is the one remaining thing between
  here and a decision about the flag.
- **A repair that would leave a job with no bullets is no longer applied.** Since
  the grounding hardening of 2026-09-25, a job left bulletless while its row
  holds text the page does not already print is an error no repair can fix, so
  such a document is withheld rather than delivered hollow — and a missing job
  is withheld the same way. A job whose only text is already printed under an
  earlier job may be bulletless; the deterministic generator de-duplicates
  sentences across the page for exactly that shape.
- **`Settlement.errors_before` is measured but not stored.** `tailoring_runs` has
  no column for how much repairing a document needed, so it is logged and
  reported by the harness. Storing it would be a migration ahead of a need.
- **No cover letters, no PDF.** Unchanged from `docs/resume-tailoring.md`; the
  same generator shape and the same renderer seam would apply.
