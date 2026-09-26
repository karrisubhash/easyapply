"""What the posting is asking for — the signal every selection decision uses.

These are unit tests for the thing that was actually broken. The tailored resume
was poor not because the renderer was wrong or the generator was careless, but
because the number both of them ranked by was mostly noise: against a real
posting, 28 of 37 scoring terms were stopwords, eligibility text or the filler
every posting and every resume share.

So each test here names one word that must not be able to decide what leads a
resume, or one relationship between requirements that must hold whatever the
posting says. They are deliberately below `test_tailoring_evaluation.py`: that
file proves the documents came out right, these prove why, and a failure here
says which rule broke rather than which document changed.
"""

from __future__ import annotations

import pytest

from app.services.tailoring import relevance
from tests.matching_fixtures import a_candidate


def target(**kwargs):
    return relevance.read_posting(a_candidate(**kwargs))


# --- the words that must not count ---------------------------------------------------


@pytest.mark.parametrize("word", ["the", "to", "in", "of", "with", "within", "be", "or"])
def test_a_stopword_is_not_a_requirement(word):
    """"with" was missing from this list beside "within" and "without", and that
    single omission let a Django employment requirement be claimed by a document
    containing no Django, on the strength of the word "with"."""
    posting = target(required_experience=[(f"5+ years {word} something", None)])

    assert word not in posting.terms


@pytest.mark.parametrize(
    "filler",
    ["experience", "years", "professional", "software", "engineering", "production",
     "services", "building", "developer", "systems", "team"],
)
def test_resume_filler_is_not_a_requirement(filler):
    """A word appearing in substantially every posting AND substantially every
    bullet discriminates between no two candidates, while looking like a hit.

    "Reduced infrastructure spend by 31% by right-sizing ECS services" outranked
    the candidate's flagship data-pipeline bullet on exactly one word: "services".
    """
    posting = target(required_experience=[(f"5+ years of {filler} work", None)])

    assert filler not in posting.terms


@pytest.mark.parametrize(
    "label",
    [
        "Must be legally authorised to work in the EU",
        "Right to work in Portugal",
        "Based in Berlin, or within two hours of CET",
        "Based in Frankfurt, or hybrid from elsewhere in Germany",
        "Willing to relocate to Dublin",
    ],
)
def test_a_requirement_no_resume_can_evidence_scores_nothing(label):
    """A visa and a commute are real requirements and no achievement is evidence
    of either.

    The concrete failure: a Kafka bullet was promoted to the top of a resume
    because "18 hours" matched "within two hours of CET". A commute policy chose
    which achievement the candidate led with.
    """
    posting = target(qualifications=[(label, True)])

    requirement = next(r for r in posting.requirements if r.label == label)
    assert not requirement.evidenceable
    assert requirement.terms == frozenset()
    assert relevance.score("Reduced staleness from 18 hours to 2 minutes", posting) == 0.0


def test_a_qualification_a_resume_CAN_evidence_still_counts():
    """The eligibility rule is about visas and geography, not about
    qualifications in general. On-call participation is a thing people do."""
    posting = target(qualifications=[("Participation in an on-call rotation", True)])

    requirement = posting.requirements[0]
    assert requirement.evidenceable
    assert relevance.score("Mentored 4 engineers through on-call onboarding.", posting) > 0


# --- the words that must count -------------------------------------------------------


def test_a_named_skill_is_never_stopworded():
    """"Go" is a two-letter word and a required language. A named thing is signal
    whatever it is spelled like — which is why the stopword list is applied to
    free text only."""
    posting = target(required_skills=["Go"])

    assert "go" in posting.terms
    assert relevance.score("Wrote the ingest daemon in Go.", posting) > 0


def test_a_required_skill_outranks_a_preferred_one():
    posting = target(required_skills=["Python"], preferred_skills=["Redis"])

    assert relevance.score("Built it in Python", posting) > relevance.score(
        "Cached it in Redis", posting
    )


