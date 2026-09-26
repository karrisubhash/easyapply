"""Proving the benchmark's expected answers cannot come from the matcher.

This is the requirement the whole benchmark rests on, and it is the one that
cannot be established by reading the code once and trusting it afterwards.
Three independent arguments, because any one of them alone could be satisfied
by an accident:

  1. **Structural.** The modules that define, load and score ground truth do
     not import the matching package, transitively, at all.
  2. **Textual.** The dataset files contain no reference to engine internals —
     no rule names, no module paths, no algorithm version.
  3. **Behavioural.** Changing the matching rules changes the RESULTS and
     leaves the ground truth byte-identical. This is the strongest of the
     three: it would fail even if someone found a way to reach the rules that
     the first two tests missed.

If this file goes red, the benchmark has stopped being a measurement and has
become a mirror. Nothing else in the suite can tell you that.
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from evaluation.benchmark import groundtruth
from evaluation.benchmark.groundtruth import DATASETS, load

BENCHMARK = Path(groundtruth.__file__).parent

#: The modules that must never be able to reach a matcher. `adapter.py` and
#: `runner.py` are deliberately absent: the adapter IS the seam, and the runner
#: is what joins the two sides together.
MATCHER_FREE = ("schema.py", "groundtruth.py", "metrics.py", "failures.py")

FORBIDDEN_ROOTS = ("app.services.matching", "app.models.match", "app.schemas.job_candidate")


def imports_of(path: Path) -> set[str]:
    """Every module name a file imports, including inside functions.

    `from evaluation.benchmark import adapter` is recorded as
    `evaluation.benchmark.adapter`, not as the package: without that the walk
    below would stop at the package and a leak imported that way would go
    unseen — which is exactly how this test first passed for the wrong reason.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            for alias in node.names:
                found.add(f"{node.module}.{alias.name}")
    return found


def transitive_imports(start: str, seen: set[str] | None = None) -> set[str]:
    """Every module reachable from a benchmark module, following only the
    edges that stay inside the benchmark package."""
    seen = seen if seen is not None else set()
    path = BENCHMARK / start
    for name in imports_of(path):
        if name in seen:
            continue
        seen.add(name)
        if name.startswith("evaluation.benchmark."):
            leaf = name.rsplit(".", 1)[-1] + ".py"
            if (BENCHMARK / leaf).exists():
                transitive_imports(leaf, seen)
    return seen


# --- 1. structural ------------------------------------------------------------


@pytest.mark.parametrize("module", MATCHER_FREE)
def test_ground_truth_modules_cannot_reach_a_matcher(module):
    reachable = transitive_imports(module)
    offending = [
        name for name in reachable if any(name.startswith(root) for root in FORBIDDEN_ROOTS)
    ]
    assert offending == [], (
        f"{module} can reach {offending}. The expected answers must not be derivable "
        f"from the thing being measured."
    )


@pytest.mark.parametrize("module", MATCHER_FREE)
def test_ground_truth_modules_do_not_import_the_application_at_all(module):
    """Stricter than the rule above and deliberately so. These four modules
    describe matching as a problem; nothing in `app` is needed to do that, and
    the day one of them needs a model is the day the boundary has moved."""
    reachable = transitive_imports(module)
    assert [name for name in reachable if name.startswith("app")] == []


def test_the_adapter_is_the_only_seam():
    """The boundary is meaningful only if exactly one module crosses it."""
    crossing = []
    for path in BENCHMARK.glob("*.py"):
        reachable = transitive_imports(path.name)
        if any(name.startswith(root) for name in reachable for root in FORBIDDEN_ROOTS):
            crossing.append(path.name)
    assert sorted(crossing) == ["adapter.py", "runner.py"], (
        "runner.py crosses only by importing the adapter; anything else here is a leak"
    )


