# matching_real_v1 — real-data matching benchmark

Independent human judgment about realistic profiles and realistic postings,
used to measure how well a matcher agrees with a person. Distinct in kind from
`datasets/matching_v1/`, which is a rule-level regression suite whose cases
were written *from* the matching rules — both exist, and neither replaces the
other. See `../../README.md`.

```
profiles.json       6 synthesised candidates
jobs.json           8 synthesised postings, with the requirements each states
ground_truth.json   16 pairings, 143 human verdicts with rationales
```

> Nothing in this benchmark estimates anyone's chance of being interviewed,
> hired, shortlisted or called back. It measures one thing: whether a matcher's
> verdict about a stated requirement agrees with a person's, given the same two
> documents.

## How the cases were selected

Coverage of *difficulty*, not of volume. A hundred well-written profiles
against a hundred plainly-worded postings would score well and teach nothing,
so each profile was built around a shape that makes judgment hard, and each
posting around a way real postings resist being checked mechanically.

The six profiles:

| profile | the hard thing about it |
|---|---|
| `p_backend_mid` | nothing — the control. Clean skills, dated roles, corroborating descriptions. |
| `p_career_changer` | six years in another discipline, a long-running unpaid project, a degree in the "wrong" subject |
| `p_contractor_overlap` | engagements that overlap by three years; one live certification and one lapsed |
| `p_frontend_prose` | the real skills are only in role descriptions; one technology named as evaluated and abandoned |
| `p_data_senior` | seniority, domain and degree field all stated in prose, none in a structured field |
| `p_junior_stuffed` | a skills list longer than the CV, four entries nothing else in the profile touches |

The eight postings between them state requirements in the forms that are
genuinely hard to check: seniority as a sentence about leading people, domain
as an industry name, location as a hybrid policy, a degree field as "or a
related field" or "a quantitative field", and duration in four different
phrasings.

**Requirements are authored, not structured.** Each posting's `requirements`
are what a reviewer confirmed the description states, written by hand rather
than produced by running the job structurer. This is deliberate: a structuring
mistake would otherwise arrive here looking like a matching failure and be
diagnosed in the wrong domain. `source_quote` records the line each requirement
was read from, so a disputed one can be checked rather than argued about.

**Profiles are synthesised, not anonymised.** Every name, employer, institution
and date is invented; what comes from reality is the shape. Anonymising real
profiles leaves re-identifiable combinations of employer, dates and title, and
buys nothing here — the benchmark measures judgment, not provenance.

## How ground truth was established

Every verdict was reached by reading the profile fixture and the job
description as documents and deciding what a careful hiring reviewer would
conclude. The rationale was written at the same time as the verdict and
**before any matcher was run**. The reviewer rules below were fixed before the
first verdict was written.

### What each verdict means

**MATCHED** — the profile, read as a document, clearly satisfies the
requirement. The reviewer can point at the item that does so and would not
expect a colleague to disagree.

**MISSING** — the requirement is definitely not met. Either nothing in the
profile speaks to it, or the profile settles it in the negative: a duration
that can be computed and falls clearly short, a degree level plainly not
reached. A MISSING of the second kind cites what was weighed, because "we found
nothing" and "we found this and it is not enough" read very differently to the
person who wrote the profile.

**UNCERTAIN** — something relevant is present and does not settle the question:
evidence too weak to call a claim, a duration that cannot be computed, a field
whose relatedness is a judgment call, or a requirement the profile schema
cannot answer at all.

### The reviewer rules

1. **A listed skill with corroboration is MATCHED.** Corroboration is a role
   description, a project technology, or stated years.
2. **A listed skill with no corroboration anywhere** is MATCHED against a
   requirement asking only for exposure or familiarity, and UNCERTAIN against
   one asking for working or production use. The profile asserts it and shows
   nothing; the bar the posting set decides whether that is enough.
3. **A skill described specifically in a paid role is MATCHED** even if it is
   absent from the skills list. A reviewer reads the whole document.
4. **A skill named without having been used is UNCERTAIN** — "evaluated X",
   "worked alongside the X team". Present, but not experience.
