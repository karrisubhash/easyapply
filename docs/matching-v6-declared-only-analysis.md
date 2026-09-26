# Declared-only evidence: policy analysis

Analysis only. Nothing under `app/services/matching/` changed to produce this
document — `satisfies()`, `TokenEvidence.tier`, `_MATCH_CONFIDENCE`,
`_DEMANDS_EMPLOYMENT` and `ALGORITHM_VERSION` (`matching_v5`) are exactly as
they were, and no verdict-changing rule is proposed as shipped work below. No
LLM, embedding, similarity model or new heuristic appears anywhere in it: every
mechanism weighed is a closed list, a count of rows the index already holds, or
a rearrangement of the existing tier/standard comparison.

The brief: determine how `declared_only` evidence currently affects each
requirement category, trace it end to end, find every real-data case it
decided, build an independent benchmark whose expectations were authored before
the matcher ran, quantify the trade-offs of changing the rule, and say whether
Matching V6 should change it.

Two artifacts accompany this document:

```
evaluation/datasets/declared_only_v1/    a new 94-judgment benchmark (6 profiles, 4 postings)
docs/matching-v6-declared-only-analysis.md   this file
```

Everything measured below is reproducible with:

```
python -m evaluation.benchmark.runner --dataset matching_real_v1
python -m evaluation.benchmark.runner --dataset declared_only_v1
```

---

## A. The trace, exactly

### A.0 Where a declaration comes from

Exactly two writers fill `TokenEvidence.declarations`, and they are
indistinguishable downstream:

| source | code | what the user did |
|---|---|---|
| a `Skill` row | `profile_index.py:113-136` | typed a name into the skills list |
| a `ProjectTechnology` row | `profile_index.py:138-158` | attached a technology to a project |

A project *description* is different: it writes to `mentions`
(`profile_index.py:291-330`), and — load-bearing for §C — a description's
mention is **suppressed** when the same project already declared that
technology (`profile_index.py:318-322`). So a project that both lists Docker
and narrates using it produces one declaration and no mention, which is
indistinguishable from a project that lists Docker and says nothing.

`TokenEvidence.tier` (`evidence.py:219-229`) then reads those three lists:

```
declarations and employment  -> DECLARED_CORROBORATED
employment                   -> EMPLOYMENT_DESCRIBED
declarations                 -> DECLARED_ONLY
mentions                     -> MENTIONED
```

`DECLARED_ONLY` is therefore reached by **one** condition: the profile asserts
the token in at least one declaring field, and no paid role's description
demonstrates it. How many times it was asserted, in how many different fields,
with or without a stated duration, is not carried past this property.

### A.1 What a requirement demands

`standard_for()` (`evidence.py:120-132`) reads the requirement's own name plus
every *verified* provenance quote (`engine.py:_quotes`) against one regex,
`_DEMANDS_EMPLOYMENT` (`evidence.py:92-100`), which fires on `production`,
`professional(ly)`, `commercial(ly)`, `industry`, `enterprise`, `hands-on`,
`real-world`, `at scale`, `demonstrable`, `demonstrated`, `proven`,
`track record`, `shipped`, `not just in a course`. Two standards exist, and
`satisfies()` (`evidence.py:135-145`) is the whole policy:

```python
if standard == STANDARD_PROFESSIONAL_USE:
    return tier in (TIER_DECLARED_CORROBORATED, TIER_EMPLOYMENT_DESCRIBED)
return tier in (TIER_DECLARED_CORROBORATED, TIER_EMPLOYMENT_DESCRIBED, TIER_DECLARED_ONLY)
```

