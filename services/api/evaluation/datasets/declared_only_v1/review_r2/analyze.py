"""Second-rating analysis for declared_only_v1. Run after `answers.json` exists.

Read-only with respect to everything that matters: it does not write to
`ground_truth.json`, does not touch `app/services/matching/`, and does not
change `matching_v5`. The candidate policies below are **counterfactual scoring
functions over already-computed facts** — they re-label rows in memory to ask
"what would this rule have produced", which is how §D of
`docs/matching-v6-declared-only-analysis.md` was measured. None of them is, or
becomes, an implementation: nothing here is importable by the engine and
nothing here changes a verdict the product would serve.

    python analyze.py              # full report
    python analyze.py --validate   # check answers.json is complete, then stop

The sufficiency criteria in `SUFFICIENCY` were written before any second rating
existed, so that "is this enough evidence" is a question the data answers rather
than one the result argues for after the fact. **This script never selects a
policy.** It prints which criteria are met and stops; choosing is a human
decision, taken with the residual risk in view and not on a score alone.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = HERE.parents[3]
sys.path.insert(0, str(API))

from evaluation.benchmark import groundtruth  # noqa: E402
from evaluation.benchmark.adapter import DeterministicEngineAdapter  # noqa: E402

DATASET = "declared_only_v1"
FIXTURES_ONLY_DATASET = "declared_only_v2"
VERDICTS = ("matched", "uncertain", "missing")
SHAPES = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
DEMANDS = ("D0", "D1", "D2", "D3", "D4")

#: Written before the second rating existed. Each is a question about whether
#: the measurement can carry a decision, not about which decision to take.
SUFFICIENCY = [
    ("C1", "Raters agree on the control rows",
     "Raw agreement >= 0.85 AND Cohen's kappa >= 0.60 on the 13 rows the first rater did "
     "NOT flag as contested. If two careful readers cannot agree on the rows nobody found "
     "hard, no number computed from the hard rows means anything."),
    ("C2", "The contested rows actually resolve",
     ">= 70% of the 14 contested rows receive the same verdict from both raters. A row where "
     "the two raters still disagree is not evidence for or against any policy; with two raters "
     "there is no majority to break the tie."),
    ("C3", "The best policy's margin exceeds the ambiguity",
     "On rows where both raters agree, the leading policy beats the status quo by MORE rows "
     "than the number left unresolved by C2. Otherwise the ranking sits inside the noise the "
     "unresolved rows represent."),
    ("C4", "No policy is bought with false matches",
     "The leading policy's false-match count does not exceed the status quo's. This is the "
     "project's own comparison order (matching_real_v1/README.md): false matches first."),
    ("C5", "There is enough of it",
     ">= 30 declared-only observations on which both raters agree. This dataset holds 27 in "
     "total, so C5 CANNOT be met by this review alone. It is stated anyway, because a "
     "criterion you can only fail by collecting more data is still the criterion."),
]


# --- candidate policies (counterfactual scoring only) -------------------------

def p0_status_quo(f):        return True
def p1_never(f):             return False
def p2_preferred_only(f):    return not f["is_required"]
def p5_two_sources(f):       return f["n_declaration_sources"] >= 2
def p6_stated_years(f):      return f["stated_years"] is not None
def p8_second_signal(f):     return p5_two_sources(f) or p6_stated_years(f)
def p10_exposure(f):         return f["exposure_wording"]
def p11_exposure_or_signal(f): return p10_exposure(f) or p8_second_signal(f)

POLICIES = [
    ("P0  status quo", p0_status_quo),
    ("P1  never settles", p1_never),
    ("P2  preferred only", p2_preferred_only),
    ("P5  >=2 declaration sources", p5_two_sources),
    ("P6  stated duration", p6_stated_years),
    ("P8  either second signal", p8_second_signal),
    ("P10 exposure wording", p10_exposure),
    ("P11 exposure or second signal", p11_exposure_or_signal),
]

import re  # noqa: E402

EXPOSURE_WORDING = re.compile(
    r"\b(exposure|familiar(?:ity)?|awareness|aware of|some\s+experience|basic|"
    r"introductory|willing(?:ness)? to learn|keen to learn)\b", re.IGNORECASE)


# --- facts --------------------------------------------------------------------

def _fixture_sources():
    """`declared_only_v2`'s profiles, jobs and authored pairings.

    That dataset has no ground_truth.json, so every row it contributes carries
    no first rating. Running the matcher over it measures the matcher; it does
    not author an expectation, and none is written anywhere by this script.
    """
    directory = groundtruth.DATASETS / FIXTURES_ONLY_DATASET
    if not directory.is_dir():
        return {}, {}, {}

    def _read(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    profiles = {raw["id"]: groundtruth._profile(raw) for raw in _read("profiles.json")["profiles"]}
    jobs = {raw["id"]: groundtruth._job(raw) for raw in _read("jobs.json")["jobs"]}
    pairings = {entry["id"]: entry for entry in _read("pairings.json")["pairings"]}
    return profiles, jobs, pairings


def facts_by_row():
    """Everything a policy needs, plus the engine's own verdict, per packet row."""
    from app.services.matching.engine import JobPosting, evaluate
    from app.services.matching.profile_index import ProfileIndex

    dataset = groundtruth.load(DATASET)
    v2_profiles, v2_jobs, v2_pairings = _fixture_sources()
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    by_case = defaultdict(list)
    for row in manifest["rows"]:
        by_case[row["case_id"]].append(row)

    # (case_id, profile fixture, job fixture, as_of, case-or-None) for both halves.
    units = [(case.id, dataset.profiles[case.profile_id], dataset.jobs[case.job_id], case.as_of, case)
             for case in dataset.cases]
    units += [(entry["id"], v2_profiles[entry["profile_id"]], v2_jobs[entry["job_id"]],
               date.fromisoformat(entry["as_of"]), None)
              for entry in v2_pairings.values()]

    out = {}
    for case_id, profile, job, as_of, case in units:
        if case_id not in by_case:
            continue
        adapter = DeterministicEngineAdapter()
        version = adapter._profile_version(profile)
        candidate = adapter._candidate(job)
        index = ProfileIndex(version)
        result = evaluate(candidate, version, as_of=as_of,
                          posting=JobPosting(location_raw=job.location, workplace_type=None))
        engine_by_rid = {}
        for judgment in result.judgments:
            rid = adapter._positions.get((judgment.category, judgment.requirement_index))
            if rid:
                engine_by_rid[rid] = judgment

        for row in by_case[case_id]:
            rid = row["requirement_id"]
            judgment = engine_by_rid[rid]
            requirement = job.requirement(rid)
            key = judgment.requirement_key
            evidence = index.evidence_for(key) if key else None
            skill = index.skill_for(key) if key else None
            quote = " ".join(filter(None, (requirement.label, requirement.source_quote)))
            truth = case.truth_for(rid) if case is not None else None
            out[row["row_id"]] = {
                "row_id": row["row_id"], "dataset": row["dataset"],
                "case_id": case_id, "requirement_id": rid,
                "profile_id": profile.id, "candidate": row["candidate"], "pairing": row["pairing"],
                "label": requirement.label, "quote": requirement.source_quote,
                "category": judgment.category, "is_required": judgment.is_required,
                "minimum_years": requirement.minimum_years,
                "standard": judgment.evidence_standard,
                "engine_verdict": judgment.status,
                "engine_tier": evidence.tier if evidence else None,
                "n_declaration_sources": len({e.source_type for e in evidence.declarations}) if evidence else 0,
                "has_project_mention": bool(evidence.mentions) if evidence else False,
                "stated_years": float(skill.years_of_experience) if skill is not None and skill.years_of_experience is not None else None,
                "exposure_wording": bool(EXPOSURE_WORDING.search(quote)),
                "rater1_verdict": truth.verdict if truth else None,
                "rater1_contested": bool(truth.contested) if truth else False,
                "rater1_rationale": truth.rationale if truth else None,
            }
    return out


