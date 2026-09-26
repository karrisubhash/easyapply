# Tailoring AI v3 — selection forensics (run3)

Analysis only. Nothing changed to produce this document: no provider call was
made, and `SYSTEM_PROMPT`, `_profile_block`, `_requirements_block`,
`PROMPT_VERSION_AI` (`tailoring_ai_v3`), `grounding`, `reconciliation`,
`relevance`, `evaluation/tailoring_runner.py`, `tailoring_v1/cases.json` and
`ai_release_criteria.json` are exactly as they were for the run. The
expectations in `cases.json` are treated as frozen ground truth throughout:
none is rated, rewritten or argued with below.

Every number here came from the existing code, run offline over
`services/api/var/tailoring-eval/run3.jsonl`: each captured document was
passed through the unmodified `reconciliation.settle` and
`tailoring_runner.score_sample`, exactly as the runner does. The per-document
rates reproduce the runner's reported selection of 0.751. Prompts were rebuilt
with `_build_user_prompt` from the same `tailoring_v1` inputs, without building
a provider. See "Reproduce" at the end.

Scope: the six delivered documents. The 14 failed calls (12
`ai_provider_unavailable`, 2 `ai_output_malformed`) left no document, and the
capture keeps only their error code (`tailoring_runner.py:377`).

---

## A. Exact selection failures observed

27 failed checks out of 106, across 6 documents. The gate averages the
per-document rates (`tailoring_runner.py:519`), which gives 0.751.

| Document | Capture line | Rate | Failed checks |
|---|---|---|---|
| `backend_senior#0` | 1 | 0.609 (14/23) | `leads:Django`, `leads:PostgreSQL`, `leads:Docker`, `metric:1.2M`, `targets:Django`, `targets:PostgreSQL`, `targets:Docker`, `targets:Kubernetes`, `targets:Redis` |
| `platform_k8s#0` | 2 | 0.789 (15/19) | `leads:Kubernetes`, `leads:Terraform`, `leads:Go`, `leads:AWS` |
| `sre_health#0` | 3 | 0.500 (5/10) | `targets:Kubernetes`, `targets:Go`, `targets:Certified Kubernetes Administrator`, `targets:Participation in an on-call rotation`, `targets:Experience in healthcare or another regulated domain` |
| `frontend_react#0` | 4 | 1.000 (8/8) | — |
| `backend_senior#1` | 5 | 0.696 (16/23) | `no-project:Observability Starter Kit`, `targets:Python`, `targets:Django`, `targets:PostgreSQL`, `targets:Docker`, `targets:Kubernetes`, `targets:Redis` |
| `backend_senior#2` | 9 | 0.913 (21/23) | `no-project:Observability Starter Kit`, `not-targets:3+ years building production services with Django` |

How each kind of check is scored, against the document **after**
reconciliation:

- `leads:X` means X is in the group named exactly `Relevant to this role`
  (`tailoring_runner.py:240`), using exact string membership.
- `no-project:X` means no carried project is named X (`:247`).
- `metric:X` means the substring X appears anywhere in the document's text
  (`:279`).
- `targets:X` and `not-targets:X` check whether X, as an exact string, is in
  `targeted_requirements` (`:282`). Nothing normalises it.

---

## B. Repeated patterns

| # | Pattern | Documents | Failed checks | Share |
|---|---|---|---|---|
| P1 | Requirement labels copied with the prompt's `- ` list marker | `sre_health#0`, `backend_senior#1` | 11 | 41% |
| P2 | Skills section not built from the profile's skill rows: built from prose, or missing entirely | `backend_senior#0`, `platform_k8s#0` | 12 (7 direct, 5 downstream) | 44% |
| P3 | Irrelevant project carried, alongside a general "include everything" default | `backend_senior#1`, `backend_senior#2` | 2 | 7% |
| P4 | Experience and duration requirement claimed from a skills listing | `backend_senior#2` | 1 | 4% |
| P5 | Quantified bullet dropped from a project that was carried | `backend_senior#0` | 1 | 4% |

