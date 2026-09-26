# non_doing_v1 — NON_DOING_MARKERS benchmark

An independent check on `app.services.matching.vocabulary.NON_DOING_MARKERS`,
`is_non_doing()`, `evidence.demonstrates()`, and the tiering, attribution and
duration rules built on top of them. Distinct in kind from
`datasets/matching_v1/` and `datasets/matching_real_v1/`: this dataset is
about one mechanism specifically, not the matcher as a whole, and it separates
the five stages a real match passes through so a mismatch can be pinned to the
stage that produced it.

```
sentence_cases    98   direct is_non_doing(text) checks
pipeline_cases    38   small synthesised profiles carried through all 5 stages
```

## How this was authored

Every expected value was decided by reading the sentence as a document and
applying the definitions already written into the production code's own
docstrings:

- `is_non_doing`: "whether this clause says the technology was NOT used."
- `demonstrates`: "whether a clause shows the work was done rather than
  contemplated."

No case's expected value was produced by running `is_non_doing`,
`demonstrates`, or any function in `app.services.matching`. See the
independence statement in `cases.json`'s `review` block, and
`tests/test_nondoing_benchmark_independence.py` for the structural, textual
and behavioural proof that the loading and scoring code cannot reach the
matcher at all.

**A `defect_class` on a case is a prediction, not an outcome.** It was
authored at the same time as the expected value, before the runner existed,
and says what KIND of mismatch the case is expected to reveal:

| class | means |
|---|---|
| `none` | a control: the author expects the implementation to get this right |
| `tokenizer` | word-splitting/hyphenation/whitespace mechanics misread a boundary |
| `vocabulary` | the marker (or protected-name) list itself is over- or under-broad for this word sense, or a plain-English non-use statement uses no listed word at all |
| `clause_context` | the marker's word-sense is correct, but clause-level granularity extends its effect past the phrase it grammatically qualifies |
| `attribution` | two technologies share one clause and a marker relevant to one wrongly changes the evidence recorded for the other |
| `evidence_model` | is_non_doing and the clause split are both right, but the tier/duration model downstream cannot represent a distinction a human reviewer would draw |

A case predicted as a defect that turns out to agree is not a mistake in the
dataset — it means the implementation is more robust than the author expected
(see `pl-007`, `pl-018`, `pl-031`, which predict resilience and are controls,
not defects, for exactly this reason: multiple clauses about the same token,
one clean, rescue the tier even when another clause about it misfires).

## Coverage

Every one of the 43 words/phrases in `NON_DOING_MARKERS` is tagged on at least
one case (`schema.NON_DOING_MARKER_VOCABULARY` is a hand-copied, independent
list used only for this bookkeeping — see its docstring). Required categories,
each represented by multiple cases:

genuine non-doing, completed work containing a marker, ordinary noun
collisions, proper-name collisions, job titles, research verbs, lifecycle
verbs, learning phrases, explicit non-use (plain English, no listed word),
`*-away` constructions, technology attribution (two technologies sharing one
clause), duration consequences, evidence-tier consequences, and job-title-path
behavior tested separately from description-clause behavior.

**Minimal pairs.** Ten `pair_id` groups hold a near-identical sentence pair
with opposite expected outcomes — e.g. `mp-eval-01`: "Evaluated Kafka... but
the project was cancelled" (True) against "After evaluating three message
brokers, the team adopted Kafka... in production for two years" (False). A
matcher that cannot tell these apart fails the pair even if it happens to get
one half right by coincidence.

**The `markers` field is a coverage tag, not a substring claim.** For the
eight `explicit_non_use` cases and a handful of pure word-boundary controls
(e.g. `sd-083`), the tagged marker is the word this case's absence-of-firing
is informative about, not a word appearing verbatim in the text — the whole
point of `explicit_non_use` is that no listed word appears at all.

## The five pipeline stages

Each `pipeline_case` builds a small in-memory `ProfileVersion` (via the
adapter, never by hand-authoring engine objects here) and states an
independent expectation at each stage the task asked to be measured
separately:

1. **non-doing classification** — folded into stage 2/3 below: whether a
   clause naming a token should file it as demonstrating the work.
2. **technology attribution** — `tokens[].attributed_employment`, and, for
   duration requirements, `resolved_subject` (which technology a requirement's
   own wording resolves to).
3. **evidence tier** — `tokens[].tier`.
4. **experience duration** — `duration.years` when a real number should be
   computable from dated employment; `measurable: false` when it should not
   be, whether because there is genuinely no employment evidence or because
   the only evidence is a prose mention with no attached dates.
5. **requirement verdict** — `verdict`.

A case can (and several do) expect stage 2/3 to be correct while stage 5
still disagrees, or the reverse — see `pl-030` and `pl-036`, where the tier
is expected to correctly land on `mentioned` (stage 2/3 agree) while the
verdict is expected to be `missing` rather than the mechanically-produced
`uncertain` (stage 5 disagrees), because the prose states an explicit,
clearly-insufficient duration ("three weeks", "two weeks") that a human
reads as settling the question, even though nothing in the profile schema
gives the tier model a dated span to compute that number from.

### Why some MISSING calls differ from the sibling real-data benchmark's rule

`datasets/matching_real_v1/README.md` rule 4 treats "evaluated X" as UNCERTAIN
("present, but not experience"), and this dataset agrees for cases where the
mention states no number at all (`pl-008`, `pl-020`, `pl-029`, `pl-037`).
Where the mention itself states or clearly implies a specific, small duration
("three weeks", "two days", "no production work with it" as an implicit
zero), rule 6's "a computable duration that falls clearly short is MISSING"
is the closer fit: a human reviewer can read the number straight out of the
sentence even though the engine's tier-based duration model has no dated span
to attach it to. `pl-014`, `pl-030` and `pl-036` are authored on this
reasoning and tagged `evidence_model` for exactly that gap.

## Adding a case

Decide the expected outcome from the text alone, before checking what the
implementation does. Tag it with every marker it is about, the required
category it belongs to, a `defect_class` prediction, and — if it is a
deliberate contrast with another case — a shared `pair_id`. Run
`python -m evaluation.nondoing.runner` to see where it lands; do not edit the
expectation to match the output.

Bump the directory to `non_doing_v2` if an existing expectation changes.