def load_answers():
    path = HERE / "answers.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def validate(answers, facts) -> list[str]:
    problems = []
    if not answers:
        return ["answers.json does not exist yet"]
    reviewer = answers.get("reviewer", {})
    for field in ("name", "role", "rated_on", "read_rubric", "prior_exposure_to_this_dataset"):
        if not str(reviewer.get(field, "")).strip():
            problems.append(f"reviewer.{field} is blank")
    seen = set()
    for entry in answers.get("ratings", []):
        rid = entry.get("row_id")
        where = f"{rid or '(no row_id)'}"
        if rid not in facts:
            problems.append(f"{where}: not a row in this packet")
            continue
        if rid in seen:
            problems.append(f"{where}: rated twice")
        seen.add(rid)
        if entry.get("verdict") not in VERDICTS:
            problems.append(f"{where}: verdict must be one of {VERDICTS}")
        if entry.get("profile_evidence_shape") not in SHAPES:
            problems.append(f"{where}: profile_evidence_shape must be one of {SHAPES}")
        if entry.get("requirement_demand") not in DEMANDS:
            problems.append(f"{where}: requirement_demand must be one of {DEMANDS}")
        if entry.get("confidence") not in ("high", "medium", "low"):
            problems.append(f"{where}: confidence must be high, medium or low")
        if len(str(entry.get("rationale", "")).strip()) < 15:
            problems.append(f"{where}: rationale is missing or too short to review")
    for rid in facts:
        if rid not in seen:
            problems.append(f"{rid}: not rated")
    return problems


