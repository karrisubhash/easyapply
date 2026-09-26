# Second-rater rubric — read this before `packet.md`

## What you are doing

You will read 47 requirements, each one paired with a candidate profile and the
job posting it came from, and decide for each whether the profile satisfies it.

They all turn on the same hard question: **what is a claim worth when the
profile makes it and the rest of the profile does not come back to it?** Most
of them involve something the candidate named in a field whose purpose is to
name it — a skills list, or the technology list attached to a project. Some
involve the opposite arrangement: a technology described in prose that no
structured field declares, or one declared in a field that a description then
contradicts. The shape codes below cover all of these; use whichever fits.

It is a long packet. You do not have to finish in one sitting — answers
accumulate in one file and the order does not matter — but please do not go
back and revise an earlier row after seeing a later one, because two rows are
sometimes deliberately similar and your first reading of each is the
measurement.

**A set of labels for these rows already exists, and is deliberately withheld
from you.** So is the behaviour of the software this is being used to evaluate.
The point of the exercise is to find out what a second careful reader concludes
without either, so please do not go looking for them: do not open
`ground_truth.json`, `manifest.json`, `profiles.json`, `jobs.json`, or anything
under `docs/`, until your answers are submitted. If you have already seen any of
them, say so in the `prior_exposure_to_this_dataset` field rather than
withdrawing — a rating from someone who has seen the originals is still useful
as long as it is labelled.

There is no answer key and no score. A row where you end up disagreeing with
whoever rated it first is a **result**, not a mistake.

---

## The three verdicts

These definitions are fixed for this project and are not up for revision here.

**MATCHED** — the profile, read as a document, clearly satisfies the
requirement. You can point at the specific item that does so, and you would not
expect a colleague reading the same two documents to disagree.

**MISSING** — the requirement is definitely not met. Either the profile
contains nothing that speaks to it at all, or it contains enough to settle the
question in the negative — a duration you can compute that falls clearly short,
a degree level plainly not reached.

**UNCERTAIN** — the profile contains something relevant that does not settle
the question: evidence too weak to call the claim, a duration that cannot be
computed from what is stated, or a question the profile simply has no field
that could answer.

The distinction that matters most, and the one that is easiest to blur:
MISSING means *the document answered and the answer was no*. UNCERTAIN means
*the document did not answer*. "I could not tell" is UNCERTAIN, never MISSING.

---

## What else to record

For every row, alongside your verdict, record two classifications. They are
**descriptive** — they say what kind of situation the row is, not what verdict
it deserves. Nothing in this rubric maps a shape to a verdict, and you should
not infer one; two rows of the same shape may well deserve different verdicts,
and that is part of what this exercise is measuring.

### `profile_evidence_shape` — what the profile does with the thing being asked about

| code | meaning |
|---|---|
| `S0` | Nothing anywhere in the profile names it. |
| `S1` | **Declaration only.** It is named in the skills list, or on a project's technology list, and nothing else in the profile refers to it — no role description, no project description, nothing. |
| `S2` | **Declaration corroborated by project evidence.** It is named in a declaring field *and* a project's own description describes work involving it. |
| `S3` | **Declaration corroborated by employment evidence.** It is named in a declaring field *and* a paid role's own description describes work involving it. |
| `S4` | **Described in a project, declared nowhere.** A project's description says it was used, and it appears in no skills row and on no project's technology list. |
| `S5` | **Described in a paid role, declared nowhere.** A role description refers to it, and it appears in no skills row and on no project's technology list. |
| `S6` | **Declaration contradicted by a description.** It is named in a declaring field, and a description elsewhere in the profile says the work was not done, was only considered, or was abandoned. |

If both `S2` and `S3` apply, record `S3`. If you are torn between `S1` and `S2`
— for instance, a technology listed against a project whose description never
mentions it — pick the one you think is right and explain the tension in your
rationale. That specific boundary is one of the things being measured.

`S4` and `S5` are for the reverse arrangement, where prose is the only place
the technology appears. `S6` outranks `S1` and `S2`: if a description
contradicts the declaration, record `S6` even though a declaring field also
names it. Note that a role description can refer to something without claiming
the work was done — recording `S5` says where the mention is, not how strong
it is, and how much it is worth is your verdict's job, not the code's.

### `requirement_demand` — what the posting's own words ask for

