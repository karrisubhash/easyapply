# The exposure-wording collision

Analysis only. Nothing under `app/services/matching/` changed to produce this
document, no V6 policy is proposed as shipped work in it, and
`ALGORITHM_VERSION` stays `matching_v5`. Every claim below was checked by
reading the current code and by running it; no fix is implemented.

This was found while inspecting the tree for the M1 status report, not by the
declared-only analysis, and it is a gap in that analysis rather than a defect in
the shipped matcher. `docs/matching-v6-declared-only-analysis.md` §E proposes an
`EXPOSURE_WORDING` option — a closed list letting a declared-only claim settle a
requirement whose own words ask only for exposure. What the analysis did not
notice is that **the same words are already load-bearing on the other side of
the match, with the opposite effect.**

## The collision, exactly

`NON_DOING_MARKERS` (`vocabulary.py:83-95`) contains, among 43 entries:

```python
"learning", "learn", "studying", "familiar", "familiarity",
"exposure", "interested", "curious", "keen",
```

These read the **profile**. `is_non_doing()` fires on a clause containing one of
them, `demonstrates_v5()` returns False, and `profile_index` files the clause's
technologies under `mentions` rather than `employment`
(`profile_index.py:280-289`). A candidate who writes *"Familiar with Kubernetes
from the platform team's handover documentation"* has, by that sentence, made
their Kubernetes evidence weaker.

The proposed `EXPOSURE_WORDING` list would read the **posting**, and would treat
the same words as *lowering the bar*, so that weaker evidence suffices.

Both readings are individually correct. A candidate saying "familiar with X" is
hedging; a posting saying "familiarity with X" is asking for little. They are
not contradictory — they are opposite sides of the same match — but they are not
independent either, and the analysis treated the posting side as though nothing
else in the system had an opinion about those words.

## What the current code actually does with it

Take the pairing this creates, which no benchmark row covers today: a posting
asking *"Familiarity with Kubernetes"* against a profile whose only Kubernetes
is the sentence *"Familiar with Kubernetes from the platform team's handover
documentation"*, with Kubernetes absent from the skills list.

| stage | code | result |
|---|---|---|
| clause read | `vocabulary.is_non_doing` fires on `familiar` | not a doing-clause |
| filing | `profile_index._file_experience_evidence` | `kubernetes` → `mentions` |
| tier | `TokenEvidence.tier` (`evidence.py:219-229`) | `MENTIONED` |
| standard | `standard_for("Familiarity with Kubernetes")` — no `_DEMANDS_EMPLOYMENT` word | `any_evidence` |
| verdict | `satisfies("mentioned", "any_evidence")` → `MENTIONED` is in no branch | **UNCERTAIN** |

Verified by running it: the requirement comes back UNCERTAIN, and it comes back
UNCERTAIN under every candidate policy in the analysis too, because **all of
them only ever take a MATCHED away.** `satisfies()` excludes `TIER_MENTIONED`
from both standards, so no declared-only policy — the status quo included — can
reach this row.

## Why this matters for the policy question

Three consequences, in increasing order of how much they should change what V6
does:

**1. The exposure option is narrower than §E's numbers suggest.** P10 and P11
were measured over rows where the profile *declares* the technology. Where the
profile's own wording is the hedge, the row never reaches the declared-only rule
at all — it is decided one layer below, by `is_non_doing`. The measured 14 → 0
false-match improvement is real and is about a smaller slice of reality than
"requirements phrased as exposure asks".

**2. There is a plausible under-match here that nobody has measured.** A posting
asking for familiarity, met by a profile that states familiarity, answers
UNCERTAIN. Whether a human reader would call that MATCHED is exactly the sort of
question this project settles with a benchmark row and not with an argument —
and there was no such row until `declared_only_v2`'s `v2_prose_exposure` fixture,
which exists for this.

**3. If `EXPOSURE_WORDING` is ever built, it must be its own list.** The
temptation will be to reuse the nine words already in `NON_DOING_MARKERS`, since
they overlap heavily. That would couple a posting-side bar to a profile-side
caveat vocabulary, so that curating one silently re-tunes the other. The
codebase has already made this call once, in the other direction:
`negation.py`'s module docstring explains why explicit negation became a sibling
table rather than new entries in `NON_DOING_MARKERS`, and
`docs/matching-v5-architecture-analysis.md` §B.2 says the same thing about
evaluating it as its own boolean, unioned rather than merged. The same reasoning
applies here, for the same reason: two questions that happen to share vocabulary
are still two questions.

## What is not claimed

- **This is not a bug in matching_v5.** Every stage above behaves as documented.
  The collision is a gap in the V6 *analysis*, which did not consider the
  profile side when measuring a posting-side option.
- **No direction is recommended.** Whether the familiarity-meets-familiarity row
  should be MATCHED or UNCERTAIN is a judgment for the human reviewer, and
  `declared_only_v2` now puts it in front of one. Guessing it here and building
  to the guess is the failure mode this whole workstream exists to avoid.
- **No count is offered.** How often real postings phrase requirements as
  exposure asks is unknown; both benchmarks are synthetic, and their density of
  exposure wording was chosen by an author, not sampled.

## The rows that will answer it

In `evaluation/datasets/declared_only_v2/`, judged blind in
`declared_only_v1/review_r2/packet.md`:

- `v2_prose_exposure` against exposure-worded asks — the collision itself, in
  both the required and preferred forms.
- The same profile against a bare ask and a duration bar, so the profile-side
  hedge can be read against a posting that is not hedging back.
- `v2_exposure_corroborated` against the same asks — the control: exposure
  wording meeting evidence nobody disputes.
- Two adversarial posting lines, *"Familiar with the production challenges of
  Kubernetes"* and *"Exposure to Terraform in a production environment"*, where
  an exposure word and a production word sit in one sentence. A naive
  `EXPOSURE_WORDING` regex fires on both and lowers a bar the posting is plainly
  raising.

  Measured on the current code, both already resolve to `professional_use`:
  `_DEMANDS_EMPLOYMENT` fires on `production` and `standard_for` returns at the
  first match, so today the production word governs and the exposure word is
  inert. That is the safe outcome, and it holds only because no exposure list
  exists yet. Anything added later must be ordered so it cannot reverse this —
  which is a design decision to take against human verdicts, not by whichever
  check happens to run first.

### Measured, for the record

Running the current engine over `v2_prose_exposure` × `v2_j_tools`:

```
tier for kubernetes : mentioned        (declared nowhere; the clause hedges)
tier for terraform  : mentioned

rq_it_py        any_evidence      -> MATCHED
rq_it_k8s       any_evidence      -> UNCERTAIN   "Familiarity with Kubernetes"
rq_it_tf        any_evidence      -> UNCERTAIN   "Awareness of Terraform or a similar ..."
rq_it_k8sprod   professional_use  -> UNCERTAIN   "Familiar with the production challenges of Kubernetes"
rq_it_tfprod    professional_use  -> UNCERTAIN   "Exposure to Terraform in a production environment"
```