# --- statistics ---------------------------------------------------------------

def kappa(pairs) -> float:
    """Cohen's kappa over three classes. 0 when the raters are at chance."""
    n = len(pairs)
    if n == 0:
        return float("nan")
    observed = sum(1 for a, b in pairs if a == b) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum((left[v] / n) * (right[v] / n) for v in VERDICTS)
    if expected >= 1.0:
        return float("nan")
    return (observed - expected) / (1 - expected)


def confusion(pairs) -> str:
    lines = ["                 r2:matched  r2:missing  r2:uncertain"]
    for v in VERDICTS:
        row = [sum(1 for a, b in pairs if a == v and b == w) for w in ("matched", "missing", "uncertain")]
        lines.append(f"  r1:{v:<11}{row[0]:>8}{row[1]:>12}{row[2]:>13}")
    return "\n".join(lines)


def policy_verdict(fact, settles) -> str:
    """What the engine would have said about this row under a candidate policy.

    A policy can only ever take a MATCHED away. A declared-only MISSING — which
    only the stated-years branch produces, when the candidate's own number falls
    below the bar — is left alone by every policy here, because whether an
    uncorroborated number may rule a requirement OUT is a separate question that
    §D.1 of the analysis measures on its own six rows and does not settle.
    """
    if fact["engine_tier"] != "declared_only":
        return fact["engine_verdict"]
    if fact["engine_verdict"] == "matched" and not settles(fact):
        return "uncertain"
    return fact["engine_verdict"]


