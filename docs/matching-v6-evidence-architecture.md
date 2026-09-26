# Matching V6 — evidence architecture

Design investigation. **Nothing is implemented.** Nothing under
`app/services/matching/` changed to produce this document, `ALGORITHM_VERSION`
is still `matching_v5`, no `matching_v6` exists, no benchmark ground truth
moved, no declared-only policy is selected, no exposure-wording logic is
designed into the engine, and nothing here proposes a model, an embedding or a
similarity measure. Every claim about current behaviour was checked by reading
the code and, where a number appears, by running it.

The brief: trace the declared-only evidence flow end to end, say exactly what
information `profile_index.py` throws away, design a representation that keeps
it, decide whether the four-tier model survives, trace every consumer, and
separate representation from policy from scoring from demand.

**The one-sentence finding.** The profile index already holds more information
than the tier exposes, and the sufficiency policy can only see the tier — so the
first thing V6 needs is not a new tier but a wider seam between the evidence and
the rule that judges it.

---

## A. The evidence lifecycle today

```
 PROFILE VERSION (the user's own rows)
 ├── Skill(name, years_of_experience, proficiency)
 ├── Project(name, description) ─── ProjectTechnology(name)
 ├── Experience(job_title, description, start_date, end_date)
 ├── Education / Certification                        ... never reach TokenEvidence
 │
 ▼  ProfileIndex.__init__                                        profile_index.py:88-91
 │   _index_skills()                  → declarations   [source_type=skill]          :113
 │   _index_project_technologies()    → declarations   [source_type=project_technology] :138
 │   _index_employment()              → employment | mentions                        :160
 │        title  → demonstrates_title()                                              :193
 │        prose  → span_split → demonstrates_v5() → shows?                           :234
 │                 shows=True  → employment                                          :284
 │                 shows=False → mentions                                            :284
 │   _index_projects()                → mentions       [source_type=project]         :291
 │        ⚠ SUPPRESSED when the same project already declared the token             :318-322
 │
 ▼  TokenEvidence           three lists: declarations · employment · mentions
 │
 ▼  .tier   ← THE PROJECTION. Four values, computed from list emptiness alone.   evidence.py:219
 │     declarations and employment → declared_corroborated
 │     employment                  → employment_described
 │     declarations                → declared_only
 │     mentions                    → mentioned
 │
 │  ┌─ standard_for(name, *verified quotes) ── _DEMANDS_EMPLOYMENT regex ──┐   evidence.py:120
 │  │     any_evidence  |  professional_use                               │
 ▼  ▼
 satisfies(tier, standard) ──────────────────────────────── THE POLICY    evidence.py:135
 │     professional_use → {declared_corroborated, employment_described}
 │     any_evidence     → + declared_only            ← the V6 question lives here
 │
 ▼  rules._judge_token                                                    rules.py:293
 │     satisfies → MATCHED  (confidence 0.95 / 0.9 / 0.75 by tier)
 │     tier present, not satisfied → UNCERTAIN (0.5 declared_only, else 0.4)
 │     tier is None → MISSING (0.9)
 │
 ▼  scoring.score(judgments)                                              scoring.py:89
 │     coverage = matched / total, per axis; overall = 0.8·req + 0.2·pref
 │     ⚠ the tier is NOT an input. A declared_only MATCHED scores exactly
 │       what a declared_corroborated MATCHED scores.
 │
 ▼  service.create_match                                                  service.py:211-250
 │     MatchRequirementResult(status, confidence, evidence_standard, …)
 │     MatchEvidence(source_type, source_id, label, detail, tier, qualifies, note, quote)
 │     ⚠ qualifies = satisfies(item.tier, standard)  ← THE POLICY, CALLED A SECOND TIME
 │
 ▼  API  MatchEvidenceRead.tier: str | None   (plain str, no Literal)     schemas/match.py:64
 ▼  UI   TIER_LABELS[tier] ?? tier                     match-result.tsx:236-241, :274
```

