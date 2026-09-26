# Matching — the deterministic engine (matching_v5_1)

Given a **confirmed profile version** and a job's **completed structured
requirements**, produce an explainable match result: what matched, what is
missing, what is uncertain, why each conclusion was reached, and what profile
evidence supports it.

```
ProfileVersion (confirmed, immutable)
        +
JobRequirements (one completed structuring run)
        ↓
matching engine — deterministic rules, no model
        ↓
MatchResult              counts, coverage, algorithm version, as-of date
  └ MatchRequirementResult   one requirement, judged, with an explanation
        └ MatchEvidence        the profile rows that justify the judgment
```

There is no LLM here, no embedding, no vector store, no similarity threshold.
That is the point of V1 rather than a limitation of it: before anything
semantic is introduced there has to be a baseline whose mistakes are
*explainable* rather than merely plausible, and something honest for a later
semantic or hybrid approach to be compared against.

## Architecture review

Read before writing anything, and reused rather than reinvented: the
profile-version immutability guarantee (triggers in `10f47a8774be`), the
ownership posture the job and profile services take (resolve through the owner,
a stranger's id is a 404 and not a 403), the `JobCandidate` schema as the
structured requirement shape, the `job_requirements`-per-run append-only
pattern, the error-handling convention in `app/api/errors.py`, and the
commit-inside-the-service transaction convention. Nothing in the profile or job
domains changed to make room for this; the matching migration is additive and
touches no existing table.

Matching is an application/domain service under `app/services/matching/`. Not a
microservice, not a worker — it is fast, synchronous and pure.

## The two inputs, and why they are pinned

**Profile side.** Matching runs against a `ProfileVersion` whose status is
`confirmed`, never against a draft. A confirmed version is immutable, so the
candidate side of a stored result cannot move underneath it: a later profile
edit creates a *new* version rather than changing what an existing match was
computed from. `POST` with no `profile_version_id` uses the caller's current
confirmed version — well-defined and immutable, since the database forbids
`is_current` on a draft.

**Job side.** The result pins `job_requirements_id`, **not** `job_id`. A
`job_requirements` row is written once per completed processing run and never
updated, so pinning it freezes the exact requirement text, structurer, model
and prompt version the verdicts were reached against.

*This is the answer to "what if JobRequirements are reprocessed?"* — they are
not mutable. Reprocessing a job creates a new run and therefore a new
requirements row, which is a genuinely new match rather than a quiet
reinterpretation of the old one. The old result keeps pointing at the old row
and stays readable exactly as it was. Both foreign keys are `RESTRICT`: a match
is a record of a judgment that was made, and losing either input would make the
stored verdicts uninterpretable.

## The three verdicts

| verdict | meaning |
|---|---|
| `MATCHED` | The profile asserts it in a field whose purpose is to assert it. |
| `MISSING` | The matcher looked and found no qualifying evidence at all. |
| `UNCERTAIN` | Evidence exists but does not settle the question. |

Three, not four. Every candidate for a fourth — "partially matched", "weakly
matched", "expired" — is really a statement about *confidence* or about
*evidence*, and both are already first-class: confidence is a column, evidence
is a table. A fourth state would put the same information in two places and let
them disagree.

The verdict follows from how strong the evidence is against what the posting
asked for — never from a score crossing a threshold:

```
tier meets the requirement's standard   → MATCHED
evidence exists, tier too weak          → UNCERTAIN
no evidence at all                      → MISSING
```

## Evidence tiers

matching_v1 had two kinds of evidence, "declared in a field whose purpose is to
declare it" and everything else. The real-data benchmark showed that binary
costing verdicts at both edges, so `evidence.py` grades instead:

| tier | meaning |
|---|---|
| `declared_corroborated` | listed as a skill **and** demonstrated in paid work |
| `employment_described` | demonstrated in a paid role's description; the skills list is silent |
| `declared_only` | declared, with nothing in the profile corroborating it |
| `mentioned` | named only in a project, or in a clause saying the work was *not* done |

Prose is read **clause by clause**, not as a bag of words. "Evaluated React
Native for a companion app, but the project was cancelled" names React Native
in a sentence that says it was not used, so it grades as `mentioned` — the
distinction between reading a description and scanning it. The clause itself is
stored on the evidence row, so a reader can judge the claim rather than trust
the grade.

## What a requirement demands

A posting that says "Kubernetes" and one that says "Kubernetes in production,
not just in a course" are asking different questions. The bar is read from the
requirement's own words **and its verified provenance quote** — the extracted
name has lost it:

| standard | satisfied by |
|---|---|
| `any_evidence` | `declared_corroborated`, `employment_described`, `declared_only` |
| `professional_use` | `declared_corroborated`, `employment_described` only |

Only *verified* quotes are consulted. An unverified citation is one the server
could not find in the description — possibly fabricated by a provider — and
letting it raise a bar would hand a model influence over a verdict the
deterministic engine exists to deny it.

`confidence` is confidence **in the verdict**, not match strength. A `MISSING`
against an empty profile is a confident verdict (0.9); an `UNCERTAIN` is by
construction an unconfident one (0.3–0.5). Reading it as "how good a match"
would invert its meaning on every `MISSING` row.

## Evidence

Every `MATCHED` and `UNCERTAIN` carries `MatchEvidence` rows naming the source
table, the row id, a label built from that row's own fields, the **tier** it
was counted at, whether it **qualified**, a **note** saying why or why not, and
the **quote** it was found in. Nothing is invented. `MISSING` carries no
evidence and says so in words — "No qualifying evidence was found" — rather
than being rendered as an absence the reader has to interpret.

Evidence that fell short is kept rather than discarded. "We found this and it
was not enough" is the most useful thing the engine can say about a gap, and an
`UNCERTAIN` showing nothing cannot explain itself.

`label` and `detail` are denormalized copies of what the evidence said at match
time. That is deliberate rather than a shortcut: a match result that cannot
explain itself without a join into live data is not a record of anything.

## Skill normalization

`app/services/matching/normalization.py`. Mechanical rules first, then a small
explicit alias table:

1. **Case, whitespace and punctuation are typography.** `PostgreSQL`,
   `postgresql` and `Postgre SQL ` are one token. Punctuation becomes a
   *separator*, never a deletion, so `react/redux` is two words rather than the
   nonsense token `reactredux`. `+` and `#` are kept — they are part of the
   name, which is what keeps `C`, `C++` and `C#` distinct.
2. **A trailing version is a version.** `Python 3`, `Java 11`, `Angular 2+`
   normalize to their base. Anchored at the end, so `3D modelling` keeps its
   word.
3. **Everything else needs an explicit alias**, with a justification beside it.

The bar for an alias: the two strings must name the same thing to a hiring
manager, not merely related things. Current entries cover spelling variants
(`postgres`→`postgresql`, `k8s`→`kubernetes`, `golang`→`go`, `nodejs`→`node.js`),
unambiguous abbreviations (`js`, `ts`, `py`), vendor names written out
(`amazon web services`→`aws`), punctuation-normalized spellings
(`react js`→`react`, `csharp`→`c#`), and spelled-out acronyms
(`restful api`→`rest`).

**What is not there matters as much.** These stay distinct, and the test suite
pins each one:

```
python  ≠ django          a language is not a framework written in it
react   ≠ react native    sharing a name is not sharing a platform
postgresql ≠ mysql        both SQL, different products
java    ≠ javascript      the classic, and the most expensive to get wrong
aws     ≠ azure
```

None of those are special-cased. They are distinct because no alias claims
otherwise: equivalence is opt-in, one justified line at a time, and the default
is that two different strings are two different things.

Matching is **whole-token, never substring**. The canonical token `go` cannot
fire inside `Django` or `MongoDB`, which is what makes short tokens safe here.
Prose scanning uses word boundaries and adjacent windows of one to three words
— not arbitrary subsets, which is how "experience with Java, and separately
with Script tooling" would become `javascript`.

## The rules

### Skills and technologies

A canonical-token lookup against the profile index, and nothing else. No
substring search, no similarity.

### Experience

Requirements naming no technology (a seniority or domain line, "internship
experience") fall back to a word scan over the profile, and that scan — and
only that scan — consults the work-action inflection table in
`morphology.py`, so "experience leading a team" is answered by "Lead four
engineers".

**Subject resolution first.** matching_v1 decided what a duration requirement
was about by removing a stopword list and keeping what was left, so "3+ years
building production services with Django" resolved `building` — and against a
profile whose unrelated role said "building credit-risk models" the engine
reported 7.7 years of Django experience for a candidate who had never used it.

A subject is now a token `vocabulary.is_technology` recognises: the curated
canonical list, unioned with whatever the profile itself declares as a skill or
project technology. The union is what keeps the list from needing to be
exhaustive — a profile that declares "Svelte" makes Svelte resolvable for that
match without anyone editing the file — and it cannot create a false subject,
because a token only enters it when the candidate called it a skill.

Four paths, chosen by what the requirement is about:

1. **No stated minimum** — judged exactly as a skill requirement is.
2. **A minimum, and a technology resolves** — duration is measured from paid
   roles whose own description demonstrates that technology, and from nothing
   else. A project can never appear here: `employment_mentioning` cannot
   return one, so "a side project is not professional duration" is structural
   rather than a rule someone has to remember.
3. **A minimum, and the requirement names only a kind of work** — total
   employment answers it, with the asymmetry below.
4. **A minimum, and nothing resolves** — `UNCERTAIN`. "5+ years of fintech
   experience" asks something real that this engine cannot check, and
   answering it with total employment would be inventing an answer.

**The total-employment asymmetry** is sound rather than merely cautious: total
employment is an *upper bound* on employment in any particular field. So a
total below the bar settles the question in the negative whatever the field —
you cannot have six years of anything inside seven months of work — while a
total above it only settles the question if some role identifies itself with
the kind of work the posting names. A profile of mechanical-engineering roles
against "5+ years of professional software experience" is `UNCERTAIN`, not a
nine-year match.

Overlapping roles are **merged, not summed**: two concurrent engagements from
2020 to 2022 are two years, not four.

### Education

Two independent questions, and conflating them is the mistake §10 warns about:
does the profile reach the required **level**, and is it in the required
**field**?

- Level not reached → `MISSING` (with the education that *was* found shown).
- Level reached, requirement names no subject → `MATCHED`.
- Level reached, subject matches → `MATCHED`.
- **Level reached, subject differs → `UNCERTAIN`.** A B.Tech in Mechanical
  Engineering against "Bachelor's in Computer Science or related field" is
  `UNCERTAIN`, never `MATCHED`: whether mechanical engineering is "related" is
  a judgment the profile schema holds no information to make.
- Requirement states no level this matcher recognizes → `UNCERTAIN`.
- **Level reached only by a degree still in progress** (its end date is after
  `as_of`) → `UNCERTAIN`, as of matching_v5_1. A degree being studied for is not
  held, and whether a posting accepts one in progress is not something the
  profile states.

The deterministic job structurer records the level and the sentence but never
fills `field_of_study`, so the field is read out of the requirement text.
Without that, every subject requirement would silently collapse into a
level-only one and the case above could never arise.

### Certifications

Exact canonical match, plus **one** equivalence rule that runs in a single
direction: a held credential that is a *more specific* form of the required one
satisfies it, because the extra words name the level of the same credential.

```
required: AWS Certified Developer
held:     AWS Certified Developer – Associate   → MATCHED
```

The reverse does not — a posting asking for the Associate level is not answered
by the unqualified name (`UNCERTAIN`). Unrelated credentials from the same
vendor never match: `AWS Certified Solutions Architect` shares words with
`AWS Certified Developer` and nothing else. A held credential that expired
before the as-of date is `UNCERTAIN`, not `MATCHED` — the profile genuinely
holds it, and whether a lapsed credential satisfies the posting is not
something the profile can answer.

### Qualifications

Free-text requirements ("must be authorized to work in the US", travel,
clearance) are recorded as `UNCERTAIN` with an explanation saying the matcher
cannot evaluate them. **Recorded, not dropped**: silently discarding a real
requirement shrinks the denominator and makes coverage look better than it is
— the one way the score could mislead without any individual verdict being
wrong.

### Responsibilities

Not evaluated, by design. A responsibility describes the role once hired, not a
bar the candidate clears, which is why `JobCandidate` gives it no
required/preferred axis in the first place.

### Location

Two questions, and no others: is the candidate in the city the posting named,
and failing that, are they in the same country.

| candidate's employment | requirement names | verdict |
|---|---|---|
| Berlin, Germany | Berlin | **MATCHED** (city, 0.9) |
| Munich, Germany | Berlin | **MATCHED** (country, 0.75) |
| Toronto, Canada | Berlin | UNCERTAIN |
| no parseable location | anywhere | UNCERTAIN |
| two ongoing roles in different countries | anywhere | UNCERTAIN |
| `"Remote"` | anywhere | UNCERTAIN |
| anything | a region, a timezone, an unlisted city | UNCERTAIN |

**Why a country match is MATCHED.** A posting that says Berlin and a candidate
working in Hamburg are in a materially different position from a candidate in
Toronto, and a reader treats them differently. The confidence says the city
differs; the verdict says the question is substantially answered.

**Why a different country is UNCERTAIN and never MISSING.** The profile schema
has no residence field and no relocation field — not on `Profile`, not on
`ProfileVersion`, not on `User`. The only geographic signal is where past JOBS
were, so "their last job was in Dublin" is not "they cannot work in Berlin". A
MISSING would assert something the profile never states. **No location
requirement can return MISSING**, and a test enforces it.

**Employment location is a proxy, and is treated as one.**
`ProfileIndex.current_location()` prefers an ongoing role, requires concurrent
roles to agree on the country before it believes either, keeps the country and
drops the city when two ongoing roles disagree only about the city, and falls
back to the most recent ended role when nothing ongoing states a place.
Education, projects and free text are never read: a university in Berlin is not
an address.

**The vocabulary is finite and curated** — 242 cities mapped to 63 countries,
with 26 country aliases. A city it has never heard of parses to nothing and the
requirement is UNCERTAIN. Names whose referent is genuinely contested
(`Cambridge`, `Birmingham`, `Perth`, `San Jose`) are listed in
`AMBIGUOUS_CITY_NAMES`, asserted absent from the table at import, and stay
unresolvable — guessing would produce a confident MATCHED on the wrong
continent. `"Berlin, France"` is a contradiction and resolves to nothing rather
than picking a half to believe.

**Evidence is mandatory for a MATCHED.** It cites the `Experience` row the
location was read from, tier `employment_described`, with the raw location
string as the quote. A verdict that cannot be grounded in a profile row is
downgraded to UNCERTAIN rather than asserted — enforced in
`rules._location_verdict`, not merely intended. The posting is never cited as
evidence about the candidate. An UNCERTAIN keeps the row it weighed, marked as
not qualifying.

**Reproducibility.** `evaluate` takes a `JobPosting` carrying `location_raw`
and `workplace_type`. Reading them at match time is safe for the same reason
`job_requirements_id` is safe to pin: `ingest_job` returns an existing row
UNCHANGED — "the first-seen version wins" — so both fields are write-once for
the life of the job.

**Deferred, and deliberately.** Timezone proximity — reading "within two hours
of CET" literally would have flipped two correct benchmark verdicts to wrong
ones, so the clause is not interpreted at all. Regions ("the EU"). Remote
eligibility — a posting whose `workplace_type` is `remote` is declined rather
than judged. Relocation willingness and candidate residence, neither of which
the schema can express. Multi-location postings — a requirement naming two
countries is refused. Geospatial distance, coordinates and any external
geocoding service.

## Required vs preferred

Kept apart end to end — separate counts, separate coverage, separate groups in
the UI. A missing preferred requirement and a missing required one are the same
*verdict* about very different *facts*. The two axes mix in exactly one place,
the overall score, and they mix gently.

## The score

```
required_coverage  = required_matched  / required_total     (1.0 if none)
preferred_coverage = preferred_matched / preferred_total    (1.0 if none)

overall_score = 0.8 × required_coverage + 0.2 × preferred_coverage
```

Three decisions worth defending:

**`UNCERTAIN` counts toward the denominator, never the numerator.** A
requirement we could not settle is not a requirement we met. Excluding
uncertain results would let a profile with two matched and eight unreadable
requirements report 100% — the exact failure this domain is built to avoid.
Counting them as met would be worse. So they dilute, and the stored counts say
how much of the dilution is doubt rather than absence.

**Required is weighted 4:1 over preferred, not treated as a gate.** Nothing
here turns a missing required requirement into a rejection.

**An empty axis scores 1.0.** A posting with no preferred requirements has not
failed to have them; coverage of nothing is complete by construction.

The components are stored as columns, not derived on read: the counts *are* the
explanation, and `3 matched, 2 uncertain` is more use than any percentage.

> This number is **coverage of structured job requirements by available profile
> evidence**. It is not a probability of being hired, an aptitude estimate, or
> a ranking of candidates, and no docstring, column comment, API field
> description or UI string in this codebase may describe it as one.

### No hard rejection

Per §13, the engine reports `required_matched`, `required_missing` and
`required_uncertain` separately and stops there. It never concludes a candidate
is unsuitable. `MatchDetail.has_unmet_required` is derived server-side so a
client cannot invent its own rule, and is phrased as *unmet* rather than as a
suitability verdict — it says what the counts say, nothing more.

## Algorithm versioning

Every result stores `algorithm_version`, currently **`matching_v5_1`**. Mandatory,
and never inferred from a timestamp. Old results keep their own value and stay
interpretable, and the same profile version against the same requirements row
under a new algorithm is a *new* result rather than an overwrite — the version
is part of the idempotency key precisely so that a job matched under v1 is
re-evaluated rather than answered from the stored v1 row.

**v1 → v2** was forced by the real-data benchmark, which found two defects the
rule-level regression suite could not see: subject resolution by keyword, and
evidence sorted into two bins.

**v2 → v3** corrected the one requirement v2 made worse: a posting asking for
"experience leading a team" against a role that says "Lead four engineers".
Exactly one verdict in 143 moved — a small change, and still a change, which
is the whole test. Without the bump the idempotency key would hand a user back
the stored v2 verdict this release exists to correct.

**v3 → v4** judges location requirements instead of deferring them. A
requirement naming a city or country the vocabulary knows is now MATCHED when
the candidate's current employment puts them in that city, or in that country.
Stored v3 results keep their blanket UNCERTAIN and stay interpretable; without
the bump the idempotency key would hand back the old verdict for any job
already matched, and the location verdicts would never be seen.

**v4 → v5** refines how a non-doing marker's verdict reaches the evidence it
describes. matching_v4 computed one boolean per whole clause and broadcast it
to every technology the clause named, so "Deprecated the legacy PHP monolith
while building its replacement in Go" graded PHP and Go identically. v5 adds
four small, independent, entirely deterministic pieces — no model, no
embedding, no parser — on top of the unchanged `vocabulary.NON_DOING_MARKERS`,
`is_non_doing()`, `demonstrates()`, and `rules.resolve_technology_subject()`:
a clause is divided into scope-bounded spans (`span_split.py`) so a caveat and
its own resolution grade separately; a trailing relative clause is reattached
to the nearest technology token it actually modifies; an explicit
plain-English denial of use ("no Rust code has shipped yet") is recognised
even when it names no marker word at all (`negation.py`, kept structurally
apart from `NON_DOING_MARKERS` — a bare negator is not safe to add to that
list, see the module's own docstring); and a job title is graded by
`evidence.demonstrates_title`, a separate rule, rather than the same path as
prose. 34 of 139 `non_doing_v2` judgments move relative to what matching_v4
computed on the same text, all toward correctly demonstrated experience
previously read as a caveat, and zero requirements in `matching_real_v1`
move at all — see `docs/matching-v5-results.md` and
`docs/matching-v5-release.md` for the full measurement. Stored v4 results
keep their own value and stay interpretable; without the bump the idempotency
key would hand back the old, weaker verdict for a job already matched.

**v5 → v5_1** is corrections, not a capability: each is a verdict an offline
probe of the pure rules showed the profile does not support. Almost all move in
the conservative direction; the exception is the "any one of" reading below,
which turns a wrong `MISSING` into `MATCHED` where the posting offered
alternatives.

- **Denials read as paid work.** `negation.py`'s six patterns needed
  "never/not" directly before a *past* use-verb, so the commonest English
  negations — "Did not use Django", "Didn't use", "Haven't used", "Did not
  work with" — and span-initial "Unfamiliar with", "Lacks … experience",
  "was never adopted", "Never had the chance to use" were all graded
  `employment_described`: a role saying "Unfamiliar with Django." was cited as
  "Demonstrated in paid work" and its whole duration counted toward "3+ years
  of Django". Four patterns were added, each with adversarial controls
  (`tests/test_matching_negation.py`). They yield to a subordinating word
  before them ("which", "that", "so", "before" …): "Designed Go services that
  do not use sticky sessions" denies sticky sessions, not Go.
- **A field of work confirmed by one shared word.** "Business Development
  Manager" confirmed "5+ years of software development"; a career of
  mechanical-engineering roles confirmed "software engineering" — the case this
  document says is `UNCERTAIN`; "No software engineering experience" confirmed
  it too. A role now confirms a field only through an affirmed clause or its
  title naming a multi-word field phrase, or a single word that names a
  discipline on its own ("software", "web"), never one that needs a qualifier
  ("development", "engineering", "management", or half of "front end") — and a
  multi-word phrase must contain such a word, so "end development" does not
  confirm "front end development" from "end-to-end development". When the
  posting names only qualifier-dependent words ("5+ years of engineering
  experience", "front end development"), its own phrase is the field and a
  role naming it confirms it. Words
  that grade a field rather than name it ("senior", "enterprise", "large
  scale") confirm nothing, alone or together. Titles and postings are compared
  across their forms ("Engineer"/"engineering", "Frontend"/"front end").
  "IT", written in capitals, is information technology: only a role saying IT
  or "information technology" confirms it, "IT systems" and "IT support"
  included — "5+ years of IT experience" had been answered by total
  employment, so a career chef MATCHED it.
- **Years that have not happened.** An end date after `as_of` is clamped to
  it; a role that has not started counts zero.
- **A degree in progress counted as held.** A degree ending — or, as the resume
  structurer stores "Expected May 2027", starting — after `as_of` is
  `UNCERTAIN` ("in progress"), not `MATCHED`.
- **"X and Y" judged on X.** "5+ years with Kubernetes and Go" resolved only
  the most specific token, so eight years of Kubernetes and no Go was
  `MATCHED`. Every named technology is now judged — any one of them only when
  the technologies themselves are joined by "or", "and/or" or a slash ("Python
  or Go", "Python (or Go)", "C/C++", "Docker, Helm, or Kubernetes"), all of
  them otherwise. A comma list is alternatives only when a WORD closes it: in
  "React, TypeScript, HTML/CSS" the slash is inside the last item. A curated
  multi-word technology keeps its glue word, so "Ruby on Rails or Django" is
  two alternatives, not ruby + rails + django. An "or"
  elsewhere in the sentence ("5 or more years", "or equivalent experience",
  "in production or staging") joins nothing; the first draft of this rule read
  it as "either will do" and was caught before release.
- **Letters read out of words.** "R&D", "C-level" and "go to production"
  resolved `r`, `c` and `go`. A one- or two-letter technology must be written
  as a capitalised word of its own ("Go", "Go-based", "Python/Go") — unless
  the text reached it through a longer alias ("Golang", "continuous
  integration", "CSharp"), which is unambiguous by itself, or it carries a
  digit ("k6"), which no English word does. The same guard applies to a
  requirement with no duration, which had looked the letters up directly.
- **Stated years against a short attributed total.** Roles that name a
  technology are a lower bound on time with it, so a profile stating 4 years
  of Django plus one 1.7-year Django role is `UNCERTAIN`, not `MISSING`.
- **Evidence under a non-matched verdict stored as qualifying**, with a note
  ("A paid role's description demonstrates this directly") contradicting the
  `UNCERTAIN` beside it. It is now qualifying only under `MATCHED`.
- **Evidence for a duration verdict** now quotes the sentence that tied the
  role to the technology, not just its title and dates.

Measured before release: `matching_real_v1`, `declared_only_v1`,
`non_doing_v1` and `non_doing_v2` score **exactly** what matching_v5 scored —
not one of their judgments moves (the reports are identical but for the
version name). One stricter variant was measured and rejected: counting a
field's years only from the roles that name it turned three of
`matching_real_v1`'s human `MATCHED` verdicts `UNCERTAIN` (c01, c02, c08),
because reviewers count a "Backend Engineer" role as software employment
whether or not it says so. Named `v5_1`, not `v6`: `matching_v6` is the
declared-only redesign already in progress (`docs/matching-v6-*.md`), which
this is not.

The service also stops truncation failures: a certification line longer than
128 characters became a 128-plus-character `requirement_key` and the INSERT
raised, so that job could never be matched. Keys, labels and matched values are
now cut to their columns.

Every version ever shipped stays defined in `app/models/match.py`: historical
rows point at them, and deleting a name would leave those rows citing a
version nobody can look up.

## Reproducibility

Four things together make a stored result reproducible:

- `profile_version_id` — confirmed, therefore immutable.
- `job_requirements_id` — written once per run, never updated.
- `algorithm_version` — the rules are an input.
- `as_of_date` — "5+ years" is a question about a moment. `evaluate()` takes
  `as_of` as a **parameter**; a matcher that read `date.today()` internally
  would be unreproducible by construction.

`evaluate()` is a pure function of `(JobCandidate, ProfileVersion, as_of)` — no
database, no clock, no configuration. Subject tokens are sorted before
iteration for the same reason: a set is hash-ordered and Python salts string
hashes per process, so `next(token for token in subjects …)` would otherwise
pick a different token on a different run.

## Idempotency

```
UNIQUE (profile_version_id, job_requirements_id, algorithm_version)
```

The same candidate snapshot, judged against the same requirement snapshot, by
the same rules, is the same evaluation. `create_match` looks first and returns
the existing row; if two requests race between the `SELECT` and the `INSERT`
the unique constraint decides and the loser reads the winner's row rather than
raising. A duplicate match is not an error — it is the same answer.

`as_of_date` is stored but is deliberately **not** part of the key. Re-requesting
yesterday's match returns yesterday's answer, complete with the day it was
measured against, instead of silently producing a different one because a day
passed.

## Security

Every query is scoped at the backend; the frontend enforces nothing.

- A `profile_version_id` is a global primary key and proves nothing on its own.
  It is loaded through a join to `profiles.user_id`, and a version belonging to
  someone else is a **404, not a 403** — the same posture the job and profile
  services take, and the answer to the insecure-direct-object-reference §19
  names.
- Jobs are resolved through `job_service.get_job`, so another user's job is a
  404 before any match row is read, rather than an empty list that quietly
  confirms the job exists.
- `MatchResult.user_id` is denormalized so every read is one predicate rather
  than a three-table join; creation still resolves ownership from the profile
  side.
- A stranger's match id and an id that was never issued are indistinguishable
  from outside.

## Persistence

Three normalized tables rather than one JSON blob, because the questions this
data exists to answer are set-shaped: *which required skills are missing across
every match*, *which jobs have high required coverage*, *what are the common
gaps*. `job_requirements.payload` took the opposite tradeoff for the opposite
reason — it is read whole and never queried per item.

`match_requirement_results.requirement_key` holds the canonical token and is
indexed, so "which skills are most often missing" is one `GROUP BY`.

`MatchEvidence.source_id` is deliberately **not** a foreign key: it is
polymorphic across five tables, and the alternative — five nullable columns and
a check that exactly one is set — buys referential integrity at the cost of a
schema nobody can read. The parent's `profile_version_id` is what makes the id
resolvable, and that *is* a foreign key, held with `RESTRICT`.

## API

```
POST /api/v1/jobs/{job_id}/matches     201 new · 200 existing
GET  /api/v1/jobs/{job_id}/matches     this caller's matches, newest first
GET  /api/v1/matches/{match_id}        one result in full, with evidence
```

`MatchRequest` carries one optional field, `profile_version_id`, and is
`extra="forbid"`: the algorithm version, the requirements row and the owner are
asserted by the server from the authenticated caller and the job's own history,
never accepted from the client. 409 when the job has not been structured yet or
the named version is a draft — both states where a match would be meaningless
rather than merely empty.

## Frontend

`/jobs/[id]/match`. The page answers four questions in the order they are
useful: what is required and missing, what is uncertain, what matched, and what
evidence caused each conclusion. Coverage appears once, small, beside the counts
that explain it — §20 asks that the percentage not dominate, and the reason is
that it is the least actionable thing on the page.

Required and preferred are separate rows in the summary and separate groups in
every category. `MISSING` is labelled "Not found" and styled neutrally rather
than as a failure: a red badge on every unmet requirement would say the
candidate is unsuitable in colour, where the words are careful not to.

Matching is an explicit action rather than something that happens on load — a
match is a stored record of a judgment, so creating one is a decision the user
makes, and re-requesting is free.

## Evaluation

`evaluation/datasets/matching_v2/cases.json`, run with
`python -m evaluation.match_runner`. Fifteen cases covering the shapes §22
names, and five measures:

- `classification_accuracy` — the headline, and on its own not enough.
- `false_matches` — expected `MISSING`/`UNCERTAIN`, reported `MATCHED`. The
  worst output this system can produce, because unlike a miss it is invisible
  to the person reading it. Counted and reported separately.
- `false_missing` — a real error, but a visible one.
- uncertainty errors, split into `overcautious` and `overconfident` so it is
  visible *which way* the engine leans.
- score consistency and reproducibility — checked, not assumed.

`matching_v1/cases.json` is kept beside it: exactly one expectation differs
(a technology demonstrated in a paid role now matches a requirement that does
not demand production use), and keeping both means the older rules stay
interpretable.

The runner is deliberately not a test. `tests/test_matching_evaluation.py`
asserts the *framework* — that the dataset covers the required shapes, that
scoring can tell a right answer from a wrong one, that it counts an invention
as an invention — and never a threshold. A number checked into CI before anyone
knows what a good score looks like freezes today's accident into tomorrow's
requirement.

## Measured against human judgment

`evaluation/datasets/matching_real_v1/` holds 143 verdicts a person reached
about 6 realistic profiles and 8 realistic postings, written without reference
to these rules. Run with `python -m evaluation.benchmark.runner`. The ground
truth has not changed since it was written; only the measurement moves.

| | v1 | v2 | v3 |
|---|---|---|---|
| false matches | 6 | 4 | **4** |
| false missing | 6 | 4 | **3** |
| matched P / R / F1 | 0.875 / 0.646 / 0.743 | 0.930 / 0.815 / 0.869 | **0.931 / 0.831 / 0.878** |
| missing P / R / F1 | 0.887 / 0.904 / 0.895 | 0.926 / 0.962 / 0.943 | **0.943 / 0.962 / 0.952** |
| uncertain P / R / F1 | 0.452 / 0.731 / 0.559 | 0.625 / 0.769 / 0.690 | **0.625 / 0.769 / 0.690** |
| overcautious / overconfident | 23 / 7 | 12 / 6 | **12 / 6** |
| evidence correctness | 0.898 | 0.951 | **0.952** |
| required / preferred agreement | 0.758 / 0.750 | 0.859 / 0.864 | **0.869 / 0.864** |
| total disagreements | 35 | 20 | **19** |
| raw agreement | 0.755 | 0.860 | **0.867** |

By what the requirement was testing (v1 → v3):

| | v1 | v3 |
|---|---|---|
| prose evidence | 2/6 | **6/6** |
| project-not-employment | 5/7 | **7/7** |
| domain | 3/6 | **6/6** |
| seniority | 1/3 | **2/3** |
| experience duration | 3/8 | **5/8** |
| uncorroborated claims | 0/5 | 1/5 |
| aliases, near-synonyms, exact skills, certifications | 100% | 100% |

**Still wrong, and why.** Four false matches remain, all of the same shape: a
skills-list entry with nothing corroborating it, against a posting that asks
for the skill without asking for production use. The engine treats that as
satisfied because the posting set no higher bar; the reviewer judged the
profile too thin to believe. Closing that gap means weighing a claim against
the profile's overall substance, which is a different kind of rule from
anything here.

The v2 regression — "experience leading a team" against "Lead four engineers"
— is fixed in v3 by a narrow work-action inflection table
(`morphology.py`). A general stemmer was measured and rejected: 42 technology
entries and 14 alias keys end in -s, -ed or -ing, so suffix-stripping turns
`spring` into `spr`, `redis` into `redi` and `postgres` into `postgre`. The
table is a hand-written list of work verbs, it is consulted only from the scan
for requirements that name no technology, and it changed exactly one verdict
in 143 with no new false matches.

Seniority and domain are still not evaluated as such. Domain scores 6/6, but by
side effect: paid-role prose qualifies, and these postings happen to use words
that appear in the role descriptions. That is luck, not a domain rule, and it
should not be read as one. **Location is evaluated as of matching_v4** — see
the Location section above.

## What this milestone does not do

No embeddings, no vector database, no semantic similarity, no LLM reasoning, no
Azure AI Search. No resume tailoring, no autofill, no extension changes. No
Temporal, Event Hubs, AKS or Kubernetes. Matching does not rank jobs, decide
whether to apply, or say anything about a person's suitability.

### Known limits

- **Coverage of the alias table is narrow by design.** An unlisted synonym
  produces a `MISSING` — a visible, correctable error, chosen over the
  invisible one. The benchmark found no missing and no false aliases.
- **The technology vocabulary is curated and finite.** A technology neither
  listed nor declared by the profile makes a duration requirement
  `UNCERTAIN` rather than answered. That is the safe direction, and it is a
  real limit.
- **A declared-only skill satisfies a requirement that sets no bar.** Four of
  the benchmark's remaining disagreements are this, and closing it needs a
  rule about profile substance that does not exist yet.
- **Morphology covers work-action verbs only.** `leading`/`lead`/`led` are one
  concept because `morphology.py` says so, one line at a time. Everything
  else compares surface forms — `engineer` is still not `engineering` — and
  technology matching never consults the table at all. The one exception is
  the field of a total-employment requirement, which since matching_v5_1
  compares "Engineer"/"engineering", "Developer"/"development" and a few
  others through a closed table (`rules._FIELD_FORMS`).
- **"Or related field" is never resolved.** It is always `UNCERTAIN`, because
  the profile schema holds nothing that could resolve it.
- **Experience attribution is keyword-based.** A role whose title and
  description never name the technology cannot be attributed to it, and becomes
  `UNCERTAIN` rather than counted.
- **Free-text qualifications are never evaluated**, only recorded.
- **Seniority and domain are not matched at all** — the requirement shape does
  not carry them in a checkable form. Location is matched as of matching_v4,
  narrowly: city or country equality only.
- **One in-field role credits all employment** (matching_v5_1 kept this on
  measurement, see *Algorithm versioning*). Fifteen years as a chef and one as a
  junior developer answer "5+ years of software development" with the total.
  Counting only in-field roles fixes that case and costs three correct verdicts
  on real data; neither dataset has an instance of the case itself.
- **One discipline word still confirms a field.** "software", "data" and "web"
  name a discipline on their own, so a "Software Sales Executive" confirms
  "software development", a "Data Entry Clerk" confirms "data engineering", and
  a chef who "liaised with IT" confirms IT. Refusing single words cost correct
  verdicts on real data; telling a sales role from an engineering one needs a
  notion of role type this engine does not have.
- **A technology offered as an alternative is still credited by span.**
  "Chose Flask instead of Django" and "built it without Django" are read as
  Django work: marking the whole span non-doing would also demote Flask. It
  needs per-token attribution (`docs/matching-v5-architecture-analysis.md` §C).
- **A single clause credits a role's whole duration.** "Built production
  applications using Django" in a 7-year role counts 7 years of Django. That is
  this document's stated rule; whether one sentence should carry a duration is
  a product decision, not a defect.
- **The evaluation dataset is hand-built and small.** Its cases were written
  from the domain rules, so a perfect score demonstrates internal consistency
  and regression safety, not real-world accuracy. Real postings and real
  profiles are what would measure that.