def report():
    facts = facts_by_row()
    answers = load_answers()
    problems = validate(answers, facts)

    dual_all = [f for f in facts.values() if f["rater1_verdict"] is not None]
    print("=" * 78)
    print(f"SECOND-RATING ANALYSIS — {DATASET} + {FIXTURES_ONLY_DATASET}")
    print("=" * 78)
    print(f"packet rows            {len(facts)}")
    print(f"  with a first rating ({DATASET})      {len(dual_all)}")
    print(f"    flagged contested by rater 1        {sum(1 for f in dual_all if f['rater1_contested'])}")
    print(f"    control rows (not contested)        {sum(1 for f in dual_all if not f['rater1_contested'])}")
    print(f"  no first rating ({FIXTURES_ONLY_DATASET})   "
          f"{len(facts) - len(dual_all)}  <- single-rater only; cannot enter any agreement statistic")

    if problems:
        print()
        print("NOT READY — the second rating is incomplete:")
        for problem in problems[:40]:
            print(f"  - {problem}")
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        print()
        print("Nothing is computed until answers.json is complete. The packet, the rubric and")
        print("the first rater's labels are all in place; what is missing is a human.")
        return 1

    r2 = {entry["row_id"]: entry for entry in answers["ratings"]}
    rows = [facts[rid] for rid in sorted(facts)]
    dual = [f for f in rows if f["rater1_verdict"] is not None]
    single = [f for f in rows if f["rater1_verdict"] is None]
    contested = [f for f in dual if f["rater1_contested"]]
    controls = [f for f in dual if not f["rater1_contested"]]

    def pairs(subset):
        return [(f["rater1_verdict"], r2[f["row_id"]]["verdict"]) for f in subset]

    print()
    print("-" * 78)
    print("1. INTER-RATER AGREEMENT")
    print("-" * 78)
    print(f"  Over the {len(dual)} rows that have a first rating. The {len(single)} fixtures-only rows have")
    print("  one rater and cannot enter an agreement statistic; they appear in sections 3 and 5.")
    print()
    for name, subset in (("rows with two raters", dual), ("control rows", controls),
                         ("contested rows", contested)):
        p = pairs(subset)
        raw = sum(1 for a, b in p if a == b) / len(p) if p else float("nan")
        print(f"  {name:<22} n={len(p):<4} raw={raw:.3f}   kappa={kappa(p):.3f}")
    print()
    print(confusion(pairs(dual)))

    print()
    print("-" * 78)
    print("2. DISAGREEMENTS")
    print("-" * 78)
    disagreements = [f for f in dual if f["rater1_verdict"] != r2[f["row_id"]]["verdict"]]
    if not disagreements:
        print("  none")
    for f in disagreements:
        entry = r2[f["row_id"]]
        flag = "contested" if f["rater1_contested"] else "CONTROL ROW"
        print(f"\n  {f['row_id']}  {f['label']!r} ({'required' if f['is_required'] else 'preferred'}, {flag})")
        print(f"    quoted as: “{f['quote']}”")
        print(f"    rater 1: {f['rater1_verdict']:<10} {f['rater1_rationale'][:150]}")
        print(f"    rater 2: {entry['verdict']:<10} [{entry['confidence']}] {entry['rationale'][:150]}")

    print()
    print("-" * 78)
    print("3. WHAT RATER 2 DID WITH EACH EVIDENCE SHAPE")
    print("-" * 78)
    print("  (the payload: what a human does with each situation a policy would encode)")
    cells = defaultdict(Counter)
    for f in rows:
        entry = r2[f["row_id"]]
        cells[(entry["profile_evidence_shape"], entry["requirement_demand"])][entry["verdict"]] += 1
    print(f"  {'shape':<8}{'demand':<9}{'matched':>9}{'uncertain':>11}{'missing':>9}")
    for (shape, demand), counter in sorted(cells.items()):
        print(f"  {shape:<8}{demand:<9}{counter['matched']:>9}{counter['uncertain']:>11}{counter['missing']:>9}")

    mismatched_shape = [f for f in rows if r2[f["row_id"]]["profile_evidence_shape"] == "S3"]
    if mismatched_shape:
        print()
        print("  ** TRIPWIRE: rater 2 classified these as employment-corroborated (S3), which the")
        print("     engine's own index does not agree with. Each one is either a misread or a real")
        print("     attribution bug in the matcher, and must be read before anything else here:")
        for f in mismatched_shape:
            print(f"       {f['row_id']}  {f['label']}  ({f['case_id']}/{f['requirement_id']})")

    s2_rows = [f for f in rows if r2[f["row_id"]]["profile_evidence_shape"] == "S2"]
    if s2_rows:
        print()
        print(f"  Note: rater 2 read {len(s2_rows)} row(s) as project-corroborated (S2). The engine")
        print("     cannot distinguish these from S1 — a project description's mention is suppressed")
        print("     when the same project also declared the technology (profile_index.py:318-322).")

    print()
    print("-" * 78)
    print("4. FALSE MATCHES UNDER THE CURRENT POLICY (matching_v5, unchanged)")
    print("-" * 78)
    both_agree = [f for f in dual if f["rater1_verdict"] == r2[f["row_id"]]["verdict"]]
    for name, labels in (("vs rater 1 (two-rater rows)", {f["row_id"]: f["rater1_verdict"] for f in dual}),
                         ("vs rater 2 (every row)", {rid: entry["verdict"] for rid, entry in r2.items()}),
                         ("vs rows both raters agree on", {f["row_id"]: f["rater1_verdict"] for f in both_agree})):
        subset = [f for f in rows if f["row_id"] in labels]
        fm = sum(1 for f in subset if f["engine_verdict"] == "matched" and labels[f["row_id"]] != "matched")
        agree = sum(1 for f in subset if f["engine_verdict"] == labels[f["row_id"]])
        print(f"  {name:<32} n={len(subset):<4} false matches={fm:<4} engine agreement={agree/len(subset):.3f}")

    print()
    print("-" * 78)
    print("5. CANDIDATE POLICIES, SCORED AGAINST THE ROWS BOTH RATERS AGREE ON")
    print("-" * 78)
    print("  Counterfactual only. Nothing below is implemented, and the leading line is not")
    print("  a recommendation — see section 6.")
    print()
    labels = {f["row_id"]: f["rater1_verdict"] for f in both_agree}
    print(f"  {'policy':<34}{'agree':>8}{'falseM':>8}{'lostM':>7}")
    results = {}
    for name, fn in POLICIES:
        verdicts = {f["row_id"]: policy_verdict(f, fn) for f in both_agree}
        agree = sum(1 for rid, v in verdicts.items() if v == labels[rid])
        fm = sum(1 for rid, v in verdicts.items() if v == "matched" and labels[rid] != "matched")
        lost = sum(1 for rid, v in verdicts.items() if labels[rid] == "matched" and v != "matched")
        results[name] = (agree, fm, lost)
        rate = agree / len(both_agree) if both_agree else float("nan")
        print(f"  {name:<34}{rate:>8.3f}{fm:>8}{lost:>7}")

    print()
    print(f"  The same policies against rater 2's labels ALONE, over all {len(rows)} rows")
    print("  (single-rater: weaker evidence than the table above, and the only view that")
    print(f"  includes the {len(single)} fixtures-only rows). Reported, not to be ranked on.")
    print()
    r2_labels = {rid: entry["verdict"] for rid, entry in r2.items()}
    print(f"  {'policy':<34}{'agree':>8}{'falseM':>8}{'lostM':>7}")
    for name, fn in POLICIES:
        verdicts = {f["row_id"]: policy_verdict(f, fn) for f in rows}
        agree = sum(1 for rid, v in verdicts.items() if v == r2_labels[rid])
        fm = sum(1 for rid, v in verdicts.items() if v == "matched" and r2_labels[rid] != "matched")
        lost = sum(1 for rid, v in verdicts.items() if r2_labels[rid] == "matched" and v != "matched")
        print(f"  {name:<34}{agree / len(rows):>8.3f}{fm:>8}{lost:>7}")

    print()
    print("-" * 78)
    print("6. IS THIS ENOUGH TO CHOOSE A POLICY?")
    print("-" * 78)
    print("  Criteria fixed before the second rating existed. This script does not choose.")
    print()
    control_pairs = pairs(controls)
    control_raw = sum(1 for a, b in control_pairs if a == b) / len(control_pairs) if control_pairs else 0.0
    control_kappa = kappa(control_pairs)
    contested_resolved = sum(1 for f in contested if f["rater1_verdict"] == r2[f["row_id"]]["verdict"])
    unresolved = len(contested) - contested_resolved
    baseline = results["P0  status quo"][0]
    leader = max(results.items(), key=lambda kv: (kv[1][0], -kv[1][1]))
    margin = leader[1][0] - baseline

    checks = {
        "C1": control_raw >= 0.85 and control_kappa >= 0.60,
        "C2": (contested_resolved / len(contested)) >= 0.70 if contested else False,
        "C3": margin > unresolved,
        "C4": leader[1][1] <= results["P0  status quo"][1],
        "C5": len(both_agree) >= 30,
    }
    for code, title, detail in SUFFICIENCY:
        mark = "MET    " if checks[code] else "NOT MET"
        print(f"  [{mark}] {code}  {title}")
        print(f"            {detail}")
    print()
    print(f"  measured: control raw={control_raw:.3f} kappa={control_kappa:.3f}; "
          f"contested resolved {contested_resolved}/{len(contested)} ({unresolved} open); "
          f"leading margin {margin} row(s) over status quo; both-agree n={len(both_agree)}")
    print()
    print(f"  NOT A CRITERION, reported because it is easily confused with C5: this review")
    print(f"  produced {len(rows)} human-rated observations, of which {len(both_agree)} are agreed by two")
    print(f"  raters. C5 counts the second number. The {len(single)} fixtures-only rows raise the first")
    print("  and cannot raise the second, because they have no first rating and a model may not")
    print("  author one. Closing C5 needs a second HUMAN rater on those rows, or an explicit,")
    print("  recorded decision to re-scope the criterion — which is not this script's to take.")
    print()
    if all(checks.values()):
        print("  All criteria met. A policy decision is DEFENSIBLE on this evidence — which is not")
        print("  the same as taken. Bring the numbers, the open rows and the residual risk to")
        print("  whoever owns the decision; do not ship on the strength of this output.")
    else:
        failed = ", ".join(code for code, ok in checks.items() if not ok)
        print(f"  {failed} not met. The evidence does NOT yet support selecting a policy.")
        print("  Sections 1-5 remain valid as description: they say what two readers concluded and")
        print("  what each rule would have done. They do not license a choice between the rules.")
    return 0


if __name__ == "__main__":
    if "--validate" in sys.argv:
        facts = facts_by_row()
        problems = validate(load_answers(), facts)
        if problems:
            print(f"answers.json: {len(problems)} problem(s)")
            for problem in problems[:40]:
                print(f"  - {problem}")
            raise SystemExit(1)
        print(f"answers.json: complete, {len(facts)} rows rated")
        raise SystemExit(0)
    raise SystemExit(report())
