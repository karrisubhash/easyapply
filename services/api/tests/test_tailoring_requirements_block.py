"""How the posting's requirements are shown to the model (`generation._requirements_block`, v4).

The run3 evaluation's defect, pinned so it cannot come back. The block printed
each label as "- Kubernetes" and never said what the marker was; two of six
documents copied it into every `targeted_requirements` entry, and grounding —
rightly comparing against the posting's own labels — flagged each as a
requirement the posting does not list. The property held here is the same
contract `test_tailoring_profile_block.py` holds for skills: **every
requirement the block shows the model is, once its documented marker is
removed, a label grounding accepts as written.**

No database and no provider: the frozen `tailoring_v1` postings and a
hand-built `JobCandidate` are enough, because the block touches nothing but the
requirements it is handed.
"""

from __future__ import annotations

import json
import re

import pytest

from app.schemas.job_candidate import JobCandidate, JobExperienceItem, JobSkillItem
from app.schemas.tailored_resume import TailoredResumeDocument
from app.services.tailoring import grounding
from app.services.tailoring.generation import (
    PROMPT_VERSION_AI,
    SYSTEM_PROMPT,
    TailoringInputs,
    _build_user_prompt,
    _requirements_block,
    requirement_labels,
)
from evaluation.datasets.tailoring_v1.inputs import JOBS, cases, confirmed_version, structured_job

HEADER = "(each '- ' line is one requirement; the '- ' is a list marker, not part of the requirement)"
MARKER = "- "

CASE_JOBS = [case["job"] for case in cases()["cases"]]


def shown_requirements(prompt: str) -> list[str]:
    """Read `<job_requirements>` back the way the instructions say to: the text
    after the marker. Exactly one marker comes off, so a label that itself
    begins with a hyphen keeps it."""
    match = re.search(r"<job_requirements>\n(.*?)\n</job_requirements>", prompt, re.DOTALL)
    assert match, "the prompt has no <job_requirements> block"
    lines = match[1].splitlines()
    assert lines[0] == HEADER
    for line in lines[1:]:
        assert line.startswith(MARKER), f"unexpected requirement line: {line!r}"
    return [line[len(MARKER) :] for line in lines[1:]]


def user_prompt(job_id: str) -> str:
    job, requirements = structured_job(job_id)
    return _build_user_prompt(
        TailoringInputs(version=confirmed_version(), job=job, requirements=requirements)
    )


def targeting_findings(targeted: list[str], requirements: JobCandidate) -> list[grounding.Finding]:
    """What the EXISTING grounding check says about these targeted labels."""
    document = TailoredResumeDocument(targeted_requirements=targeted)
    report = grounding.validate(
        document,
        confirmed_version(),
        job_requirement_labels=frozenset(requirement_labels(requirements)),
    )
    return [finding for finding in report.findings if finding.section == "targeting"]


def hand_built_requirements() -> JobCandidate:
    """Labels that could confuse a marker: punctuation, an internal ' - ', and a
    label that itself starts with a hyphen."""
    return JobCandidate(
        required_skills=[JobSkillItem(name="C++"), JobSkillItem(name="Node.js - backend")],
        required_experience=[JobExperienceItem(description="- 3+ years on-call")],
        preferred_skills=[JobSkillItem(name="Kubernetes")],
    )


# --- A. each requirement is rendered as a marked line --------------------------------


@pytest.mark.parametrize("job_id", CASE_JOBS)
def test_every_requirement_is_rendered_as_one_marked_line(job_id):
    _, requirements = structured_job(job_id)
    lines = _requirements_block(requirements).splitlines()

    assert lines[0] == HEADER
    assert lines[1:] == [f"{MARKER}{label}" for label in requirement_labels(requirements)]


def test_kubernetes_is_rendered_with_the_marker():
    _, requirements = structured_job("j_platform_k8s")
    assert "- Kubernetes" in _requirements_block(requirements).splitlines()


# --- B. the prompt says the marker is not part of the label --------------------------


@pytest.mark.parametrize("job_id", CASE_JOBS)
def test_the_block_says_the_marker_is_not_part_of_the_requirement(job_id):
    prompt = user_prompt(job_id)
    block = prompt.split("<job_requirements>\n", 1)[1]
    # The explanation is the first thing inside the block, above every label.
    assert block.startswith(HEADER + "\n- ")