def test_a_named_skill_outranks_a_content_word():
    """One required skill is worth more than two words from a sentence. A reader
    of the posting would apply the same ordering."""
    posting = target(
        required_skills=["Kubernetes"],
        required_experience=[("Experience with regulated healthcare data", None)],
    )

    assert relevance.score("Ran the Kubernetes estate", posting) > relevance.score(
        "Worked on regulated healthcare records", posting
    )


def test_a_repeated_term_is_not_worth_more():
    """Distinct hits, never occurrences: counting occurrences ranks a
    keyword-stuffed sentence above a substantive one."""
    posting = target(required_skills=["Python"])

    once = relevance.score("Built the service in Python.", posting)
    thrice = relevance.score("Python, Python and more Python.", posting)
    assert once == thrice


# --- ordering ------------------------------------------------------------------------


def test_skills_are_ordered_by_what_the_posting_asked_for_first():
    """A skills line is scanned, not read. Alphabetical order put AWS in front of
    Python on a posting that led with Python."""
    posting = target(required_skills=["Python", "Django"], preferred_skills=["Redis"])

    assert relevance.rank_names(["Redis", "Django", "Python"], posting) == [
        "Python",
        "Django",
        "Redis",
    ]


def test_a_skill_the_posting_never_named_sorts_last_and_keeps_its_order():
    posting = target(required_skills=["Python"])

    assert relevance.rank_names(["Kafka", "Python", "dbt"], posting) == [
        "Python",
        "Kafka",
        "dbt",
    ]


# --- what a document may claim to have targeted ---------------------------------------


def evidence(*, prose=(), listed=(), education=()):
    return relevance.Evidence(
        prose=frozenset(prose), listed=frozenset(listed), education=frozenset(education)
    )


def test_a_certification_needs_its_whole_name():
    """"AWS Certified Developer" and "AWS Certified Solutions Architect" share
    two words of three. Matching on any word claims the first credential from a
    candidate who holds only the second — a fabricated qualification, in the one
    field a reviewer reads to check the document aimed correctly.
    """
    posting = target(certifications=[("AWS Certified Developer", False)])
    held = evidence(listed={"aws", "certified", "solutions", "architect"})

    assert relevance.answered(held, posting) == []


def test_a_certification_the_candidate_holds_is_claimed():
    posting = target(certifications=[("Certified Kubernetes Administrator", True)])
    held = evidence(listed={"certified", "kubernetes", "administrator"})

    assert relevance.answered(held, posting) == ["Certified Kubernetes Administrator"]


def test_a_skills_line_does_not_evidence_an_employment_requirement():
    """The sharpest judgement in the module. "3+ years building production
    services with Django" is a claim about employment; a skills line reading
    "Django" does not evidence it, and a document saying otherwise tells the
    reader it has three years of Django on it when it has a word in a list.
    """
    posting = target(
        required_skills=["Django"],
        required_experience=[("3+ years building production services with Django", 3)],
    )
    # Django is in the skills section and in no bullet.
    held = evidence(listed={"django"}, prose={"python", "postgresql"})

    claimed = relevance.answered(held, posting)
    assert "Django" in claimed, "the skill IS evidenced, by the skills line"
    assert "3+ years building production services with Django" not in claimed


def test_an_employment_requirement_is_claimed_when_a_bullet_answers_it():
    posting = target(
        required_experience=[("3+ years building production services with Django", 3)]
    )
    held = evidence(prose={"django", "built", "the", "admin"})

    assert relevance.answered(held, posting) == [
        "3+ years building production services with Django"
    ]


def test_an_eligibility_requirement_is_never_claimed():
    """The document said it targeted "Based in Berlin, or within two hours of
    CET" for a candidate in Bristol, because "18 hours to under 2 minutes"
    supplied the words "hours" and "in"."""
    posting = target(qualifications=[("Based in Berlin, or within two hours of CET", True)])
    held = evidence(prose={"in", "hours", "two", "the", "berlin"})

    assert relevance.answered(held, posting) == []


