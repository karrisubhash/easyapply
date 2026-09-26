# The S1/S2 representation gap — specification, not implementation

Specification only. Nothing under `app/services/matching/` changed to produce
this document, nothing below is built, and `ALGORITHM_VERSION` stays
`matching_v5`. This is a design artifact for whoever scopes the work, written to
the same standard as `docs/matching-v5-architecture-analysis.md`'s component
list: exact current behaviour, the first point where information is lost, the
smallest shape that would hold it, and what it would cost.

## The gap

The second-rater rubric asks a human to distinguish two situations:

- **S1** — a technology named in a declaring field (a skills row, or a project's
  technology list) and referred to nowhere else.
- **S2** — the same, *plus* a project description that describes work involving
  it.

**The engine cannot tell these apart.** Both produce `TIER_DECLARED_ONLY`, with
identical evidence, identical confidence (0.75) and an identical explanation
string. A human reviewer looking at the same profile can see the difference
immediately, and at least one candidate V6 policy would want to act on it.

## Exactly where the information is lost

`ProfileIndex._index_projects` (`profile_index.py:291-330`) walks each project's
description clause by clause and files what it finds under `mentions` — except
here:

```python
# A project that lists the technology explicitly has
# already produced a declaration; its description
# repeating the word adds nothing but noise.
if any(
    item.source_type == EVIDENCE_PROJECT_TECHNOLOGY
    and item.source_id == project.id
    for item in bucket.declarations
):
    continue
```

`profile_index.py:318-322`. If the same project already declared the
technology, the description's mention is **dropped**, not recorded and ignored.
So:

| profile | declarations | mentions | tier |
|---|---|---|---|
| project lists Docker, description silent | 1 | 0 | `declared_only` |
| project lists Docker, description narrates deploying with Docker Compose | 1 | **0** | `declared_only` |
| project does *not* list Docker, description narrates it | 0 | 1 | `mentioned` |

Rows 1 and 2 are indistinguishable downstream. Row 3 — the weaker-looking
arrangement, where the candidate never declared it — is the only one that
retains the description at all, and it grades *lower* than both.

Measured on the current code, over `declared_only_v2`'s fixtures:

```
v2_project_contradiction  terraform   tier=declared_only  decl=2 mentions=0
v2_project_contradiction  redis       tier=declared_only  decl=2 mentions=0
v2_project_undeclared     postgresql  tier=mentioned      decl=0 mentions=1
v2_project_undeclared     redis       tier=mentioned      decl=0 mentions=1
```

The first two are the sharpest form of the defect. That project's description
says outright that Terraform was evaluated and abandoned and that the Redis
layer was never built — and because the project also *declared* both, the
suppression drops those sentences entirely, leaving `declared_only` with two
declarations and no trace of the contradiction. A candidate who says "I did not
use this" is graded above one who never declared it at all.

The suppression is not a bug and the comment defending it is right on its own
terms: without it, a project that lists a technology and mentions it three times
would look like four pieces of evidence. The defect is that the fix throws the
information away instead of recording it once.

**First point of incorrect semantics:** `profile_index.py:318-322`. Everything
downstream is a faithful function of what this loop chose to keep. `tier`,
`satisfies()` and every candidate policy are innocent — they cannot read a
distinction the index never stored.

This is a **representation** defect in the precise sense
`docs/matching-v5-architecture-analysis.md` uses: no vocabulary change closes
it, because no word is being misread. The shape simply has no field for
"declared here, and also described here."

## The smallest shape that would hold it

One field, one filing change, no new vocabulary, no new tier, and no change to
`satisfies()`:

```
1. TokenEvidence.corroborations : list[Evidence]        (new, evidence.py)

     Evidence the profile supplies for a token BEYOND the declaration itself,
     which is not employment. A project description that describes work with a
     technology the same project declared goes here instead of being dropped.

     It is deliberately NOT `mentions`: mentions already means "named in a
     project, or in a clause that says the work was not done", and merging the
     two would let a cancelled proof-of-concept look like corroboration — the
     exact collapse evidence.py's module docstring says the tiers exist to
     prevent.

2. profile_index._index_projects                        (changed, ~4 lines)

     Where it currently `continue`s on an already-declared technology, append
     to `bucket.corroborations` instead. Same de-duplication rule as today:
     one entry per project per token.

3. TokenEvidence.tier                                   (UNCHANGED)

     Still the same four-way property, in the same order, reading the same
     three lists. `corroborations` is not consulted. A profile that declares
     and describes still grades declared_only, exactly as it does now.
```

Point 3 is the load-bearing one. **This change is verdict-neutral by
construction**: it adds a field that nothing reads. Shipping it changes no
verdict, moves no benchmark number, and needs no `ALGORITHM_VERSION` bump — it
makes a distinction *available* to a later policy, and to the API surface that
explains a verdict to a user, without taking any position on what that
distinction is worth.

Whether a policy should then read it is the V6 question, and this specification
takes no view on it.

## What it would cost

- **`evidence.py`** — one field on a dataclass, and a decision about whether
  `Evidence.all()` and `best()` include it. `best()` should not: the verdict
  cites what qualified it, and corroborations do not qualify anything until some
  rule says they do.
- **`profile_index.py`** — the four-line filing change above.
- **Persistence** — `match_evidence` rows are written from `judgment.evidence`
  (`service.py`), which comes from `best()`. If `best()` is untouched, nothing
  new is persisted and no migration is needed. If a later change wants these
  rows stored, that is a separate schema question with its own migration.
- **Tests** — the existing tier tests pin behaviour that this does not change,
  so they should pass untouched; that is the main evidence the change is
  verdict-neutral. New tests would pin that the field is populated in the
  declared-and-described case and empty in the declared-and-silent one.
- **Risk** — low, and concentrated in one place: the de-duplication rule. Filing
  one corroboration per project per token, rather than per clause, is what stops
  a chatty description from looking like repeated independent evidence.

## What this does not fix

- **It does not distinguish a project description that *contradicts* the
  declaration** ("I evaluated Terraform but deployed by hand"). That clause
  contains a `NON_DOING_MARKERS` word, so `demonstrates_v5` already returns
  False and the existing `mentions` path is the right home for it — but the
  suppression at `profile_index.py:318-322` currently drops that too, which
  means a contradicting description is invisible in exactly the same way a
  corroborating one is. The filing change above should route both: the
  affirmative reading to `corroborations`, the non-doing reading to `mentions`.
  `declared_only_v2`'s `v2_project_contradiction` fixture exists to put this
  case in front of a human (rubric code `S6`).
- **It does not touch employment.** S3 is already representable —
  `declarations` plus `employment` is `declared_corroborated` — and nothing here
  changes that.
- **It does not decide anything.** No policy reads the new field. Adding one is
  V6 work, gated on the human review, and out of scope for this document by the
  same reasoning that kept every other component in
  `docs/matching-v5-architecture-analysis.md` separate from the analysis that
  identified it.

## Order, if this is ever scoped

Before any V6 policy that would read it, and independently of which policy wins:
it is verdict-neutral, so it can ship on its own schedule, and having it in
place means a policy decision does not also have to carry a representation
change. After the human review, though, not before — if the reviewer's S1/S2
classifications turn out to track nothing in their verdicts, the distinction is
not worth representing and this document should be closed unbuilt.