Two things in that diagram are the whole of this document: the **⚠ suppression**
at `profile_index.py:318-322`, and the fact that **`satisfies()` sees only the
tier** — a four-value projection of a representation that knows much more.

---

## B. What is lost, exactly

### B.1 Loss 1 — an affirmative project description, dropped

`_index_projects` files project prose as `mentions`, except here:

```python
# A project that lists the technology explicitly has
# already produced a declaration; its description
# repeating the word adds nothing but noise.
if any(item.source_type == EVIDENCE_PROJECT_TECHNOLOGY
       and item.source_id == project.id
       for item in bucket.declarations):
    continue                                        # profile_index.py:318-322
```

The comment is right about the problem — without it, a chatty description would
look like repeated independent evidence — and wrong about the remedy: it drops
the sentence instead of recording it once.

Measured on `declared_only_v2`'s fixtures:

```
v2_project_contradiction  terraform   tier=declared_only  decl=2 mentions=0 employment=0
v2_project_contradiction  redis       tier=declared_only  decl=2 mentions=0 employment=0
v2_project_undeclared     postgresql  tier=mentioned      decl=0 mentions=1 employment=0
v2_project_undeclared     redis       tier=mentioned      decl=0 mentions=1 employment=0
```

The first two rows are the sharp end. That project's description says Terraform
was evaluated and abandoned and the Redis layer was never built — and because
the project also *declared* both, those sentences are discarded entirely. A
candidate who writes "I did not use this" grades `declared_only`; a candidate
who never declared it and merely described using it grades `mentioned`, which is
**lower**.

### B.2 Loss 2 — polarity, conflated inside `mentions`

`mentions` currently holds two different claims:

| what the profile said | how it arrives | polarity |
|---|---|---|
| a project names a technology | `_index_projects` | neutral — named, setting is unpaid |
| a role's clause says it was *not* done | `_file_experience_evidence`, `shows=False` | **negative** — explicitly refuted |

The tier reads both as `mentioned`. This is deliberate and documented —
`evidence.py:23-27` says collapsing `mentioned` into `declared_only` "would let
a cancelled proof-of-concept satisfy a production requirement" — but the
distinction it draws is between *mentioned* and *declared*, not between
*neutral* and *refuted*, and no consumer can tell the latter two apart.

### B.3 Loss 3 — the projection is narrower than the representation

This one is not a loss of data; it is a loss of *access*, and it is the most
consequential of the three.

`Evidence.source_type` already distinguishes a skills row (`skill`) from a
project technology row (`project_technology`), and both live in `declarations`.
So "declared in two different tables" is fully representable today — the
declared-only analysis's P5 policy was measured on exactly that field.

But `satisfies(tier, standard)` receives neither. It receives a four-value
string. **Every candidate V6 policy that the analysis found informative is
unexpressible through that signature**: P5 needs declaration sources, P6 needs
`Skill.years_of_experience`, P10 needs the requirement's wording, P11 needs
both. Not because the data is missing, but because the seam is too narrow.

### B.4 The rubric is already a specification

The second-rater rubric asks a human to classify each row into `S0`–`S6`. Those
codes are, exactly, the distinctions the representation cannot currently make:

| code | meaning | representable today? |
|---|---|---|
| `S0` | nothing names it | yes — `tier is None` |
| `S1` | declaration only | **no** — indistinguishable from `S2` and `S6` |
| `S2` | declaration + project description describes the work | **no** — suppressed (B.1) |
| `S3` | declaration + paid role describes the work | yes — `declared_corroborated` |
| `S4` | described in a project, declared nowhere | partly — `mentioned`, conflated with B.2 |
| `S5` | described in a paid role, declared nowhere | yes — `employment_described` |
| `S6` | declaration contradicted by a description | **no** — suppressed, or conflated |

Three of seven are unrepresentable. A human reviewer is being asked to make a
distinction the engine has no field for, which is the right order to do it in —
but it means the review's answers cannot be acted on until the representation
can hold them.