def test_the_instructions_say_to_copy_the_text_after_the_marker():
    flattened = " ".join(SYSTEM_PROMPT.split())
    assert (
        "Copy each as the text after its `- ` marker, exactly: the marker is list "
        "layout, never part of the requirement." in flattened
    )


# --- C. the canonical requirement text is unchanged ----------------------------------


@pytest.mark.parametrize("job_id", CASE_JOBS)
def test_the_labels_shown_are_the_postings_own_labels_unchanged(job_id):
    _, requirements = structured_job(job_id)
    shown = shown_requirements(user_prompt(job_id))

    # Same text, same order, as the labels grounding checks against...
    assert shown == requirement_labels(requirements)
    # ...and those are the reviewer-confirmed labels in the frozen dataset.
    raw = json.loads(JOBS.read_text(encoding="utf-8"))
    row = next(item for item in raw["jobs"] if item["id"] == job_id)
    assert sorted(shown) == sorted(requirement["label"] for requirement in row["requirements"])


def test_only_the_marker_comes_off_a_label_with_hyphens_of_its_own():
    requirements = hand_built_requirements()
    lines = _requirements_block(requirements).splitlines()

    assert "- - 3+ years on-call" in lines
    assert "- Node.js - backend" in lines
    assert [line[len(MARKER) :] for line in lines[1:]] == requirement_labels(requirements)
    assert "- 3+ years on-call" in requirement_labels(requirements)


def test_no_requirements_renders_nothing():
    """Unchanged from v3: no header explaining lines that are not there."""
    assert _requirements_block(JobCandidate()) == ""


# --- D. deterministic ---------------------------------------------------------------


@pytest.mark.parametrize("job_id", CASE_JOBS)
def test_the_prompt_is_deterministic(job_id):
    assert user_prompt(job_id) == user_prompt(job_id)
    _, requirements = structured_job(job_id)
    assert _requirements_block(requirements) == _requirements_block(requirements)


# --- E. "Kubernetes" is targeted as "Kubernetes" -------------------------------------


def test_kubernetes_is_targeted_as_kubernetes_not_as_its_line():
    _, requirements = structured_job("j_platform_k8s")
    shown = shown_requirements(user_prompt("j_platform_k8s"))

    # Following the instruction turns the line "- Kubernetes" into "Kubernetes"...
    assert "Kubernetes" in shown
    assert "- Kubernetes" not in shown
    # ...which grounding accepts as written, while the line itself stays rejected.
    assert targeting_findings(["Kubernetes"], requirements) == []
    [finding] = targeting_findings(["- Kubernetes"], requirements)
    assert finding.claim == "- Kubernetes"
    assert finding.reason == grounding.REASON_UNTARGETED


# --- the invariant: shown, minus the marker, is what grounding accepts ---------------


@pytest.mark.parametrize("job_id", CASE_JOBS)
def test_every_requirement_shown_is_one_grounding_accepts_once_the_marker_is_removed(job_id):
    _, requirements = structured_job(job_id)
    shown = shown_requirements(user_prompt(job_id))

    assert shown  # not vacuous
    assert targeting_findings(shown, requirements) == []


@pytest.mark.parametrize("job_id", CASE_JOBS)
def test_every_requirement_line_copied_with_its_marker_is_still_rejected(job_id):
    """Grounding is not what changed: the marker is still no part of any label."""
    _, requirements = structured_job(job_id)
    lines = [f"{MARKER}{label}" for label in shown_requirements(user_prompt(job_id))]

    findings = targeting_findings(lines, requirements)
    assert [finding.claim for finding in findings] == lines
    assert all(finding.reason == grounding.REASON_UNTARGETED for finding in findings)


def test_the_prompt_version_records_the_change():
    """Stored on every run; a document must not be read against an older prompt.

    v5 left this block's header and labels exactly as v4 wrote them — the
    tests above still hold — and changed the fence around it and the rules; see
    `test_tailoring_ai.py` and `test_tailoring_security.py`.
    """
    assert PROMPT_VERSION_AI == "tailoring_ai_v6"