def test_a_degree_is_claimed_from_the_education_section_only():
    posting = target(
        education=[("Bachelor's degree in Computer Science", "bachelor", None, True)]
    )

    from_prose = evidence(prose={"computer", "science", "bachelor"})
    from_education = evidence(education={"bsc", "computer", "science"})

    assert relevance.answered(from_prose, posting) == []
    assert relevance.answered(from_education, posting) == [
        "Bachelor's degree in Computer Science"
    ]


def test_required_requirements_are_claimed_before_preferred_ones():
    posting = target(required_skills=["Python"], preferred_skills=["Redis"])
    held = evidence(listed={"python", "redis"})

    assert relevance.answered(held, posting) == ["Python", "Redis"]


def test_emphasis_changes_order_and_not_what_may_be_claimed():
    """A requirement a prior match could not evidence is still missing. Emphasis
    moves true sentences forward; it never adds a term, so it can never make a
    document claim something it does not evidence."""
    plain = relevance.read_posting(a_candidate(required_skills=["Python"]))
    nudged = relevance.read_posting(
        a_candidate(required_skills=["Python"]), emphasise=("Rust",)
    )

    assert "rust" not in nudged.terms
    assert set(nudged.terms) == set(plain.terms)


# --- free text: one coincidental word is not evidence -------------------------------
#
# The review that produced these found `targeted_requirements` claiming
#
#     "Experience in regulated financial services"
#
# for a candidate who has worked in logistics and healthcare, on the strength of
# the single word "regulated" in their summary ("...across two regulated
# industries"). That is a false statement about what the document aimed at, in
# the one field a reviewer reads to check exactly that.
#
# What makes it hard is the requirement beside it. The SAME token, from the SAME
# sentence, is also the only thing supporting
#
#     "Experience in healthcare or another regulated domain"
#
# and THAT claim is correct — the posting says any other regulated domain will
# do, and the candidate's is one. Against the evaluation dataset every free-text
# claim, right and wrong alike, rested on exactly one matched token, so no
# threshold on count separates them; proportion actively ranks them the wrong
# way round (one word of two, against one word of four).
#
# They are separated by what the requirement will ACCEPT, not by how much of it
# matched. See `relevance._alternatives`.


def test_a_single_shared_adjective_does_not_evidence_a_compound_requirement():
    """The reported defect. "regulated" attaches to finance, healthcare,
    aviation and gambling alike; a document containing it has said nothing about
    which one."""
    posting = target(
        preferred_experience=[("Experience in regulated financial services", None)]
    )
    coincidental = evidence(prose={"regulated", "industries", "python", "kafka"})

    assert relevance.answered(coincidental, posting) == []


def test_the_same_requirement_is_claimed_once_the_document_covers_it():
    """Not a ban on the requirement — a demand that the document earn it."""
    posting = target(
        preferred_experience=[("Experience in regulated financial services", None)]
    )
    covered = evidence(prose={"regulated", "financial", "ledgers", "reconciliation"})

    assert relevance.answered(covered, posting) == [
        "Experience in regulated financial services"
    ]


def test_an_alternative_the_document_fully_covers_is_enough():
    """"or" means alternatives. The posting states that any other regulated
    domain will do, and a profile saying "two regulated industries" has answered
    that alternative completely rather than partially answering one requirement.
    """
    posting = target(
        preferred_experience=[
            ("Experience in healthcare or another regulated domain", None)
        ]
    )

    by_the_second = evidence(prose={"regulated", "industries"})
    by_the_first = evidence(prose={"healthcare", "records"})

    assert relevance.answered(by_the_second, posting) == [
        "Experience in healthcare or another regulated domain"
    ]
    assert relevance.answered(by_the_first, posting) == [
        "Experience in healthcare or another regulated domain"
    ]