---

## C. Should evidence stay a four-tier model?

**Yes — and it should stop being the representation.**

The four tiers should survive as a *derived summary*, and a richer structure
should sit underneath them. Three independent reasons, in descending order of
how binding they are:

1. **The tier vocabulary is frozen ground truth in an active benchmark.**
   `non_doing_v2` pins **53 per-token expectations**, each naming a tier and an
   `attributed_employment` boolean, plus 40 final verdicts
   (`evaluation/nondoing/adapter.py:78-79` reads `bool(evidence.employment)` and
   `evidence.tier`). Renaming, reordering or removing a tier invalidates that
   dataset and forces a `non_doing_v3` — the project's own rule, and a large
   cost for a representation change that need not incur it.

2. **The tier is a database CHECK constraint, a hand-written TypeScript tuple
   and an exhaustive UI label map.** `ck_match_evidence_tier` (migration
   `7b4c1e9d2f80`) hard-codes the four literals;
   `packages/shared/src/index.ts:203-208` repeats them in semantic strength
   order; `match-result.tsx:236-241` maps each to user-facing copy. Adding a
   fifth value is a four-repository change with a migration. Adding an in-memory
   field is none of that.

3. **The codebase has already reasoned this out once, for verdicts.**
   `models/match.py:96-101`: *"Three, not four. A fourth state has been
   deliberately resisted: every candidate for one ... is really a statement
   about CONFIDENCE or about EVIDENCE, and both of those are already
   first-class."* The same argument applies here in mirror image: every
   candidate fifth tier ("declared and demonstrated unpaid", "declared and
   refuted") is really a statement about *which evidence exists*, which a richer
   evidence structure holds directly and a coarser ordinal cannot.

So: **tier stays exactly as it is, as a projection. The representation grows
beneath it.** A projection can be widened later without moving; an ordinal
cannot be widened at all without a migration and a renegotiated benchmark.

---

## D. Current vs proposed representation

### D.1 Current

```python
@dataclass
class TokenEvidence:
    declarations: list[Evidence]   # skill rows AND project-technology rows
    employment:   list[Evidence]   # paid roles whose description demonstrates it
    mentions:     list[Evidence]   # projects, AND clauses that say it was not done

    @property
    def tier(self) -> str | None:  # four values, from emptiness alone
```

### D.2 Proposed — additive, verdict-neutral by construction

```python
@dataclass
class TokenEvidence:
    # --- unchanged. The tier substrate. Every existing reader keeps working. ---
    declarations: list[Evidence]
    employment:   list[Evidence]
    mentions:     list[Evidence]

    # --- new. Nothing reads these until a policy is chosen to. ---

    #: Affirmative, non-employment description of the work being done — the
    #: project prose that profile_index.py:318-322 currently discards. NOT
    #: `mentions`: mentions already means "named, or named as not done", and
    #: merging them would let a cancelled proof-of-concept look like
    #: corroboration, which is the collapse evidence.py exists to prevent.
    corroborations: list[Evidence]

    #: Clauses that explicitly deny, abandon or refute the work. Filed here IN
    #: ADDITION to `mentions`, never instead of it — the duplication is the
    #: price of leaving `tier` byte-identical, and is the one piece of this
    #: design that a later normalisation (D.4) would remove.
    disclaimers: list[Evidence]

    @property
    def tier(self) -> str | None:
        ...  # UNCHANGED. Reads the original three lists only.
```

And on `Evidence`, one optional field, set only where it is known:

```python
    #: Where the work happened, when the profile says. Lets a rule ask "was
    #: this paid?" without re-deriving it from which list the item is in.
    setting: str | None = None      # "employment" | "project" | None
```

### D.3 What the proposal preserves

Every arrangement the brief names becomes directly expressible:

| what must be preserved | how | today |
|---|---|---|
| declaration | `declarations` where `source_type == "skill"` | ✓ already |
| project declaration | `declarations` where `source_type == "project_technology"` | ✓ already |
| project corroboration | `corroborations` | **✗ discarded** |
| employment corroboration | `employment` | ✓ already |
| explicit non-use / abandonment | `disclaimers` | **✗ conflated into `mentions`** |
| prose-only evidence | `employment` / `corroborations` with empty `declarations` | partly |

And the rubric's codes become computable, which is the test of whether the
design is adequate:

```
S0  tier is None
S1  declarations and not (employment or corroborations or disclaimers)
S2  declarations and corroborations and not employment
S3  declarations and employment
S4  corroborations and not declarations          (project prose only)
S5  employment and not declarations              (paid prose only)
S6  declarations and disclaimers
```

### D.4 The richer alternative, named and not recommended

A fully normalised model would drop the three lists for one `items:
list[Evidence]`, each carrying `act` (declared | described), `setting`
(employment | project | none) and `polarity` (affirmative | neutral | refuted),
with `tier` computed from facets. It is cleaner, it removes D.2's duplication,
and it is the right long-run shape.

It is **not** recommended for V6, because it rewrites the substrate that
`tier`, `best()`, `all()`, `has_any`, `employment_mentioning()` and
`evaluation/nondoing/adapter.py` all read, in a change whose entire value is
supposed to be that no verdict moves. Do the additive version, get the
benchmarks to prove neutrality, and normalise later if the facets earn it.

---

## E. The four concerns, separated

The brief asks for these to be separated explicitly. They are currently
separated in three places out of four, and the exception is the problem.

| concern | question it answers | lives in | state |
|---|---|---|---|
| **Evidence representation** | what does the profile say? | `evidence.py` dataclasses, `profile_index.py` | sound, but lossy (§B) |
| **Requirement demand** | what does the posting ask for? | `standard_for()`, `_DEMANDS_EMPLOYMENT`, the two standards | **cleanly separated** — read from the posting's own verified words, never from the profile |
| **Evidence sufficiency policy** | is this evidence enough for that demand? | `satisfies()` | **entangled with the projection**, and called from two places |
| **Scoring** | how do verdicts aggregate? | `scoring.py` | **cleanly separated** — takes finished judgments, never re-judges (`scoring.py:89-99`) |

### E.1 The entanglement, precisely

`satisfies(tier: str | None, standard: str) -> bool` couples the policy to the
*projection* rather than to the *evidence*. Widening the policy therefore
requires widening the projection — which §C says is the expensive direction —
unless the signature changes.

It is called from exactly two places:

- `rules.py:293` — the verdict.
- `service.py:232` — the stored `qualifies` flag, **re-derived at persistence
  time** from `item.tier` and `judgment.evidence_standard`, which is all a
  persisted row has.

That second call site is the binding constraint on any richer policy. A rule
that reads declaration counts, stated years or requirement wording cannot be
re-derived from a `MatchEvidence` row, because the row does not carry those
things. Today the two call sites agree only because both see the same two
values.

**The fix is not to widen the row. It is to stop asking the question twice.**
`qualifies` is a fact about a judgment that has already been made; it should be
computed once, in `rules`, and carried to persistence — not recomputed from a
narrower view of the same evidence.

---

## F. Consumer trace and compatibility

Every reader of the evidence representation, inside and outside the matching
service.

### F.1 Inside `app/services/matching/`

| consumer | reads | impact of D.2 |
|---|---|---|
| `evidence.py` `tier`/`has_any`/`best()`/`all()` | the three lists | none — unchanged |
| `profile_index.py` writers | writes all lists | **changes** (~10 lines: stop dropping, route polarity, set `setting`) |
| `profile_index.employment_mentioning()` :356 | `.employment` only | none |
| `rules.py` `_judge_token` :291-326 | `tier`, `best()` | none until a policy reads facets |
| `rules.py` `_judge_technology_duration` :893-1013 | `has_any`, `employment_mentioning`, `skill_for().years_of_experience`, `best()` | none |
| `rules.py` `_judge_unmeasured_non_technology` :1152-1178 | `has_any` | none |
| `service.py` :232 | `satisfies(item.tier, …)` | **changes in Phase 2** (E.1) |

### F.2 Outside the matching service

| consumer | what it pins | breaks if… |
|---|---|---|
| `models/match.py:173-183` `EVIDENCE_TIERS` | the four literals; renders into `_TIER_SQL` | a tier value is added/renamed |
| `models/match.py:146-160` `EVIDENCE_SOURCES` | six source types → `ck_match_evidence_source_type` | a new evidence **kind** is persisted |
| migration `7b4c1e9d2f80` | `ck_match_evidence_tier` = `tier IS NULL OR tier IN (…4 literals…)`; `ix_match_evidence_tier` | a tier value is added → **migration required** |
| `match_evidence.qualifies` | `Boolean NOT NULL` | "explicitly refuted" is wanted as a third state → **schema change** |
| `schemas/match.py:64` | `tier: str \| None` — plain `str`, no `Literal` | never — **and that is the hazard**: a new value passes validation silently |
| `packages/shared/openapi.json`, `api-schema.ts` | `string` | never — no safety net either |
| `packages/shared/src/index.ts:203-208` `MATCH_EVIDENCE_TIERS` | the hand-written contract, **in semantic strength order** | a new tier must be *placed*, not appended |
| `apps/web` `match-result.tsx:236-241` `TIER_LABELS` | `Record<MatchEvidenceTier, string>` | a new tier → **compile error** (loud, good) |
| `apps/web` `match-result.tsx:245` | `evidence.tier as MatchEvidenceTier` — **unchecked cast**, falls back to `?? tier` | a backend newer than the frontend renders the raw `snake_case` token to the user |
| `evaluation/nondoing/adapter.py:78-79` | `bool(evidence.employment)` and `evidence.tier`, per token | either changes → `non_doing_v2`'s 53 expectations invalid → `non_doing_v3` |
| `evaluation/benchmark/adapter.py:99-105` `_rule_label` | `source_type` sets `{skill, project_technology}`, `{experience}`, `{project}` | a new source_type changes reported rule labels (reporting only) |
| `tests/test_matching_evidence_tiers.py` | 11 tier assertions, 3 standard assertions | a tier or confidence changes |
| `matching_real_v1`, `declared_only_v1` | 169 + 94 human verdicts | any **policy** change (expected); a representation-only change must not move them |
| `apps/web` match page tests | literal UI copy strings | tier wording changes |

### F.3 The version-skew hazard, called out separately

`match-result.tsx:245` casts an unvalidated string into a closed union, and
`TIER_LABELS[tier] ?? tier` renders the raw token when the lookup misses. A
backend deployed ahead of the frontend would therefore show a user
`project_corroboration` in a badge. This is a **pre-existing** defect, harmless
today because the value set has never changed, and it becomes user-visible the
first time it does. It should be fixed — a humane fallback, or a
parse-don't-cast at the API boundary — *before* any change that could introduce
a new value, not as part of it.

---

## G. Effect per requirement category

| category | reaches `TokenEvidence`? | effect of D.2 (representation only) | what a later policy could newly read |
|---|---|---|---|
| **skills** | yes — `judge_token_requirement` → `_judge_token` | none; tier and verdict identical | S1/S2/S6 separation; declaration multiplicity |
| **technologies** | yes — **the same function**, same args (`engine.py:196-206`) | none | identical to skills; there is no separate technology policy and there should not be |
| **experience with a technology** | yes — `_judge_technology_duration` | none; it reads `employment` and `Skill.years_of_experience`, neither of which moves | a dated project corroboration becomes *visible* — whether it may count as duration is policy (reviewer rule 7 says no) |
| **experience, generic** | no — `_judge_total_employment` reads dated spans | none | none |
| **certifications** | **no** — `judge_certification` reads `profile.certifications` | none, and must stay none | none. Any V6 scope listing certifications is mis-scoped |
| **education** | **no** — `judge_education` reads `profile.education` | none, and must stay none | none |
| **required requirements** | `is_required` carried, never consulted by `satisfies` | none | the axis remains available and is *empirically refuted* as a discriminator |
| **preferred requirements** | same | none | same |

Two invariants worth stating as design constraints rather than observations:
**certifications and education must not acquire a tier** (there is no stronger
or weaker way to hold a degree — `models/match.py:489-492` already says so), and
**skills and technologies must not diverge** (they are one code path; any
difference between them should come from the posting's wording, never the
category).

---

## H. Minimum schema and code changes

Ordered by cost. The first row is the whole of the representation fix.

| change | files | migration? | `ALGORITHM_VERSION`? | verdicts move? |
|---|---|---|---|---|
| Add `corroborations`, `disclaimers`, `Evidence.setting` | `evidence.py` | no | no | no |
| Stop dropping project prose; route polarity; set `setting` | `profile_index.py` (~10 lines) | no | no | **no — provable** |
| Compute `qualifies` once in `rules`, carry it to persistence | `rules.py`, `service.py` | no | no | no |
| Expose facets on the judgment for explanation text | `rules.py`, `schemas/match.py` | no | no | no |
| **A policy that reads facets** | `evidence.py` `satisfies` signature, `rules.py` | no | **yes** | **yes, by design** |
| Persist a new evidence **kind** | + `models/match.py`, new migration, `shared/index.ts`, `TIER_LABELS`, fixtures | **yes** | yes | yes |
| Add a fifth **tier** | all of the above + `non_doing_v3` | **yes** | yes | yes |

The last row is the one to avoid, and §C is the argument for why it is
avoidable.

---

## I. Proposed implementation sequence

Each phase is independently shippable and independently revertible. Phases 1-3
are verdict-neutral and need no version bump; the gate between 3 and 4 is the
human review, not a code readiness question.

**Phase 1 — keep what is currently discarded.** Add the two lists and
`setting`. Change `_index_projects` to append to `corroborations` instead of
`continue`, and to file a non-doing clause into `disclaimers` as well as
`mentions`. `tier` untouched.
*Exit criteria:* all four benchmarks byte-identical (§K T1); `non_doing_v2`'s 53
tier + `attributed_employment` expectations unchanged; the 11 tier assertions
pass unmodified.

**Phase 2 — close the policy seam.** Compute `qualifies` in `rules` where the
full evidence is in scope; have `service.py` store the carried value instead of
re-deriving it. `satisfies()` keeps its current signature and behaviour.
*Exit criteria:* same as Phase 1, plus a test asserting `satisfies` has exactly
one call site.

**Phase 3 — make the facets legible.** Surface the S-code-equivalent shape in
the judgment's explanation and, if wanted, in the API — so a user reading
"listed in your profile" can see whether anything else in their profile
supports it. Still no policy change.
*Exit criteria:* verdicts unchanged; new explanation strings reviewed.

**Phase 4 — a policy, once one is chosen.** Widen `satisfies` to take the
evidence and the demand. Bump `ALGORITHM_VERSION`. Re-measure every benchmark
and expect movement. **Blocked on the human review**, not on engineering.

**Phase 5 — optional normalisation (D.4).** Only if the facets prove
load-bearing. Not scoped here.

---

## J. Unresolved policy decisions

None of these is decided by this document, and none should be decided from the
existing benchmark alone. Each names the rows that will inform it.

| # | question | informed by |
|---|---|---|
| 1 | Does a project description that corroborates a declaration lift it above a bare one? (`S1` vs `S2`) | `declared_only_v1` R07/R09/R10/R12; `declared_only_v2` `v2c03` |
| 2 | Does a description that *contradicts* a declaration sink it below `declared_only`? (`S6`) | `v2c03` — Terraform and Redis declared twice, described as not used |
| 3 | Does prose-only evidence outrank a bare declaration? (`S4`/`S5` vs `S1`) | `v2c04`, `v2c01` |
| 4 | Does a self-stated duration count as evidence — and may it rule a requirement *out* as well as in? | R23/R24/R26; `v2c05` (12 stated years against a 5-year career) |
| 5 | Does exposure wording lower the bar, and what governs a line containing both an exposure word and a production word? | `v2c01`, `v2c07`; `docs/matching-v6-exposure-wording-collision.md` |
| 6 | Should the required/preferred axis ever change the evidence bar? | empirically refuted in `docs/matching-v6-declared-only-analysis.md` §D; formally still open |
| 7 | Should `qualifies` gain a third state ("explicitly refuted") rather than sharing `false` with "insufficient"? | follows from 2; costs a schema change |
| 8 | Does a declaration corroborated only by a project ever satisfy `professional_use`? | reviewer rule 7 says no; no row currently tests it |

---

## K. Test strategy

**T1 — verdict-neutrality harness (the load-bearing one).** Capture the full
text report of all four benchmarks (`matching_real_v1`, `declared_only_v1`,
`non_doing_v2`, the `matching_v2` regression set) before the change, and diff
after. For Phases 1-3 the diff must be **empty** — not "equivalent", empty. This
is the technique `docs/matching-v5-release.md` §2 already used to prove the v4→v5
release changed nothing but a version string, and it is the only evidence that
separates a representation change from a policy change.

**T2 — tier purity.** A property test asserting `tier` is a pure function of
`(declarations, employment, mentions)`: populate `corroborations` and
`disclaimers` arbitrarily and assert the tier does not move. This is what stops
Phase 1 from becoming Phase 4 by accident.

**T3 — `non_doing_v2` golden.** Its 53 `(token, attributed_employment, tier)`
expectations and 40 verdicts are an external contract on the representation. Run
it in CI and treat any movement as a release-blocking event requiring a dataset
version bump, never a dataset edit.

**T4 — single policy seam.** After Phase 2, a test that fails if `satisfies` is
called from more than one module. Cheap, and it prevents the exact drift that
put a policy call inside the persistence layer in the first place.

**T5 — shape coverage.** Unit tests that construct each of `S0`–`S6` and assert
the new lists are populated and empty as specified. `declared_only_v2`'s
fixtures are the natural source of shapes, since they were authored to be
exactly these.

**T6 — frontend resilience, before any new value ships.** A test that an
unrecognised tier renders a humane fallback rather than the raw token
(`match-result.tsx:245`, §F.3). This is worth doing whether or not V6 proceeds.

**T7 — migration round-trip**, only if Phase 6/7 of §H is ever reached:
`upgrade → check → downgrade -1 → upgrade`, plus a test that a persisted
`matching_v5` row is unreadable-but-intact under the new constraint, mirroring
`test_a_historical_v4_result_is_untouched_and_v5_creates_its_own_row`.

**What is deliberately not in the strategy:** any test that asserts a *better*
benchmark score. A policy change is expected to move numbers in both directions,
and the project's own comparison order — false matches first, raw agreement last
— is the frame for judging it, not a threshold in a test.

---

## What this document does not do

- It does not choose a declared-only policy, and Phase 4 is explicitly blocked
  on the human review rather than on any number in a benchmark.
- It does not design exposure-wording logic. §J item 5 records the question;
  `docs/matching-v6-exposure-wording-collision.md` records why it is harder than
  it looks.
- It does not create `matching_v6`, touch `ALGORITHM_VERSION`, or modify
  anything under `app/services/matching/`.
- It does not propose semantic matching, embeddings or a model. Every mechanism
  above is a list, a boolean, or a field on a dataclass.
- It does not supersede `docs/matching-v6-s1-s2-representation-gap.md`, which
  specifies the narrowest possible version of Phase 1 in more detail. Where the
  two differ, that document is the tighter scope and this one is the wider
  context.