5. **Nothing anywhere is MISSING.**
6. **Duration counts paid employment** whose role plausibly involved the
   subject. Overlapping roles count once. A computable duration that falls
   clearly short is MISSING, not UNCERTAIN: the profile answered, and the
   answer was no. UNCERTAIN is for durations that cannot be computed.
7. **Projects are not professional duration.** A side project against a
   "professional X years" requirement caps at UNCERTAIN.
8. **Education** compares level numerically and field by meaning, as a person
   would: mechanical engineering *is* engineering; statistics *is* quantitative;
   whether mechanical engineering is "related" to computer science is a genuine
   judgment call and therefore UNCERTAIN.
9. **Certifications** match on credential identity. A level suffix held against
   an unqualified requirement counts up. Expired is UNCERTAIN.
10. **Location** is MATCHED when recorded employment places the person in the
    stated area, and UNCERTAIN when it places them elsewhere — a profile does
    not state where someone will live, and people relocate.
11. **Work authorisation is always UNCERTAIN.** The profile holds no field that
    could answer it, however suggestive the employment history.
12. **Seniority** is MATCHED when a title or description shows senior or lead
    responsibility, MISSING when the profile is clearly individual-contributor
    with nothing, UNCERTAIN when ownership language makes it arguable.
13. **Domain** is MATCHED when an employer or description clearly places the
    work in that industry.

### Contested judgments

Nine of the 143 are flagged `contested`, each with the disagreement recorded in
`contested_note`. They are the ones where a second careful reader could land
elsewhere — whether a six-year public project counts as professional
experience, whether a statistics doctorate is "related" to computer science,
whether a timezone clause or a hybrid-attendance clause governs. The runner
reports agreement over the uncontested subset separately for this reason.

## Known limitations

- **One reviewer.** The single largest limitation. There is no inter-rater
  agreement figure because there was one rater, and that rater is an engineer
  on this system rather than a recruiter. `contested` flags are an attempt to
  record known softness, not a substitute for a second opinion. Adding a second
  independent reviewer is the highest-value improvement available to this
  dataset.
- **Synthetic data.** The profiles and postings are written to be realistic,
  and they are still written. Real postings are longer, more repetitive, worse
  organised and more contradictory than these.
- **Six profiles, eight postings, 143 judgments.** Enough to expose systematic
  failure modes; nowhere near enough for a confidence interval. A category with
  six observations that scores 0.5 means "look at this", not "50%".
- **Structuring is assumed correct.** Requirements are hand-authored, so the
  benchmark says nothing about how well the job structurer would have extracted
  them from the description.
- **Domain skew.** All eight postings are software engineering roles in
  Europe. Nothing here measures matching for other professions or other
  hiring cultures.
- **No adversarial cases.** Nobody has tried to construct a profile that games
  the matcher.

## How future algorithms are compared

The dataset and the ground truth do not move. A new matcher implements
`MatchingAlgorithm` in `evaluation/benchmark/adapter.py` — the single module in
the benchmark that is allowed to know how any matching works — and is run
against the same 143 judgments:

```
python -m evaluation.benchmark.runner --algorithm matching_v1
python -m evaluation.benchmark.runner --algorithm <the new one>
```

Compare in this order:

1. **false matches** — the errors a reader of the result cannot see. A new
   algorithm that improves agreement while increasing this is worse.
2. **per-verdict precision and recall** — which direction the errors run.
3. **agreement by aspect** — whether a specific failure mode was actually
   fixed, or whether the total moved for unrelated reasons.
4. **evidence correctness** — an explainable matcher that cites the wrong item
   has not improved just because its verdict is right.
5. **raw agreement**, last, and never on its own.

A result is only comparable when `dataset_version` and `algorithm_version`
match what is being claimed; both are printed on every report.

**Changing this file.** A verdict may be corrected only on the grounds that the
reviewer misread the profile or the posting, and the rationale must change with
it. A verdict must never be edited because an algorithm disagrees with it —
that disagreement is the measurement. A change to any expectation bumps the
directory to `matching_real_v2`, so results from before and after are never
compared as though they were the same benchmark.