def test_an_alternative_is_not_satisfied_by_a_word_from_each_side():
    """Alternatives are answered whole or not at all. Taking one word from one
    branch and one from another is the original defect wearing a disjunction."""
    posting = target(
        preferred_experience=[("Senior Kafka administration or Redis operations", None)]
    )
    split_across = evidence(prose={"kafka", "operations"})

    assert relevance.answered(split_across, posting) == []


def test_or_inside_a_word_does_not_split_a_requirement():
    """"orchestration" and "monitoring" contain "or". A matcher that splits on
    them is reading requirements that were never written."""
    posting = target(required_experience=[("Kafka orchestration and monitoring", None)])

    assert relevance.answered(evidence(prose={"kafka"}), posting) == []
    assert relevance.answered(
        evidence(prose={"kafka", "orchestration", "monitoring"}), posting
    ) == ["Kafka orchestration and monitoring"]


def test_a_term_of_art_carries_its_alternative_alone():
    """"Participation in an on-call rotation" is the named practice "on-call"
    wrapped in a sentence. A document saying "on-call" is talking about it —
    unlike a document saying "regulated", which is talking about anything.

    The same observation grounding's proper-noun check rests on: nothing in
    ordinary English prose is spelled with an internal hyphen.
    """
    posting = target(qualifications=[("Participation in an on-call rotation", True)])
    bullet = evidence(prose={"mentored", "engineers", "on-call", "onboarding"})

    assert relevance.answered(bullet, posting) == [
        "Participation in an on-call rotation"
    ]


def test_an_ordinary_word_from_a_requirement_with_a_term_of_art_is_not_enough():
    """The term of art is what must match, not the phrasing around it."""
    posting = target(qualifications=[("Participation in an on-call rotation", True)])

    assert relevance.answered(evidence(prose={"rotation", "shift"}), posting) == []


def test_a_requirement_stating_only_a_duration_can_never_be_claimed():
    """"5+ years of professional experience" has no topic in it. Nothing on a
    resume evidences it, and a document claiming to have targeted it is claiming
    to have targeted a number."""
    posting = target(required_experience=[("5+ years of professional experience", 5)])
    anything = evidence(prose={"python", "years", "experience", "professional", "5+"})

    assert relevance.answered(anything, posting) == []


def test_a_duration_does_not_block_the_topic_beside_it():
    """The counterpart. "3+ years building production services with Django" is
    about Django, and a document with Django bullets has spoken to it — the
    duration is `minimum_years`, which is matching's job to judge rather than
    tailoring's. Requiring the literal characters "3+" in a bullet would make
    every requirement stating a number permanently unanswerable, which is a
    different wrong answer rather than a stricter one."""
    posting = target(
        required_experience=[("3+ years building production services with Django", 3)]
    )

    assert relevance.answered(evidence(prose={"django", "admin"}), posting) == [
        "3+ years building production services with Django"
    ]


# --- the kinds that did NOT change --------------------------------------------------


@pytest.mark.parametrize(
    "name,held,claimed",
    [
        ("Docker", {"docker"}, True),
        ("Kubernetes", {"kubernetes"}, True),
        ("Redis", {"postgresql", "python"}, False),
        # A named thing still needs its whole name.
        ("Amazon Web Services", {"amazon", "web"}, False),
        ("Amazon Web Services", {"amazon", "web", "services"}, True),
    ],
)
def test_named_technology_requirements_are_unchanged(name, held, claimed):
    posting = target(technologies=[(name, True)])

    assert bool(relevance.answered(evidence(listed=held), posting)) is claimed


@pytest.mark.parametrize(
    "name,held,claimed",
    [
        (
            "Certified Kubernetes Administrator",
            {"certified", "kubernetes", "administrator"},
            True,
        ),
        # Two words of three shared with the credential actually held.
        ("AWS Certified Developer", {"aws", "certified", "solutions", "architect"}, False),
        (
            "AWS Certified Solutions Architect",
            {"aws", "certified", "solutions", "architect"},
            True,
        ),
    ],
)
def test_named_certification_requirements_are_unchanged(name, held, claimed):
    posting = target(certifications=[(name, False)])

    assert bool(relevance.answered(evidence(listed=held), posting)) is claimed