def test_the_adapter_defers_its_matcher_imports():
    """Importing the benchmark package must not import the engine, so the
    structural tests above are checking a real process state rather than a
    convention."""
    source = (BENCHMARK / "adapter.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_level = {
        alias.name if isinstance(node, ast.Import) else node.module
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
    offending = [
        name
        for name in module_level
        if name and any(name.startswith(root) for root in FORBIDDEN_ROOTS)
    ]
    assert offending == [], f"adapter.py imports {offending} at module scope"


# --- 2. textual ---------------------------------------------------------------


def dataset_files():
    return sorted((DATASETS / "matching_real_v1").glob("*.json"))


def test_the_dataset_names_no_engine_internals():
    """A rationale that cited a rule would be a verdict reached by reading the
    code rather than the profile."""
    # The review block legitimately names the algorithm — its whole job is to
    # say that no verdict came from it. The CASES are what must be clean.
    forbidden = (
        "matching_v1",
        "app.services",
        "ProfileIndex",
        "judge_token_requirement",
        "judge_experience",
        "canonical(",
        "ALIASES",
        "structured evidence",
        "prose evidence",
    )
    for path in dataset_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(
            {key: value for key, value in payload.items() if key != "review"}
        )
        for term in forbidden:
            assert term not in text, f"{path.name} mentions {term!r} outside its review block"


def test_every_rationale_argues_from_the_documents():
    """Weak but worth holding: a rationale should talk about the profile and
    the posting. One that talks about verdicts is describing an output."""
    dataset = load()
    for case in dataset.cases:
        for truth in case.ground_truth:
            lowered = truth.rationale.lower()
            assert "the matcher" not in lowered
            assert "the engine" not in lowered
            assert "algorithm" not in lowered


def test_the_dataset_declares_its_independence():
    dataset = load()
    statement = dataset.review.independence_statement.lower()
    assert "no verdict" in statement
    assert "must not be edited to agree" in statement


# --- 3. behavioural -----------------------------------------------------------


def ground_truth_digest() -> str:
    raw = (DATASETS / "matching_real_v1" / "ground_truth.json").read_bytes()
    return hashlib.sha256(raw).hexdigest()


def loaded_verdicts() -> dict[str, str]:
    dataset = load()
    return {
        f"{case.id}/{truth.requirement_id}": truth.verdict
        for case in dataset.cases
        for truth in case.ground_truth
    }


def test_changing_the_matching_rules_moves_the_score_and_not_the_truth(monkeypatch):
    """The proof that matters.

    An alias is added to the engine — a real rule change, of the kind a future
    version would make. If the benchmark's expected answers were derived from
    the rules in any way, they would move with it. They must not: only the
    measurement moves.
    """
    from evaluation.benchmark import runner

    before_digest = ground_truth_digest()
    before_truth = loaded_verdicts()
    before = runner.run()

    from app.services.matching import normalization

    # "Java" and "JavaScript" are different languages, and the benchmark has a
    # case that turns on it. Claiming otherwise is exactly the sort of wrong
    # rule change this benchmark exists to catch.
    monkeypatch.setitem(normalization.ALIASES, "java", "javascript")

    after = runner.run()

    assert ground_truth_digest() == before_digest, "the dataset file changed"
    assert loaded_verdicts() == before_truth, "a human verdict moved with the rules"

    assert after.report.false_matches != before.report.false_matches, (
        "a rule change that makes Java equal JavaScript must show up as a new "
        "false match; if it does not, the benchmark is not measuring the rules"
    )
    assert len(after.report.false_matches) > len(before.report.false_matches)


def test_the_expected_verdicts_do_not_match_the_engine_everywhere():
    """A dataset that agreed with the matcher on every requirement would be
    indistinguishable from one generated by it. Disagreement is not proof of
    independence on its own, but perfect agreement would be evidence against.
    """
    from evaluation.benchmark import runner

    result = runner.run()
    disagreements = [o for o in result.report.observations if not o.agrees]

    assert disagreements, (
        "matching_v1 agrees with every human judgment, which for a hand-written "
        "dataset of this size is more likely to mean the ground truth was copied "
        "from the engine than that the engine is perfect"
    )


def test_the_benchmark_scores_a_deliberately_wrong_matcher_badly():
    """The metrics must be able to say 'bad'. A scoring pipeline that flatters
    everything would report matching_v1 as fine no matter what it did."""
    from evaluation.benchmark.metrics import Observation, Report
    from evaluation.benchmark.schema import VERDICT_MATCHED

    dataset = load()
    everything_matches = Report(algorithm_version="always_matched", dataset_version="x")
    for case in dataset.cases:
        for truth in case.ground_truth:
            everything_matches.observations.append(
                Observation(
                    case_id=case.id,
                    requirement_id=truth.requirement_id,
                    category="skill",
                    aspect="exact_skill",
                    is_required=True,
                    expected=truth.verdict,
                    actual=VERDICT_MATCHED,
                    actual_refs=(),
                    expected_refs=truth.evidence.refs,
                )
            )

    # A matcher that says yes to everything has perfect MATCHED recall and
    # should be damned by precision and by the false-match count instead.
    assert everything_matches.matched.recall.value == 1.0
    assert everything_matches.matched.precision.value < 0.6
    assert len(everything_matches.false_matches) > 50
    assert len(everything_matches.unsupported_matches) > 50
