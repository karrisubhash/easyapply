# tailoring_v1 — is the tailored resume any good?

The tailoring domain already had tests proving a document is *grounded*. It had
none proving a document is *useful*. This dataset is the second half: it pairs
one real confirmed profile with four real postings and asserts, mechanically,
properties a genuinely tailored resume must have.

## The two sides

**Profile** — `resume_profile_v1`, read by the real resume pipeline
(`extract` → `DeterministicResumeStructurer` → `to_snapshot`). Not a fixture
written for tailoring: the same document `test_resume_golden_profile.py` asserts
fact by fact, so a change that corrupts the reading fails there first and this
dataset never sees it.

**Postings** — four from `matching_real_v1`, chosen because the candidate stands
in a different relation to each:

| Job | Relation | What it catches |
|---|---|---|
| `j_backend_senior` | strong match | does the best evidence lead? |
| `j_platform_k8s` | match on the *other* half of the profile | does selection move when the posting moves? |
| `j_sre_health` | match on the least-obvious evidence | is on-call / healthcare evidence findable? |
| `j_frontend_react` | near-total mismatch | does it stay honest and still render? |

`j_frontend_react` is the important one. A tailoring engine is easy to make look
good on a posting the candidate matches; the failure that costs a user an
interview is the one where the engine reaches for something that is not there.

## The discipline

`cases.json` was authored from the resume and the four postings **before the
generator was changed**, and is not edited to match an output. If the code
disagrees with it, the question is which of the two is wrong about the
documents — never "make the expectation match".

Every expectation is a property that can be checked by a machine against the
generated document. None of them is a human quality judgement, because a human
judgement nobody can re-run is not a regression test.

## What it does not do

It does not score prose. There is no reference resume to diff against and no
rubric, because the deterministic generator selects sentences the candidate
already wrote — the question this dataset answers is *which* sentences, in
*which order*, with *what dropped*.

Run it: `pytest tests/test_tailoring_evaluation.py`