**`satisfies()` takes no `is_required` parameter.** It cannot: the axis is not
passed to it and is not in scope. `is_required` travels through every judging
function (`rules.py:172, 250, 336, 501, 788, 887, 1022, 1140, 1200, 1428`) and
is used for exactly two things — it is stored on `RequirementJudgment`, and
`scoring._count` (`scoring.py:79-86`) partitions on it. This is consistent with
`docs/matching.md:365-371` ("the two axes mix in exactly one place, the overall
score, and they mix gently"), and it means **required and preferred requirements
are held to an identical evidence bar today**. §D measures whether they should
be.

### A.2 The seven categories, and which ones `declared_only` can reach

| category | reads `TokenEvidence`? | what `declared_only` does |
|---|---|---|
| **skills** | yes — `judge_token_requirement` → `_judge_token` (`rules.py:167-326`) | MATCHED at confidence 0.75 under `any_evidence`; UNCERTAIN at 0.5 under `professional_use` |
| **technologies** | yes — the *same* function, same arguments (`engine.py:196-206`) | identical to skills in every respect |
| **certifications** | **no** — `judge_certification` (`rules.py:332-458`) reads `profile.certifications` and compares canonical names | nothing. Unreachable. |
| **education** | **no** — `judge_education` (`rules.py:495-624`) reads `profile.education` | nothing. Unreachable. |
| **experience, no stated minimum** | yes — routes to `_judge_token` (`rules.py:817-835`) | same as skills |
| **experience, stated minimum, technology subject** | yes — `_judge_technology_duration` (`rules.py:881-1013`) | three outcomes; see §A.3 |
| **experience, stated minimum, generic subject** | no — `_judge_total_employment` reads dated spans only | nothing |
| **qualifications / location** | no — always UNCERTAIN, or employment-derived | nothing |

Two findings fall straight out of this table. **Certifications and education are
entirely unaffected by declared-only evidence** — they never consult the tier,
and no declared-only judgment in either benchmark carries those categories.
And **skills and technologies are not two policies but one**: `engine.py:169-206`
sends both through `judge_token_requirement` with the same `profile` and the
same `quotes`, so any difference in outcome between a "skill: Docker" and a
"technology: Docker" requirement comes from the posting's wording alone, never
from the category.

### A.3 The one place `declared_only` can produce MISSING

`_judge_technology_duration` (`rules.py:881-1013`) is the only path where a
declared-only claim settles a requirement in the negative:

```
no evidence at all                          -> MISSING   (rules.py:896)
dated employment demonstrates the token     -> MATCHED / MISSING on merged years
skill row carries years_of_experience
    AND standard == any_evidence:
        stated >= minimum                   -> MATCHED  confidence 0.8  (rules.py:963)
        stated <  minimum                   -> MISSING  confidence 0.8  (rules.py:979)
otherwise                                   -> UNCERTAIN confidence 0.3 (rules.py:996)
```

The asymmetry is worth naming because nothing in the code comments it: the
candidate's own unverified number is **believed in both directions**. It can
match a five-year bar and it can rule out a three-year bar. The
`standard == STANDARD_ANY_EVIDENCE` guard at `rules.py:960` is what keeps it out
of production-worded requirements, and it is the reason most real duration
requirements never reach this branch at all — `professional` is in
`_DEMANDS_EMPLOYMENT`, so "4+ years of professional Python" sets the higher bar
and falls through to UNCERTAIN.

### A.4 Declaration → verdict → score → match

```
Skill / ProjectTechnology row
  -> ProfileIndex.declarations            profile_index.py:123 / :150
  -> TokenEvidence.tier = DECLARED_ONLY   evidence.py:226
  -> standard_for(name, *verified quotes) evidence.py:120
  -> satisfies(tier, standard)            evidence.py:135      <-- the entire policy
  -> MATCHED @0.75 | UNCERTAIN @0.5       rules.py:293-326
  -> AxisCounts.matched++                 scoring.py:81-86
  -> coverage = matched / total           scoring.py:62-65
  -> overall = 0.8*required + 0.2*preferred  scoring.py:74-76
```

A declared-only MATCHED is worth the same as a `declared_corroborated` MATCHED
at every stage after `satisfies()`. The tier survives only in `confidence`
(0.75 vs 0.95) and in the explanation string; `scoring.py` never sees it. So a
profile can reach a high overall score on claims alone, and the number carries
no trace of how it got there — `confidence` is per-requirement and is
documented as confidence *in the verdict*, not match strength
(`rules.py:35-38`).

---

## B. What `declared_only` decided in `matching_real_v1`

169 judgments, `matching_v5`, raw agreement **0.911** (154/169), uncontested
0.930. Of those 169, the operative tier is `declared_only` in exactly **7**.
All seven, classified as the brief asks:

| # | case / requirement | profile | employment | axis | standard | expected | actual | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | `c05` / `r_docker` | `p_career_changer` | 8.9y | required | any | matched | matched | **correct MATCHED** |
| 2 | `c10` / `r_ml_sql` | `p_data_senior` | 11.6y | required | any | matched | matched | **correct MATCHED** |
| 3 | `c14` / `r_pk_k8s` | `p_junior_stuffed` | 0.6y | required | professional | uncertain | uncertain | **correct UNCERTAIN** |
| 4 | `c12` / `r_g_k8s` | `p_junior_stuffed` | 0.6y | preferred | any | uncertain | matched | **incorrect MATCHED** |
| 5 | `c13` / `r_k8s` | `p_junior_stuffed` | 0.6y | preferred | any | uncertain | matched | **incorrect MATCHED** |
| 6 | `c14` / `r_pk_tf` | `p_junior_stuffed` | 0.6y | required | any | uncertain | matched | **incorrect MATCHED** |
| 7 | `c14` / `r_pk_aws` | `p_junior_stuffed` | 0.6y | preferred | any | uncertain | matched | **incorrect MATCHED** |

- **incorrect UNCERTAIN: none.**
- **incorrect MISSING: none.** The three false-missings in this benchmark
  (`c09/r_fs_senior`, `c15/r_docker`, `c16/r_g_intern`) all have tier `None` —
  nothing in the profile mentions the token at all — and are unrelated.
- **The benchmark reports 4 false matches. All 4 are rows 4-7.** Declared-only
  evidence is the sole source of invisible error in this benchmark.

This is the failure `docs/matching.md:625-631` already records — "Four false
matches remain, all of the same shape: a skills-list entry with nothing
corroborating it... Closing that gap means weighing a claim against the
profile's overall substance, which is a different kind of rule from anything
here" — and `matching.md:664-666` lists as a known limit. The aspect score is
`uncorroborated_claim 0.200 (1/5)`, the worst of any aspect with more than one
observation, and it has not moved since v2 (`matching.md:622`: `0/5 → 1/5`).

### B.1 Why this evidence cannot, on its own, justify a policy

Six of the seven rows come from **one profile**. The two correct MATCHEDs have
8.9 and 11.6 years of employment; all four errors have 0.6. Read casually, that
is a clean seniority signal — and it is entirely confounded, because
`p_junior_stuffed` is also the only profile in the dataset whose declarations
have nothing whatsoever beside them. The reviewer's own rationales name both
explanations without separating them: *"For a graduate with one six-month
internship this reads as a list of things encountered rather than used"* and
*"nothing else in the profile touches it — not the internship, not the capstone,
not any project"*.

Fitting a years threshold to these seven rows would be curve-fitting to a
single profile. §C exists to break that confound.

---

## C. The independent benchmark

`evaluation/datasets/declared_only_v1/` — 6 profiles, 4 postings, 14 pairings,
**94 judgments**, 27 of them declared-only. Design, reviewer standard and
limitations are in that directory's README.

**Independence protocol.** Profiles and postings were authored first; the
expectations were written by reading each pairing as two documents against the
thirteen reviewer rules fixed in `matching_real_v1/README.md`; the file was then
frozen and hashed **before the matcher was executed against this dataset even
once**:

```
sha256(ground_truth.json) = 0f96c6d6374ca781ff4372f67329ec9c83af4b3af4175edfa23abcb2786b1f04
frozen 2026-09-20T17:50:43Z   — unchanged after every run reported below
```

The only engine property consulted while authoring was the curated technology
vocabulary, so that a case would measure declared-only policy rather than
vocabulary coverage.

**What it crosses.** Employment duration against corroboration-beside-the-claim,
which `matching_real_v1` holds constant together. The decisive fixture is
`d_senior_bare`: twelve years, three detailed role descriptions, and three
claims (Kubernetes, Terraform, Rust) that those descriptions never touch — a
profile nobody would call thin, saying about *this claim* precisely what the
graduate's stuffed list says.

### C.1 Result

Raw agreement **0.809** (76/94), uncontested 0.867 (65/75), evidence
correctness 1.000, required agreement 0.797, preferred 0.833. Aspect
`uncorroborated_claim` 7/15. **10 false matches, and all 10 are declared-only
MATCHEDs.**

The 27 declared-only judgments:

| outcome | n | where |
|---|---|---|
| **correct MATCHED** | 9 | project-corroborated claims (`c04`, `c05`, `c14`), stated-years claims (`c12/snow`, `c13/k8s`), exposure-worded asks (`c01/tf`, `c04/tf`) |
| **incorrect MATCHED** | 10 | `c01`, `c02`×2, `c07`×2, `c08`×2, `c10`, `c12`×2 |
| **correct UNCERTAIN** | 6 | every `professional_use`-worded ask, plus duration asks with no number to read |
| **correct MISSING** | 2 | `c12/rq_da_k8s3`, `c13/rq_pf_k8s3` — stated 2 years against a 3-year bar |
| **incorrect UNCERTAIN** | 0 | — |
| **incorrect MISSING** | 0 | — |

Across both benchmarks: **263 judgments, 34 declared-only, 14 false matches,
14/14 of them declared-only, and zero declared-only errors in the other
direction.** The defect is systematic and one-directional: the rule never
hedges where it should answer, only answers where it should hedge.

### C.2 The confound, broken

The ten errors by profile employment:

```
0.5y  d_grad_bare     ***        (3 errors)
6.5y  d_mid           **         (2)
8.0y  d_changer       **         (2)
10.2y d_senior_years  **         (2)
12.0y d_senior_bare   *          (1)
```

**Five of the ten errors are on profiles with 6.5 years or more**, including
`c10/rq_pf_k8s` — the twelve-year principal engineer whose bare Kubernetes claim
the matcher read as MATCHED against a required platform line. Meanwhile five of
the nine *correct* declared-only MATCHEDs are on profiles with one year or less
(`d_grad_project`), where a long-running project corroborates the claim.

Seniority does not separate the classes. It anti-separates them here: the junior
profile with a project is more often right than the senior profile without one.

---

## D. Policy simulation

Each candidate policy is applied to the 263 judgments from both benchmarks.
Only rows whose operative tier is `declared_only` can move; every other verdict
is held exactly as `matching_v5` produced it, so the difference is attributable
to the declared-only rule and nothing else. No policy can see a human verdict.
`lost matches` is cumulative and includes the 11 true matches the engine already
loses to unrelated failure modes — the *marginal* cost of a policy is the column
minus 11.

| policy | agreement (263) | declared-only (34) | uncontested d-o (20) | false matches | lost matches |
|---|---|---|---|---|---|
| **P0 status quo** — settles any non-production ask | 0.8745 | 0.588 | 0.55 | **14** | 11 |
| P1 — never settles anything | 0.8859 | 0.676 | 0.75 | 0 | 22 |
| P2 — settles preferred only | 0.8783 | 0.618 | 0.50 | 8 | 16 |
| P3 — settles required only *(control)* | 0.8821 | — | — | 6 | 17 |
| P4 — needs ≥1y total employment | 0.8973 | 0.765 | 0.90 | 7 | 12 |
| P4 — needs ≥2y / ≥3y / ≥5y | 0.8783 | — | — | 7 | 17 |
| P4 — needs ≥8y | 0.8859 | 0.676 | 0.80 | 5 | 17 |
| P4 — needs ≥10y | 0.8859 | 0.676 | 0.80 | 3 | 19 |
| P5 — needs ≥2 declaration sources | 0.9011 | 0.794 | 0.90 | 1 | 17 |
| P6 — needs a stated duration | 0.8859 | 0.676 | 0.75 | 2 | 20 |
| P7 — needs bare-declaration fraction ≤0.2 | 0.8859 | — | — | 0 | 22 |
| P7 — ≤0.34 / ≤0.5 / ≤0.67 | 0.878-0.897 | — | — | 2 / 7 / 7 | 22 / 17 / 12 |
| P8 — P5 **or** P6 | 0.9011 | 0.794 | 0.90 | 3 | 15 |
| P10 — posting's words ask only for exposure | 0.8935 | 0.735 | 0.75 | **0** | 20 |
| **P11 — P10 or P8** | **0.9087** | **0.853** | **0.90** | 3 | **13** |
| P12 — "nice to have"/"helpful" wording *(control)* | 0.8631 | 0.500 | 0.45 | 8 | 20 |

Read in the order `matching_real_v1/README.md` prescribes — false matches
first, then error direction, then aspect agreement, and raw agreement last.

**What the table refutes.**

- **A seniority threshold is refuted, not merely unsupported.** The P4 family is
  dominated at every N tested: no threshold reaches P5's or P11's agreement, and
  each one that removes a false match removes a true one with it. The reason is
  §C.2 — errors occur across the full range of employment, so the cut cannot be
  placed anywhere useful. The best member of the family (≥1y, 0.8973) is
  effectively "has ever held a job", not a seniority rule at all, and it still
  leaves 7 false matches.
- **The required/preferred axis is refuted as a discriminator.** Errors split
  8 preferred / 6 required. P2 and P3 both score below policies that ignore the
  axis, and P12 — which keys on the soft wording that *marks* a preferred line
  ("nice to have", "helpful", "bonus") — is the worst policy measured, below the
  status quo. Whether a requirement is optional says nothing about whether a
  claim about it is believable.
- **Profile substance (P7) does not survive being made precise.** The share of a
  profile's declarations that no role corroborates is the closest deterministic
  reading of `matching.md:629-631`'s "weighing a claim against the profile's
  overall substance", and every threshold for it is dominated by P5.

**What the table supports.** Two signals carry real information, and both are
already in the data:

1. **The posting's own words asking only for exposure** ("any Terraform
   exposure", "familiarity with", "awareness of") — the exact mirror of
   `_DEMANDS_EMPLOYMENT`, in the opposite direction. On its own it removes every
   false match in both benchmarks (P10: 14 → 0) while raising agreement.
2. **A second, independent assertion** — the token declared in a project's
   technology field *as well as* the skills list, or carrying its own stated
   duration (P5/P6/P8).

Combined (P11) they give the best measured result: agreement 0.8745 → 0.9087,
false matches 14 → 3, at a marginal cost of **2** true matches. Both of those
two losses are uncontested rows from `matching_real_v1` — `c05/r_docker` (a
career changer's Docker, declared only as a project technology) and
`c10/r_ml_sql` (an eleven-year ML engineer's bare SQL). All three of P11's
remaining false matches are *contested* rows, i.e. ones where this document's
own rater flagged that a second reader could land elsewhere.

### D.1 The duration sub-policy, measured separately

The `rules.py:957-993` stated-years branch touches 6 declared-only rows and is
independent of everything above:

| | agreement (263) | false matches |
|---|---|---|
| D0 — believe the number in both directions *(status quo)* | 0.8745 | 14 |
| D1 — distrust it in both directions | 0.8707 | 13 |
| D2 — ceiling only: may rule a requirement out, never in | 0.8783 | 13 |

D2 is one judgment better than the status quo over 263. **Six observations
cannot justify a change to a rule that currently produces two correct MISSINGs**,
and the asymmetry D2 encodes — disbelieving a number for MATCHED while trusting
it for MISSING — is a position that needs arguing on its merits, not adopting on
a one-row margin. Recorded as measured; not recommended.

### D.2 What this does to the score a user sees

Mean absolute per-case error in `overall_score` against the reviewer's own
verdicts, over all 40 cases in both datasets: **P0 = 0.0578, P8 = 0.0576**. The
mean signed bias of the status quo is **+0.003**. At the aggregate level, the
declared-only defect is invisible: its over-scoring is offset by unrelated
failure modes that under-score (chiefly the field-confirmation UNCERTAINs in
`_judge_total_employment`).

The harm is concentrated, not diffuse. The worst per-case over-scores:

```
c12_senior_years_x_data      0.840 vs 0.520 human    +0.320
c02_grad_bare_x_platform     0.260 vs 0.000          +0.260
c07_changer_x_platform       0.260 vs 0.000          +0.260
c14_junior_stuffed_x_platform_k8s  0.200 vs 0.000    +0.200
```

Two of those are profiles scored 0.20-0.26 on postings a reviewer scored **0.0**
— every point of the score came from claims nothing supports. Any argument that
"the score is fine on average" is true and beside the point.

---

## E. Evidence-supported policy options

Stated as options with their measured cost, not as a shortlist with a winner.
Each is deterministic, each is a closed list or a count of rows the index
already holds, and none requires a model.

**Option 1 — Change nothing.** Keep `satisfies()` as it is.
*Costs:* 14 of 14 false matches across both benchmarks persist; the worst aspect
score in the suite (`uncorroborated_claim`) stays where it has been since v2;
per-case scores stay inflated by up to 0.32 on thin profiles.
*Buys:* no risk of new regressions, and no dependence on a 34-row,
single-non-human-rater evidence base.

**Option 2 — An `EXPOSURE_WORDING` vocabulary (P10).** A new closed list, its
own file, a sibling to `_DEMANDS_EMPLOYMENT` rather than an edit to it:
`declared_only` satisfies a requirement only when the posting's own verified
words ask for exposure, familiarity, awareness or basic knowledge. Everything
else declared-only becomes UNCERTAIN.
*Measured:* agreement 0.8745 → 0.8935; false matches 14 → **0**; marginal cost
9 true matches turned into UNCERTAIN.
*Shape:* identical in kind to the mechanism the codebase already trusts — read
the bar from the posting's own words, never from the profile's weight.
*Risk:* uncertainty inflation. Nine requirements a reviewer called satisfied
would come back hedged, and UNCERTAIN dilutes coverage by design
(`scoring.py:11-17`).

**Option 3 — Exposure wording OR a second independent assertion (P11).**
Option 2, widened: a declared-only claim also settles a bare ask when the token
is declared in two different tables (skills *and* a project's technologies) or
carries its own `years_of_experience`.
*Measured:* agreement 0.8745 → **0.9087**; false matches 14 → 3; marginal cost
**2** true matches.
*Risk:* the two lost matches are uncontested `matching_real_v1` rows, so this
option knowingly overrules the one human rater the project has on two
judgments. Its three residual false matches are all contested rows, meaning its
apparent accuracy rests partly on expectations this document's own rater marked
as disputable.

**Option 4 — Second assertion only (P5/P8).** Drop the wording half.
*Measured:* P5 agreement 0.9011, false matches 1, marginal cost 6; P8 agreement
0.9011, false matches 3, marginal cost 4.
*Why it is listed below Option 3:* it fails the exposure cases (`c01/rq_gr_tf`,
`c04/rq_gr_tf`) where the posting explicitly asks for nothing more than the
claim, which is the one situation where believing a bare declaration is
unambiguously correct.

**Explicitly not supported by the evidence:** any employment-years threshold
(P4 — dominated at every N, and errors span 0.5y to 12.0y); any required/
preferred split (P2/P3/P12 — the axis does not predict the error, and the
control policy keyed to preferred-marking wording is the worst measured);
any profile-substance ratio (P7 — dominated by P5 at every threshold); and any
change to the `rules.py:957-993` stated-years branch (D1/D2 — six observations,
one-judgment margins).

---

## F. Limitations

- **The rater of `declared_only_v1` is not a human.** `matching_real_v1` was
  rated by an engineer on this system; the new dataset was rated by the AI
  assistant that also ran this analysis and wrote this document. That is a
  materially weaker form of independence than a second human, and it is the
  single largest limitation of everything in §C-§E. The numbers are a design
  input; they are not a release gate.
- **34 declared-only observations, 14 of them contested.** Every policy
  comparison in §D turns on differences of 2-11 judgments. A category with
  fewer than twenty observations that scores 0.9 means "look at this", not
  "90%".
- **The two benchmarks are not commensurable and must never be pooled in a
  headline.** `declared_only_v1` concentrates declared-only cells far above
  their natural rate; its 0.809 raw agreement is not comparable to
  `matching_real_v1`'s 0.911. §D pools them only for the policy counterfactual,
  where every non-declared-only row is held fixed and contributes identically to
  every policy.
- **The reviewer rules contradict each other on exactly this question.** Rule 1
  counts stated years and project technologies as corroboration; rule 6 says
  duration counts paid employment. `c12/rq_da_scala5` sits precisely on the
  collision, and the expectation recorded there follows rule 6 because the
  requirement is a duration requirement. A reviewer applying rule 1 would score
  Option 3 differently.
- **P11's measured advantage is partly an artifact of its own rater.** All three
  of its residual false matches are contested rows; if a human rater resolved
  those three the other way, P11's false-match count would be 0 and its
  advantage over Option 2 would grow — and if they resolved the two lost matches
  the other way, it would shrink. The evidence does not currently distinguish
  these.
- **Synthetic profiles, one discipline, one hiring culture.** Both datasets are
  UK/EU software engineering. Nothing here measures declared-only behaviour for
  other professions, where skills lists may be shorter, longer, or mean
  something different.
- **Structuring is assumed correct.** Requirements are hand-authored in both
  datasets. In production, `source_quote` reaches `standard_for()` through the
  job structurer, and Options 2 and 3 make the *exact wording* of that quote
  load-bearing in a way it is not today — an extraction that drops "any
  exposure" would silently change a verdict.
- **The engine weakly co-authored the fixtures.** Technology names in
  `declared_only_v1` were chosen from the curated vocabulary so that cases would
  test policy rather than coverage.

---

## G. Additional benchmark cases required

Before any of Options 2-4 could be defended as shipped work, these cells are
missing or too thin. Each is a `declared_only_v2` (or `matching_real_v2`)
authoring task, not a code task.

1. **A human re-rating of the 19 contested judgments in `declared_only_v1`** —
   and ideally of all 94. This is the highest-value item on the list by a wide
   margin, because §D's ranking between Options 2, 3 and 4 depends entirely on
   how those rows resolve.
2. **Exposure-worded requirements at volume.** The exposure half of P10/P11
   rests on **two** rows (`c01/rq_gr_tf`, `c04/rq_gr_tf`). A closed vocabulary
   cannot be curated from two examples. Needed: at least a dozen requirements
   using "familiarity with", "awareness of", "some experience with", "basic",
   "willingness to learn", against both corroborated and bare claims, plus
   adversarial ones ("familiar with the production challenges of Kubernetes")
   where the exposure word does not govern the technology.
3. **A declared-only claim against a `professional_use` ask in a corroborated
   profile.** Every `professional_use` row in both datasets resolved correctly,
   which means that half of `satisfies()` is currently untested against anything
   difficult.
4. **Two declaration sources with genuinely different meanings.** P5 treats
   "skills list + project technology" as corroboration. Needed: a case where a
   project technology is listed and the project plainly did *not* use it
   (abandoned, or listed aspirationally), to find out whether the second
   assertion is evidence or duplication. `c07/rq_pf_docker` is the only probe
   and it is contested.
5. **Project descriptions that describe a technology the project did not
   declare.** §A.0's suppression rule (`profile_index.py:318-322`) means a
   described-and-declared technology is indistinguishable from a
   declared-and-silent one. No case in either dataset separates them, so
   "declared plus described" is currently unmeasurable as a tier input.
6. **Stated durations at scale, and dishonest ones.** The stated-years branch
   has six observations. Needed: stated years far above what the employment
   history could support, stated years on a technology that post-dates the
   candidate's career, and stated years that agree with corroborated employment.
7. **Non-software profiles.** Whether a skills list means the same thing in
   another profession is untested and currently unknowable from this suite.

---

## H. Recommendation for Matching V6

**Yes — V6 should change declared-only handling, and no, it should not ship any
of §E's options on this evidence alone.** Those are not in tension; they are the
two halves of the finding.

**What the evidence establishes well enough to act on.** Declared-only evidence
is the sole source of invisible error in this system: 14 of 14 false matches
across 263 judgments in two independently authored benchmarks, with zero errors
in the opposite direction. That is no longer a suspicion recorded in a known-
limits list (`matching.md:664-666`); it is measured, reproducible, and
attributable to one line — `evidence.py:144`, the presence of
`TIER_DECLARED_ONLY` in the `any_evidence` branch of `satisfies()`. The error is
one-directional and unreadable from the result, which is the exact failure class
this codebase's own comparison order puts first.

**What the evidence refutes, and this matters as much.** The two rules most
likely to be proposed from intuition are both wrong:

- **Do not gate declared-only on years of experience.** Not at any threshold.
  The errors run from 0.5 to 12.0 years, the twelve-year principal engineer
  produces one and the one-year graduate with a project produces none, and every
  member of the P4 family is dominated. `matching_real_v1` appears to support a
  seniority rule only because six of its seven declared-only rows come from one
  profile.
- **Do not split the bar by required versus preferred.** The axis does not
  predict the error (8 preferred, 6 required), and the control policy keyed to
  preferred-marking wording scores *below* the status quo. A posting saying
  "nice to have" has told you how much the gap matters, not how much the claim
  is worth. `satisfies()` should stay axis-blind, which also keeps
  `matching.md:365-371`'s "the two axes mix in exactly one place" true.

**What V6 should actually do, in order.**

1. **Author the cases in §G first — items 1 and 2 before anything else.** A
   human rating of the contested rows, and a real corpus of exposure-worded
   requirements. Until those exist, the difference between Options 2, 3 and 4 is
   inside the noise of a 34-row sample rated by one non-human reviewer.
2. **Then implement Option 2 (`EXPOSURE_WORDING`), not Option 3**, if the new
   cases hold. It is the option whose shape the codebase already trusts — a
   closed, curated, reviewable list read from the posting's own verified words,
   a sibling to `_DEMANDS_EMPLOYMENT` in its own file — and the only one that
   reaches zero false matches on both benchmarks. Its cost is uncertainty
   inflation, which is the safe direction and a visible one. Option 3's extra
   agreement comes from reading the *profile's* shape rather than the posting's
   words, which is a different kind of rule, and its two measured losses overrule
   the project's only human rater.
3. **Leave the stated-years branch alone.** Six observations, one-judgment
   margins.
4. **Leave certifications and education alone.** They never touch this tier
   (§A.2), and any V6 scope that lists them under declared-only work is
   mis-scoped.
5. **Do not bump `ALGORITHM_VERSION` for analysis.** It stays `matching_v5`
   until something is built and measured against both datasets the way
   `matching_v1 → v2` was measured.

**The honest summary of sufficiency**, which the brief asked for directly: the
available data is sufficient to justify *removing* a policy — the blanket
`any_evidence` acceptance of declared-only evidence is measurably wrong and
wrong in one direction. It is **not** sufficient to justify *choosing* the
replacement. Thirty-four observations, fourteen of them contested, rated by a
single non-human reviewer, separated by margins of two to eleven judgments, is
enough to rule out the two intuitive candidates and enough to name the two
signals that carry information. It is not enough to fix a rule that will decide
what a real candidate is told about a real posting.
