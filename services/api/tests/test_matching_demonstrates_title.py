"""Step 4 of Matching V5: `evidence.demonstrates_title`.

Every one of the ten `job_title` non_doing_v1 sentence cases expects
`is_non_doing` to be `False` (confirmed by re-querying the dataset while
designing this step) — there is no case where a bare title should read as
non-doing via an ordinary NON_DOING_MARKERS word. This function is what a
real Experience.job_title is graded by from matching_v5 onward; the ten
sentence cases themselves are unaffected (they call `is_non_doing()`
directly, which this does not touch), which is a scope boundary, not an
oversight — see docs/matching-v5-implementation-design.md §2.4.
"""

import pytest

from app.services.matching.evidence import demonstrates_title


@pytest.mark.parametrize(
    "title",
    [
        "Learning Experience Designer",
        "Curriculum and Learning Technologist",
        "Retired Systems Architect - Independent Java Consultant",
        "VP of Engineering (Retired), 20 Years Building Distributed Java Systems",
        "Kubernetes Migration - Planning & Delivery Lead",
        "Site Reliability Engineer",
        "Research Scientist, Applied Machine Learning",
        "Senior Data Engineer, Machine Learning Platform",
        "Exploration & Onboarding Lead, Customer Success",
        "Machine Learning Research Engineer, Autonomous Systems",
    ],
)
def test_every_non_doing_v1_job_title_case_is_affirmed(title):
    """The ordinary marker check (evaluated/considered/planning/retired/...)
    is never consulted for a title at all -- these titles contain several of
    those words and must still come back True."""
    assert demonstrates_title(title) is True


def test_a_title_with_an_explicit_negation_is_still_weakened():
    """The one thing that CAN still weaken a title: component 3's negation
    patterns, which are a different, function-word-scoped signal from the
    ordinary marker check this function otherwise skips entirely. Uses one
    of the six curated patterns (negation.py), not an untested phrase --
    "never certified" is a plausible-sounding but NOT curated pattern, and
    correctly does not fire; see test_matching_negation.py for the exact
    list."""
    assert demonstrates_title("Kubernetes Administrator (Never Used in Production)") is False


def test_empty_and_none_titles_default_affirmative():
    assert demonstrates_title("") is True
    assert demonstrates_title(None) is True