@pytest.mark.parametrize(
    "held,claimed",
    [
        ({"bsc", "computer", "science"}, True),
        ({"bsc", "mechanical", "engineering"}, False),
    ],
)
def test_education_requirements_are_unchanged(held, claimed):
    """Education stays on the permissive rule, deliberately. A degree is checked
    against the education section alone — institution, degree and field, all
    transcribed — so two words landing there is not the coincidence that two
    words landing anywhere in a page of prose is."""
    posting = target(
        education=[
            (
                "Bachelor's degree in Computer Science or a related field",
                "bachelor",
                None,
                True,
            )
        ]
    )

    assert bool(relevance.answered(evidence(education=held), posting)) is claimed


def test_a_posting_the_candidate_does_not_match_claims_nothing():
    """The React/JavaScript/Jest case, at the unit level. None of these is
    anywhere in the profile, and the free-text requirement beside them is carried
    by a term of art the document does not have."""
    posting = target(
        required_skills=["React", "TypeScript", "JavaScript"],
        preferred_skills=["React Native"],
        technologies=[("Jest", False)],
        required_experience=[("4+ years building production web front-ends", 4)],
    )
    backend = evidence(
        prose={"python", "fastapi", "kafka", "postgresql", "built", "service"},
        listed={"python", "postgresql", "typescript", "docker"},
    )

    assert relevance.answered(backend, posting) == ["TypeScript"]


# --- the safety property ------------------------------------------------------------


def test_tightening_can_only_ever_remove_a_claim():
    """The property that makes this change safe to ship.

    Relevance decides which true things are shown. It must never authorise a
    claim grounding cannot independently prove — and grounding is unchanged,
    checking `targeted_requirements` against the posting's own labels without
    consulting this module at all.

    This asserts the direction: for every requirement and every evidence set
    below, what the new rule claims is a SUBSET of what the old
    one-distinctive-word rule claimed. A tightening that could add a claim would
    not be a tightening, and no amount of reading the two rules proves that as
    cheaply as trying them.
    """
    posting = target(
        required_skills=["Python", "Go"],
        preferred_skills=["Redis"],
        technologies=[("Docker", True), ("Jest", False)],
        required_experience=[("3+ years building production services with Django", 3)],
        preferred_experience=[
            ("Experience in regulated financial services", None),
            ("Experience in healthcare or another regulated domain", None),
        ],
        certifications=[("AWS Certified Developer", False)],
        education=[("Bachelor's degree in Computer Science", "bachelor", None, True)],
        qualifications=[("Participation in an on-call rotation", True)],
    )

    vocabulary = sorted(
        {term for requirement in posting.requirements for term in requirement.terms}
    )
    # Every single-token evidence set, plus every prefix of the vocabulary. Each
    # is a chance for the new rule to claim something the permissive one did not.
    probes = [{term} for term in vocabulary]
    probes += [set(vocabulary[:index]) for index in range(len(vocabulary) + 1)]

    for probe in probes:
        held = evidence(prose=probe, listed=probe, education=probe)
        new = set(relevance.answered(held, posting))
        permissive = {
            requirement.label
            for requirement in posting.requirements
            if requirement.evidenceable and (requirement.terms & frozenset(probe))
        }
        assert new <= permissive, f"probe {sorted(probe)} gained {sorted(new - permissive)}"


def test_a_requirement_the_posting_never_stated_is_never_claimed():
    """Relevance only ever returns labels the posting supplied. Grounding checks
    this independently and warns when it fails; the two agreeing is not an
    accident worth resting on, so it is asserted here too."""
    posting = target(
        required_skills=["Python"], qualifications=[("Ships on Fridays", True)]
    )
    stated = {requirement.label for requirement in posting.requirements}

    held = evidence(prose={"python", "ships", "fridays"}, listed={"python"})
    assert set(relevance.answered(held, posting)) <= stated