Once P1 is removed, 16 failed checks remain, spread over four documents. 13 of
them sit in two documents, `backend_senior#0` and `platform_k8s#0`, and each of
those two failures is a single whole-section event. None of this is a
consistent bias across all six documents.

---

## C. Root cause per pattern, with evidence

### P1 — requirement labels copied with the list marker. Cause: F (presentation of the requirement list)

**What was captured.** Here is `sre_health#0`'s raw `targeted_requirements`,
unchanged by reconciliation:

```
['- Kubernetes', '- Go', '- Participation in an on-call rotation',
 '- Experience in healthcare or another regulated domain',
 '- Certified Kubernetes Administrator']
```

`backend_senior#1` has the same shape: seven labels, every one beginning with
`- `.

**Mechanism.**
- `_requirements_block` prints each label as `f"- {label}"`
  (`generation.py:1013`) inside `<job_requirements>` (`:1041`).
- The instruction at `generation.py:776` asks for requirements "quoted from the
  requirement list you are given". The model quoted the whole line.
- This is v2's copy artifact again, in a block v3 did not touch. v3 fixed the
  SKILLS block by labelling what `- ` means (the header at
  `generation.py:962`: "each '- ' line is one skill name"). The requirement
  block uses the same marker and has no such header.

**The contrast shows the cause directly.** The model saw both blocks in the
same prompt:
- Skills: 0 of 75 skill entries across the six documents carry a `-`.
- Targeted requirements: 12 of 31 entries carry `- `.
- The copying is all-or-nothing per document: 100% of labels in two documents,
  and 0% in the other four.

**What the targeting judgement was, once the string is set aside.** With the
prefix removed offline:
- Both documents' target lists equal what `relevance.answered` derives from the
  same settled document. The only difference is that `sre_health#0`'s
  derivation also includes Prometheus, which is not expected.
- The targeting was right; the string was wrong.
- `sre_health#0` goes from 0.500 to 1.000 and `backend_senior#1` from 0.696 to
  0.957.

**Why this is not an evaluation issue.**
- Grounding also rejects these strings. It raised 12 `REASON_UNTARGETED`
  warnings: "This requirement is not one this job posting actually lists"
  (`grounding.py:776`).
- `_normalise` (`grounding.py:158`) is deliberately narrow and does not strip
  punctuation.
- The review screen renders each label verbatim as a badge
  (`apps/web/src/components/tailoring/tailored-resume-view.tsx:210`), so a user
  would see "- Kubernetes".
- The scorer's exact match is agreeing with grounding and with what the
  product shows.

**Why the run3 repair analysis looked clean.** Warnings do not block delivery,
reconciliation does not act on them, and `evaluation.tailoring_repairs` reports
only repairs, which are errors. All 12 were invisible in that output.

### P2 — skills section not built from the profile's skill rows. Cause: C (model behaviour, sampled), with A contributing

**`backend_senior#0`, raw output:**
```
Relevant to this role: Python, FastAPI
Additional Skills:     Kafka, REST API, Prometheus, Terraform, HL7, FHIR, ECS
```

- Every one of these nine names is a word in the profile's experience or
  project text. Eight of them are in lines this document carries. Terraform is
  the exception: it comes only from the project this document dropped.
- Five of the six skills the posting names are in the profile's skill rows but
  appear in no bullet: Django, PostgreSQL, Docker, Redis and Kubernetes. None
  of the five is in the skills section. PostgreSQL survives only as a
  technology of the Ledger project, and Kubernetes only inside a certification
  name.
- FastAPI, which the posting does not name, is in the lead group.
- The run's 4 repairs are this sample: `REST API` (the skill row is
  `REST APIs`), HL7, FHIR and ECS are all prose words.