| code | meaning |
|---|---|
| `D0` | The posting names the thing and asks nothing further about how it was acquired. |
| `D1` | **Professional-use wording.** The posting's own words ask for production, professional, commercial, hands-on, at-scale, proven or shipped use — the quoted line sets a bar above merely having the skill. |
| `D2` | **Duration or context.** The posting asks for a number of years of it, or asks for it in a particular setting. |
| `D3` | Both `D1` and `D2`. |
| `D4` | **Exposure wording.** The posting's own words ask only for exposure, familiarity, awareness, basic knowledge or a willingness to learn — the quoted line sets a bar *below* ordinary use. |

Judge this from the **quoted line** shown for each row, not from the extracted
name. "Kubernetes" and "Kubernetes in production, not just in a course" are
different questions, and the quote is what the posting actually said.

`D0` and `D4` are different: `D0` is a posting that names the thing and says
nothing about the level, `D4` is a posting that explicitly asks for less than
ordinary use. Some lines contain an exposure word *and* a professional-use word
at the same time. Record the one you judge to govern the sentence and say why
in your rationale — those lines are in the packet precisely because it is not
obvious, and a split between raters on them is a useful result rather than a
problem.

### The rest of the answer

- `confidence` — `high`, `medium`, or `low`. Low confidence is not a failure;
  it is information, and the rows where raters are unsure are exactly the rows
  worth knowing about.
- `rationale` — one or two sentences saying what you pointed at and why it was
  or was not enough. **Required for every row.** A verdict without a reason
  cannot be reviewed later, and cannot be overturned when it turns out to be
  the wrong one.
- `notes` — optional. Use it for anything that struck you: a requirement you
  thought was badly worded, a profile you found unrealistic, a row you would
  have answered differently on a different day.

---

## Ground rules that are supplied

These are settled for this project and you should apply them:

1. **Judge only from the two documents in the row's own section.** Not from
   what is typical of the industry, not from what a candidate of this kind
   usually knows, not from another pairing.
2. **Judge the profile as it stood on the date given** at the end of each
   candidate's dossier.
3. **Nothing anywhere in the profile about it → MISSING.**
4. **A duration you can compute from the employment dates, that falls clearly
   short of what the posting asks, is MISSING** — not UNCERTAIN. The document
   answered.
5. **Overlapping roles count once.** Two concurrent jobs from 2020 to 2022 are
   two years of experience, not four.
6. **Read the quoted line.** The bar a posting sets lives in its own words.

## Ground rules that are deliberately NOT supplied

There is no rule here telling you:

- how much weight a claim carries when nothing else in the profile supports it;
- whether a technology listed against a personal project counts as
  corroboration, or as the same claim made twice;
- whether a number of years the candidate has typed next to a skill counts as
  evidence of that many years, or what to do when the profile's own dates make
  that number impossible;
- whether a technology described only in prose, and declared in no structured
  field, counts for more or less than one declared and never described;
- whether a declaration survives a description that contradicts it;
- whether any of the above should change with how much professional experience
  the candidate has, or with whether the requirement is required or preferred.

**Those questions are the entire purpose of this review.** Supplying a
rule for them would mean measuring whether you can apply a rule, which is not
in doubt and is not useful. Use your judgment, and put your reasoning in the
`rationale` field so it can be read later.

---

## How to record your answers

1. Copy `answers_template.json` to `answers.json` in this same directory.
2. Fill in the `reviewer` block at the top: your name, your role, the date, how
   long it took, whether you read this rubric, and whether you had seen any of
   this dataset before.
3. Fill in one entry per `row_id`. Every field except `notes` is required.
4. Leave `ground_truth.json` alone. Your ratings live in `answers.json` and
   nowhere else — the first rater's labels stay exactly where they are, so that
   both readings remain separately auditable.

```json
{
  "row_id": "R01",
  "verdict": "matched | uncertain | missing",
  "profile_evidence_shape": "S0 | S1 | S2 | S3 | S4 | S5 | S6",
  "requirement_demand": "D0 | D1 | D2 | D3 | D4",
  "confidence": "high | medium | low",
  "rationale": "<one or two sentences: what you pointed at, and why it was or was not enough>",
  "notes": ""
}
```

When `answers.json` is in place, the analyst runs `python analyze.py` from this
directory. Nothing is computed until then, and no software will be changed on
the strength of these ratings alone.
