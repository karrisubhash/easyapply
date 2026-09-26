"""Proving the non_doing_v1 benchmark's expected answers cannot come from the
matcher.

Mirrors `tests/test_benchmark_independence.py` for the real-data matching
benchmark, applied to this narrower one. Three independent arguments:

  1. **Structural.** `schema.py`, `groundtruth.py` and `metrics.py` do not
     import `app.services.matching`, transitively or otherwise.
  2. **Textual.** The dataset names no engine internals outside its own
     `review` block, which is explicitly permitted to say what it is proving.
  3. **Behavioural.** Changing the marker set changes the RESULTS and leaves
     the ground truth byte-identical.

If this file goes red, the benchmark has stopped measuring the vocabulary and
started reflecting it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from evaluation.nondoing import groundtruth
from evaluation.nondoing.groundtruth import DATASETS, DEFAULT_VERSION, load

PACKAGE = Path(groundtruth.__file__).parent

MATCHER_FREE = ("schema.py", "groundtruth.py", "metrics.py")

FORBIDDEN_ROOTS = ("app.services.matching", "app.models.match", "app.models.profile_version")


def imports_of(path: Path) -> set[str]:
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
    seen = seen if seen is not None else set()
    path = PACKAGE / start
    for name in imports_of(path):
        if name in seen:
            continue
        seen.add(name)
        if name.startswith("evaluation.nondoing."):
            leaf = name.rsplit(".", 1)[-1] + ".py"
            if (PACKAGE / leaf).exists():
                transitive_imports(leaf, seen)
    return seen


# --- 1. structural --------------------------------------------------------------


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
    reachable = transitive_imports(module)
    assert [name for name in reachable if name.startswith("app")] == []


def test_the_adapter_is_the_only_seam():
    crossing = []
    for path in PACKAGE.glob("*.py"):
        reachable = transitive_imports(path.name)
        if any(name.startswith(root) for name in reachable for root in FORBIDDEN_ROOTS):
            crossing.append(path.name)
    assert sorted(crossing) == ["adapter.py", "runner.py"], (
        "runner.py crosses only by importing the adapter; anything else here is a leak"
    )


def test_the_adapter_defers_its_matcher_imports():
    source = (PACKAGE / "adapter.py").read_text(encoding="utf-8")
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


# --- 2. textual -------------------------------------------------------------------


def dataset_path() -> Path:
    return DATASETS / DEFAULT_VERSION / "cases.json"


def test_the_dataset_names_no_engine_internals():
    """The review block legitimately names the algorithm and the modules it is
    proving independence from; the CASES must be clean."""
    forbidden = (
        "is_non_doing(",
        "demonstrates(",
        "NON_DOING_MARKERS",
        "ProfileIndex",
        "app.services",
        "judge_token_requirement",
        "judge_experience",
        "resolve_technology_subject",
        "ALGORITHM_VERSION",
        "matching_v4",
        "matching_v5",
        "canonical(",
    )
    payload = json.loads(dataset_path().read_text(encoding="utf-8"))
    text = json.dumps(
        {key: value for key, value in payload.items() if key not in ("review",)}
    )
    for term in forbidden:
        assert term not in text, f"cases.json mentions {term!r} outside its review block"


def test_every_rationale_argues_from_the_text():
    """A rationale should talk about the sentence, not about an output."""
    engine_word = re.compile(r"\bthe engine\b")
    dataset = load()
    for case in dataset.sentence_cases:
        lowered = case.rationale.lower()
        assert "the matcher" not in lowered
        assert not engine_word.search(lowered)
        assert "is_non_doing" not in lowered
    for case in dataset.pipeline_cases:
        lowered = case.note.lower()
        assert "the matcher" not in lowered
        assert not engine_word.search(lowered)


def test_the_dataset_declares_its_independence():
    dataset = load()
    statement = dataset.review.independence_statement.lower()
    assert "no verdict" in statement or "no tier" in statement
    assert "must not be edited to agree" in statement


# --- 3. behavioural -----------------------------------------------------------


def dataset_digest() -> str:
    return hashlib.sha256(dataset_path().read_bytes()).hexdigest()


def loaded_sentence_expectations() -> dict[str, bool]:
    dataset = load()
    return {case.id: case.expected_non_doing for case in dataset.sentence_cases}


def test_changing_the_marker_vocabulary_moves_the_score_and_not_the_truth(monkeypatch):
    """The proof that matters: adding a word to the live marker set is a real
    rule change. If the dataset's expected answers were derived from the
    rules in any way, they would move with it. They must not."""
    from evaluation.nondoing import runner

    before_digest = dataset_digest()
    before_truth = loaded_sentence_expectations()
    before = runner.run()

    from app.services.matching import vocabulary

    # "built" names no marker today; adding it is a real (if deliberately bad)
    # rule change that reaches several clauses the dataset expects to read as
    # affirmative.
    monkeypatch.setattr(vocabulary, "_SINGLE_MARKERS", vocabulary._SINGLE_MARKERS | {"built"})

    after = runner.run()

    assert dataset_digest() == before_digest, "the dataset file changed"
    assert loaded_sentence_expectations() == before_truth, "an expected value moved with the rules"

    assert after.report.sentence_agreement.numerator != before.report.sentence_agreement.numerator, (
        "adding a marker word must change the measured score; if it does not, this "
        "benchmark is not actually exercising is_non_doing()"
    )


def test_the_expected_answers_do_not_match_the_implementation_everywhere():
    """A dataset that agreed with is_non_doing on every case would be
    indistinguishable from one generated by running it."""
    from evaluation.nondoing import runner

    result = runner.run()
    disagreements = [o for o in result.report.all_observations if not o.agrees]

    assert disagreements, (
        "the implementation agrees with every authored expectation, which for a "
        "hand-written dataset of this size is more likely to mean the expectations "
        "were copied from the implementation than that the vocabulary is flawless"
    )


def test_the_benchmark_can_report_a_bad_score():
    """The scoring must be able to say 'wrong'. A `Rate` of 0 must print and
    compare as such."""
    from evaluation.nondoing.metrics import Rate

    zero = Rate(0, 10)
    assert zero.value == 0.0
    assert "0.000" in str(zero)