- The five target misses are **downstream** of this. The model claimed only
  what its document showed (Python, and the Bachelor's degree). It even
  under-claimed: `relevance.answered` over the same document finds PostgreSQL
  (a project technology) and Kubernetes (in a certification name).

**`platform_k8s#0`:** the raw document has `skills: []`, so there is no skills
section at all. That alone accounts for all four `leads:` misses. Every other
selection check in this document passed:
- all six targets
- `project-first` (Observability Starter Kit)
- both certifications
- both exclusions

**Where the prompt contributes:**
- `generation.py:705` says "Only use skills and technologies that appear in the
  profile data". HL7 does appear in the profile data, in prose.
- Grounding accepts only skill rows plus project technologies
  (`grounding.py:248`, `:263`, `:272`, `:759`). The prompt's rule is looser
  than the validator's, and never mentions that project technologies are
  allowed.
- `generation.py:770` says "Put the ones this posting names" in the lead
  group. It does not say *every* skill row the posting names.
- Nothing says the skills section is required. `cases.json` invariants list
  `skills` in `sections_required`, but the AI runner does not score that
  invariant (`tailoring_runner.py:229`).

**Why C rather than A.** With the same prompt and the same model, 4 of the 6
documents built the lead group exactly: the posting-named skill rows, in the
requirement block's order.

| Document | Lead group |
|---|---|
| `sre_health#0` | Kubernetes, Go |
| `frontend_react#0` | TypeScript |
| `backend_senior#1`, `backend_senior#2` | Python, Django, PostgreSQL, Docker, Redis, Kubernetes |

The instruction is followed more often than not. The two lapses are
whole-section behaviours in single samples.

### P3 — irrelevant project carried. Cause: C, with D and A contributing

- **Where it appeared.** Observability Starter Kit was carried in
  `backend_senior#1` and `#2`, and excluded in `#0`.
- **What the prompt asks.** `generation.py:763` says "only ones that speak to
  this posting. A project that answers nothing belongs nowhere on this
  document". It gives no test for what "speaks to" means.
- **The overlap with the posting is zero.** The project's name, technologies
  (Terraform, Grafana, Prometheus) and description ("metrics and tracing
  stack") share no term with the backend requirement list. The deterministic
  tailor scores the project 0 and drops it.
- **D.** The project is infrastructure-adjacent:
  - the candidate's own summary says "observability tooling"
  - the posting says "platform team" and names Docker and Kubernetes

  A model judging adjacency rather than overlap can read that as relevant.
- **The repeated signal is inclusion, not this one project.** The two
  documents that carried the project are the only two that listed every one of
  the profile's 20 skills, despite "Keep the rest short — a list of twenty says
  less than a list of eight" (`generation.py:772`).
  - `backend_senior#1`: 26 entries, with 6 names printed twice (in the lead
    group and again under their category).
  - `backend_senior#2`: all 20.

### P4 — duration/experience requirement claimed from a skills listing. Cause: A, with F contributing

- **What was claimed.** `backend_senior#2` targeted "3+ years building
  production services with Django", which is scored. It also targeted "5+
  years of professional software engineering experience", which is not scored.
  `#0` and `#1` claimed neither.
- **A.** The only exclusion the prompt states is "A requirement about a visa, a
  location or a commute is never answered by a resume" (`generation.py:779`).
  Nothing covers years, or experience of doing something.
- **F.** The requirement block (`generation.py:1013`) is a flat list with no
  kind and no required/preferred marker. `- Django` and
  `- 3+ years building production services with Django` look like the same kind
  of line.
- **The deterministic side encodes this distinction and the prompt does not.**
  An experience requirement is answered by prose only, never by a skills line
  (`relevance.py:89`).
- **An explicit rule lands with this model.** The eligibility exclusion the
  prompt does state held on all 8 eligibility and location `not-targets` checks
  across the six documents. That is evidence that an explicit rule of this kind
  works.

### P5 — quantified bullet dropped. Cause: C, with A contributing

- **What happened.** `backend_senior#0` carried Ledger Reconciliation Engine
  with one bullet, "Exposes a REST API and a Prometheus metrics endpoint". It
  dropped "Reconciles 1.2M daily transactions against three upstream ledgers…".
- **The choice fits P2.** Neither bullet names a posting term, and the model
  kept the one that names technologies. That is the same keyword lens as P2 in
  the same sample.
- **A.** The only guidance on how many lines to write is "WRITE FEWER LINES
  RATHER THAN WEAKER ONES" (`generation.py:711`). Nothing says a measured
  outcome is worth keeping.
- `#1` and `#2` kept both bullets.

### Observed but not scored

- **`platform_k8s#0` claims `Go` with no Go anywhere in the document.** The
  skills section is missing and no bullet names Go. The check passes because
  scoring checks only the label, and grounding only checks the label against
  the posting. The claim came from the profile, not from the page.
- **Every document has `headline: null`.**
- **The lead-group order follows the requirement block, not the posting
  text.** Redis comes before Kubernetes because `read_posting` reads
  technologies before preferred skills (`relevance.py:450`), while the posting
  lists Kubernetes first. The AI runner does not score
  `lead_skills_order_follows_posting`.
- **The profile has 5 experience clauses and the caps are 4 and 2.** In 5 of
  the 6 documents the model carried every clause. This run's experience-bullet
  checks therefore say little about bullet *selection* either way.

### The questions asked, answered directly

- **Why are target requirements omitted?** Mostly they are not omitted.
  - 11 of the 16 target misses are present but carry the `- ` prefix (P1).
  - The other 5 are in `backend_senior#0`, where the skills were never put on
    the page (P2) and the model declined to claim what the document did not
    show.
  - No miss shows a model that chose the wrong requirements to aim at.
- **Why does `platform_k8s` fail to prioritise Kubernetes/Go/Terraform/AWS?**
  It did not fail on prioritisation.
  - In its only delivered document, all six targets, the project order and both
    certifications were right.
  - The four misses are one event: no skills section (P2).
  - The other two `platform_k8s` attempts were `ai_output_malformed`, so n=1.
  - The adapter raises that code for empty content (usually the token ceiling,
    `openrouter.py:281`) and for `json_invalid` (`:324`), and the capture
    cannot say which it was.
- **Why does `sre_health` miss its targets?** Entirely P1.
  - The content was exactly the five expected labels, including on-call and
    healthcare.
  - Its lead group, both `bullet_selected` checks and the 250k metric all held.
  - n=1.
- **Why is Observability Starter Kit selected?** See P3: 2 of 3 backend
  samples. It comes with an inclusion default in the same two samples, the
  prompt offers no test for relevance, and the project is adjacent to the
  posting.
- **Why is the 3+ years Django requirement mis-targeted?** P4. One of three
  backend samples claimed it. The prompt never says that duration and
  experience requirements are not answered by a skills entry, and the
  requirement list gives no kind that would show the difference.

### Does the prompt give enough explicit instruction?

| Capability | What the prompt says | Verdict | Evidence |
|---|---|---|---|
| Distinguish job requirements from profile skills | Separate blocks. `:705` says skills must "appear in the profile data" | **Partial**: the rule is looser than grounding's, and the requirement lines have no kind | P2 (`backend_senior#0` prose skills), `platform_k8s#0` claiming Go |
| Target relevant requirements | `:776`: quote from the list, only with evidence | **Content adequate, format unstated** | Target content right in 4/6 documents once the prefix is removed; P1 |
| Prioritise high-value technologies | `:770`: lead group, "in the order the posting asks for them" | **Adequate where followed**; no required/preferred marker in the block | 4/6 lead groups exact; not the failing behaviour |
| Avoid eligibility/location targeting | `:779`: visa, location, commute | **Sufficient** | 8/8 checks held |
| Avoid duration targeting | nothing | **Missing** | P4 |
| Select projects only when relevant | `:763`: "only ones that speak to this posting" | **Stated, not operational** | P3, 2/3 backend documents |

---

## D. Likely prompt-addressable

| Pattern | Confidence | Why |
|---|---|---|
| P1 | **High** | A mechanical copy of a printed character. The same model stopped doing exactly this for skills once the marker was explained (0/75). |
| P4 | Medium | The one explicitly stated exclusion held 8/8, and this is the same kind of rule. There is only one occurrence of the failure. |
| P3 | Low–medium | An operational test ("a project must name something in the requirement list") could help. The inclusion default in the same samples suggests the cause is behavioural. |
| P2 | Low | The `:705` wording could be aligned with grounding. But the instruction was already followed in 4/6 documents, and one failure is a missing section, which no wording clearly prevents. |
| P5 | Low | One occurrence, riding on P2. |

## E. Apparently model capability or sampling limits

- **P2's two lapses:** a whole section omitted in one document, and built from
  the wrong source in another. The same model followed the same instruction in
  the other four documents.
- **The P3 and P5 behaviour in those samples:** everything included, or the
  tech-named line kept.
- **Output reliability:** `ai_output_malformed` on 2 of 3 `platform_k8s`
  attempts. Their cause can't be recovered.

With six documents from one free model, the evidence **cannot separate**
"better wording would fix it" from "this model does it anyway" for P2, P3 or
P5. What it does rule out is a systematic failure of targeting logic.

## F. The smallest prompt change, if one is justified

**Justified: P1 only.**
- **The edit.** Give `<job_requirements>` the same one-line explanation the
  SKILLS block already has, for example "each '- ' line is one requirement;
  the requirement is the text after '- ', copied exactly". It goes in
  `_requirements_block` or at `generation.py:1041`.
- **Version.** Bump `PROMPT_VERSION_AI` to `tailoring_ai_v4`, and update
  `test_the_prompt_version_records_the_change`
  (`tests/test_tailoring_profile_block.py:239`), which pins v3.
- **Why this form.** It copies the v3 mechanism that measurably worked.

**What it can buy on these six documents is bounded.** Stripping the prefix
offline raises selection from **0.751 to 0.878**, which is still below the 0.90
minimum. It is justified as a defect fix: users would see "- Kubernetes"
badges, and grounding warns on every such label. It is not a route to passing
selection by itself, and delivery (0.30) is a provider-availability problem it
cannot touch.

**Possible second sentence (P4), with weaker evidence.**
- **The edit.** Add to the exclusion at `:779`: "A requirement stating a
  number of years, or experience of doing something, is answered only by an
  experience or project line that shows it — never by a skills entry."
- **The trade-off.** The evidence is 1 of 3 samples. Adding it makes v4 a
  two-variable change, which is harder to read from a small n.

**Not justified by this data:** prompt edits for P2, P3 or P5.

## G. What should not be changed

- **The v3 skill and link representation.** It worked: 0 category-suffix
  repairs, and 0/75 marker copies in skills.
- **The frozen expectations.** That includes `targeted_exclude` "3+ years … Django"
  and `projects_exclude` Observability Starter Kit, both with stated reasons in
  `cases.json`.
- **The scorer's exact label match.** It agrees with grounding and with what
  the UI shows.
- **Reconciliation, for now.** Stripping `- ` there would hide the prompt defect
  and prevent v4 from being measured. Whether to add it later as a second line
  of defence is a separate decision.
- **Replacing model targets with `relevance.answered`.** Doing that in response
  to this run would be a design change, not a prompt fix. It also has its own
  over-claim: on `platform_k8s#0` it would claim "6+ years of professional
  infrastructure experience" from the word "infrastructure" in "Reduced
  infrastructure spend".
- **The eligibility instruction** (8/8), the `MAX_*` caps, the release criteria
  and the model choice. On n=6 none of them is implicated.

## H. Tests and offline checks before another provider evaluation

1. **`test_every_requirement_shown_is_one_the_posting_lists_as_written`,**
   modelled on `test_every_skill_shown_is_one_grounding_accepts_as_written`
   (`tests/test_tailoring_profile_block.py:124`).
   - **What it does.** For each `tailoring_v1` case, parse `<job_requirements>`
     from `_build_user_prompt`, remove only the marker the prompt documents,
     and assert each line equals a `requirement_labels()` entry.
   - **Also assert** that the instructions describe the marker, as
     `test_the_instructions_describe_the_new_representation` does for skills.
2. **Re-score the run3 capture offline after the edit.** It must still give
   0.751 with matching `fingerprints`, which proves grounding and scoring were
   untouched.
3. **Make `evaluation.tailoring_repairs` report grounding warnings**, not only
   repairs. All 12 P1 labels were invisible in its run3 output. This changes
   evaluation tooling, not scoring.
4. **Add an offline diagnostic comparing the model's targets with
   `relevance.answered` on the same settled document.** It lists over-claims
   (`platform_k8s#0` Go, `backend_senior#2` years) and under-claims
   (`backend_senior#0`). It reports only and is not a gate.
5. **Record `finish_reason` and token usage for `ai_output_malformed` in the
   capture** (`tailoring_runner.py:377` records only the code). With that, the
   next run can say whether the `platform_k8s` failures hit the token ceiling:
   `TAILORING_AI_MAX_TOKENS=24000` in the local `.env`, against a code default
   of 12000. Counts only, never the prompt or the reply.
6. **Run the harness self-check.** `python -m evaluation.tailoring_runner`
   (deterministic) must still score 1.0, and
   `pytest tests/test_tailoring_evaluation.py tests/test_tailoring_profile_block.py`
   must pass.
7. **Run the one-call smoke (`evaluation.tailoring_smoke`) before the runner**,
   and use a new `--capture` path.

---

## Limitations

- **Small sample.** Six documents from one model
  (`nvidia/nemotron-3-super-120b-a12b:free`) against one profile. Three of the
  four cases have n=1, so each per-case conclusion rests on a single sample.
- **Counterfactuals are edits of captured documents, not model outputs.** The
  0.878 figure bounds what a perfect P1 fix would score on *these* six
  documents. It does not predict what v4 will produce.
- **Failed calls are unrecoverable.** Whether the two malformed `platform_k8s`
  outputs share `platform_k8s#0`'s missing skills section is unknown.
- **Some expectations are not scored.** `lead_skills_order_follows_posting`,
  `suppresses_irrelevant_skills`, `differs_from_case` and `sections_required`
  appear in `cases.json` but not in the AI runner's selection rate. The
  over-inclusion in P3 and the empty skills section in P2 register only through
  the checks that happen to depend on them.

## Reproduce

From `services/api`, offline, with no provider constructed:

```python
# PYTHONPATH=. .venv/Scripts/python.exe
import json
from app.schemas.tailored_resume import TailoredResumeDocument
from app.services.tailoring import reconciliation
from app.services.tailoring.generation import requirement_labels
from evaluation.datasets.tailoring_v1.inputs import cases, confirmed_version, structured_job
from evaluation.tailoring_runner import score_sample, profile_vocabulary

profile = confirmed_version(); vocab = profile_vocabulary(profile)
ds = cases(); by = {c["id"]: c for c in ds["cases"]}
for raw in open("var/tailoring-eval/run3.jsonl", encoding="utf-8"):
    r = json.loads(raw)
    if "error" in r:
        continue
    case = by[r["case_id"]]; _, req = structured_job(case["job"])
    doc = TailoredResumeDocument.model_validate(r["document"])
    st = reconciliation.settle(doc, profile, repair_allowed=True,
                               job_requirement_labels=frozenset(requirement_labels(req)))
    s = score_sample(case, ds["invariants"], st, vocab)
    print(r["case_id"], r["sample"], round(s.selection.rate, 3), s.selection.failed,
          st.document.targeted_requirements)
```

To get the P1 counterfactual, strip a leading `"- "` from each
`doc.targeted_requirements` entry before `settle`.
