"""The matching domain: what a profile version satisfies in a job's requirements.

Three tables, deliberately relational rather than one JSON blob, because the
questions this data exists to answer are set-shaped: "which required skills are
missing across all my matches", "which jobs have high required coverage",
"what are my most common gaps". A JSONB payload would answer none of those
without a scan — the opposite tradeoff from `JobRequirements.payload`, which is
read whole, always, and never queried by individual item.

    MatchResult               one (profile version x job requirements x
                              algorithm) evaluation. Holds the counts and the
                              score COMPONENTS as columns, so the score is
                              queryable and auditable rather than a number
                              whose derivation is lost.

    MatchRequirementResult    one job requirement, judged. MATCHED, MISSING or
                              UNCERTAIN, with the explanation in our own words.

    MatchEvidence             the profile-version rows that justify a judgment.
                              Evidence is required for MATCHED and UNCERTAIN;
                              MISSING means the matcher looked and found none.

Reproducibility (the reason for every foreign key here):

  * `profile_version_id` — a CONFIRMED version is immutable, so the candidate
    side of the evaluation cannot move underneath a stored result. The service
    layer refuses to match against a draft for exactly this reason.
  * `job_requirements_id` — NOT `job_id` alone. `job_requirements` is written
    once per completed processing run and never updated, so pinning the row
    pins the exact requirement text, structurer, model and prompt version that
    produced it. Reprocessing a job creates a NEW requirements row and
    therefore a genuinely new match, rather than silently reinterpreting an
    old one.
  * `algorithm_version` — the rules themselves are an input. matching_v1's
    verdicts must stay interpretable after matching_v2 exists.
  * `as_of_date` — "5+ years of experience" is a question about a moment. A
    result computed from `date.today()` and not recording which day that was
    would be unreproducible by construction; storing it makes re-running the
    same evaluation give the same answer.

Those four together are the whole reproducibility story, and the first three
are also the idempotency key — see the unique constraint below.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

#: Bumped when the RULES change in a way that could change a verdict. Stored on
#: every result; never inferred from a timestamp. See
#: app/services/matching/engine.py, which owns the current value.
#:
#: Every version ever shipped stays here. A stored result keeps the value it
#: was produced under, and a reader six months from now needs the name to
#: still mean something — deleting v1 when v2 arrived would leave every
#: historical row pointing at a version nobody can look up.
ALGORITHM_V1 = "matching_v1"
#: Technology-vocabulary subject resolution, and four-tier graded evidence.
#: See app/services/matching/vocabulary.py and evidence.py.
ALGORITHM_V2 = "matching_v2"
#: A work-action inflection table, so "experience leading a team" is answered
#: by "Lead four engineers". Narrow by construction and reachable only from
#: the non-technology scan — see app/services/matching/morphology.py.
ALGORITHM_V3 = "matching_v3"
#: Deterministic location matching: city-or-country equality against the
#: candidate's current employment location. See
#: app/services/matching/geography.py and rules.judge_location.
ALGORITHM_V4 = "matching_v4"
#: Clause-level non-doing evidence refined into scope-bounded spans, plus
#: three narrow, independent additions: nearest-technology-token
#: reattachment for a trailing relative clause, an explicit plain-English
#: negation check kept apart from NON_DOING_MARKERS, and title-aware
#: indexing for Experience.job_title. See app/services/matching/span_split.py,
#: negation.py, and evidence.demonstrates_title. NON_DOING_MARKERS,
#: is_non_doing(), demonstrates(), and resolve_technology_subject() are
#: unchanged; nothing here is a model, an embedding, or a parser.
ALGORITHM_V5 = "matching_v5"
#: Safety corrections to matching_v5, each found by an offline probe producing
#: a verdict the profile does not support: auxiliary and span-initial denials
#: ("Didn't use Django", "Unfamiliar with Django") read as paid work; a field of
#: work confirmed by one generic word; years counted past `as_of`; a degree in
#: progress counted as
#: held; "X and Y" judged on X alone; one-letter subjects read out of "R&D".
#: See the v5 -> v5_1 note in app/services/matching/engine.py. Named v5_1 and
#: not v6 because "matching_v6" is the declared-only redesign already in
#: progress (docs/matching-v6-*.md, "matching_v6 Phase 1" in profile_index.py),
#: which this is not.
ALGORITHM_V5_1 = "matching_v5_1"

# --- the three verdicts ------------------------------------------------------
#
# Three, not four. A fourth state has been deliberately resisted: every
# candidate for one ("partially matched", "weakly matched", "expired") is
# really a statement about CONFIDENCE or about EVIDENCE, and both of those are
# already first-class here — confidence is a column and evidence is a table.
# Adding a state would put the same information in two places and let them
# disagree.

#: The profile version contains structured evidence that satisfies the
#: requirement.
STATUS_MATCHED = "matched"
#: The matcher looked and found no qualifying evidence at all.
STATUS_MISSING = "missing"
#: Evidence exists but does not settle the question — a technology named only
#: in prose, a degree in an unrelated field, a duration that cannot be
#: computed from what the profile actually states. Distinct from MISSING on
#: purpose: "we found nothing" and "we found something we cannot interpret"
#: lead a user to do completely different things.
STATUS_UNCERTAIN = "uncertain"

MATCH_STATUSES = (STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN)

# --- what kind of requirement was judged -------------------------------------
#
# Mirrors the JobCandidate categories that are deterministically checkable.
# `responsibilities` is absent by design: a responsibility describes the role
# once hired, not a bar the candidate clears, and the JobCandidate schema
# already says so by giving it no required/preferred axis.

CATEGORY_SKILL = "skill"
CATEGORY_TECHNOLOGY = "technology"
CATEGORY_EXPERIENCE = "experience"
CATEGORY_EDUCATION = "education"
CATEGORY_CERTIFICATION = "certification"
#: Free-text requirements ("Must be authorized to work in the US"). V1 cannot
#: check these against profile data at all, so they are recorded as UNCERTAIN
#: with an explanation saying so, rather than dropped. Silently discarding a
#: real requirement would make coverage look better than it is.
CATEGORY_QUALIFICATION = "qualification"

MATCH_CATEGORIES = (
    CATEGORY_SKILL,
    CATEGORY_TECHNOLOGY,
    CATEGORY_EXPERIENCE,
    CATEGORY_EDUCATION,
    CATEGORY_CERTIFICATION,
    CATEGORY_QUALIFICATION,
)

# --- where a piece of evidence came from -------------------------------------

EVIDENCE_SKILL = "skill"
EVIDENCE_PROJECT_TECHNOLOGY = "project_technology"
EVIDENCE_EXPERIENCE = "experience"
EVIDENCE_PROJECT = "project"
EVIDENCE_EDUCATION = "education"
EVIDENCE_CERTIFICATION = "certification"

EVIDENCE_SOURCES = (
    EVIDENCE_SKILL,
    EVIDENCE_PROJECT_TECHNOLOGY,
    EVIDENCE_EXPERIENCE,
    EVIDENCE_PROJECT,
    EVIDENCE_EDUCATION,
    EVIDENCE_CERTIFICATION,
)

# --- how strong a piece of evidence is ----------------------------------------
#
# The grade a piece of evidence was counted at. matching_v1 had no such
# column because it had only two kinds of evidence; the real-data benchmark
# showed that binary costing verdicts in both directions, and the tier is what
# lets a stored result say WHY a listed skill was or was not enough.
#
# A local copy of app/services/matching/evidence.py's vocabulary, deliberately:
# the model owns what may be written to the column, and a CHECK constraint
# cannot import a service module.

EVIDENCE_TIER_DECLARED_CORROBORATED = "declared_corroborated"
EVIDENCE_TIER_EMPLOYMENT_DESCRIBED = "employment_described"
EVIDENCE_TIER_DECLARED_ONLY = "declared_only"
EVIDENCE_TIER_MENTIONED = "mentioned"

EVIDENCE_TIERS = (
    EVIDENCE_TIER_DECLARED_CORROBORATED,
    EVIDENCE_TIER_EMPLOYMENT_DESCRIBED,
    EVIDENCE_TIER_DECLARED_ONLY,
    EVIDENCE_TIER_MENTIONED,
)

# --- what a requirement demanded of its evidence -------------------------------

STANDARD_ANY_EVIDENCE = "any_evidence"
STANDARD_PROFESSIONAL_USE = "professional_use"

EVIDENCE_STANDARDS = (STANDARD_ANY_EVIDENCE, STANDARD_PROFESSIONAL_USE)

_STATUS_SQL = ", ".join(f"'{value}'" for value in MATCH_STATUSES)
_CATEGORY_SQL = ", ".join(f"'{value}'" for value in MATCH_CATEGORIES)
_EVIDENCE_SQL = ", ".join(f"'{value}'" for value in EVIDENCE_SOURCES)
_TIER_SQL = ", ".join(f"'{value}'" for value in EVIDENCE_TIERS)
_STANDARD_SQL = ", ".join(f"'{value}'" for value in EVIDENCE_STANDARDS)


class MatchResult(Base):
    """One evaluation of one profile version against one job's requirements."""

    __tablename__ = "match_results"

    __table_args__ = (
        # Idempotency (and the reason a repeated request is cheap): the same
        # candidate snapshot, judged against the same requirement snapshot, by
        # the same rules, is the same evaluation. Deriving it twice would
        # produce identical rows, so the database refuses the second one and
        # the service returns the first.
        UniqueConstraint(
            "profile_version_id",
            "job_requirements_id",
            "algorithm_version",
            name="uq_match_results_version_requirements_algorithm",
        ),
        # "My matches for this job", the listing endpoint's only query.
        Index("ix_match_results_user_id_job_id", "user_id", "job_id"),
        CheckConstraint(
            "char_length(algorithm_version) > 0",
            name="ck_match_results_algorithm_version_not_blank",
        ),
        # Counts are counts. Cheap to state, and they make a corrupt write
        # visible at the point it happens rather than in a chart six weeks on.
        CheckConstraint(
            "required_total >= 0 AND preferred_total >= 0",
            name="ck_match_results_totals_not_negative",
        ),
        CheckConstraint(
            "required_matched + required_missing + required_uncertain = required_total",
            name="ck_match_results_required_counts_sum",
        ),
        CheckConstraint(
            "preferred_matched + preferred_missing + preferred_uncertain = preferred_total",
            name="ck_match_results_preferred_counts_sum",
        ),
        CheckConstraint(
            "required_coverage BETWEEN 0 AND 1 "
            "AND preferred_coverage BETWEEN 0 AND 1 "
            "AND overall_score BETWEEN 0 AND 1",
            name="ck_match_results_scores_are_fractions",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Denormalized from profile_versions -> profiles -> user_id so that every
    # ownership check is one predicate on this table rather than a three-table
    # join. The service still resolves ownership from the profile side when
    # CREATING a match; this column is how reads stay scoped cheaply.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_match_results_user_id"),
        nullable=False,
    )

    # RESTRICT, not CASCADE: a match is a record of a judgment that was made.
    # Deleting the version it judged is a deliberate procedure, not a side
    # effect — the same posture every other per-user table in this schema takes.
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "profile_versions.id",
            ondelete="RESTRICT",
            name="fk_match_results_profile_version_id",
        ),
        nullable=False,
        index=True,
    )

    # CASCADE: a match is meaningless without the job it is about, and jobs are
    # user-owned rows a user may remove.
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE", name="fk_match_results_job_id"),
        nullable=False,
    )

    # The exact requirements row evaluated — see the module docstring. RESTRICT
    # because losing it would make the stored verdicts uninterpretable.
    job_requirements_id: Mapped[int] = mapped_column(
        ForeignKey(
            "job_requirements.id",
            ondelete="RESTRICT",
            name="fk_match_results_job_requirements_id",
        ),
        nullable=False,
        index=True,
    )

    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # The day "5+ years" was measured against. Without it the same inputs give
    # a different answer tomorrow, which is not reproducibility.
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)

    # --- the score, as components ---------------------------------------------
    #
    # Stored as columns, not derived on read and not folded into one number:
    # the counts ARE the explanation, and a UI that shows "3 matched, 2
    # uncertain" is more use than any percentage. See
    # app/services/matching/scoring.py for the formula and for why UNCERTAIN
    # counts toward the denominator but not the numerator.
    required_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_missing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_uncertain: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    preferred_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_missing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_uncertain: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    required_coverage: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    preferred_coverage: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    #: Coverage of structured job requirements by available profile evidence.
    #: NOT a probability of being hired, and nothing in this codebase may
    #: describe it as one.
    overall_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    requirement_results: Mapped[list["MatchRequirementResult"]] = relationship(
        back_populates="match_result",
        cascade="all, delete-orphan",
        order_by="MatchRequirementResult.id",
    )

    # Read-only, and here for one reason: `profile_version_id` is the database's
    # name for a version and `version_number` is the user's, and the match
    # result screen was printing the first under the second's label. On a
    # freshly truncated database the two agree, which is why every test and
    # every demo looked right; on a real profile with a dozen versions they do
    # not, and the screen told a user their match had been run against a version
    # that was not the one they confirmed.
    profile_version: Mapped["ProfileVersion"] = relationship(  # noqa: F821
        viewonly=True,
    )

    @property
    def profile_version_number(self) -> int | None:
        """Which version this judged, as the USER names it.

        A property rather than a column: the number already exists one join
        away, and a copy of it here would be a second thing to keep in step with
        no way to notice when it drifted.
        """
        version = self.profile_version
        return version.version_number if version is not None else None

    def __repr__(self) -> str:
        return (
            f"<MatchResult id={self.id} job={self.job_id} "
            f"version={self.profile_version_id} score={self.overall_score}>"
        )


class MatchRequirementResult(Base):
    """One job requirement, judged against one profile version."""

    __tablename__ = "match_requirement_results"

    __table_args__ = (
        # Stable identity for a requirement within a result: its category and
        # its position in that category's source list. Makes a re-run
        # comparable row-for-row against an earlier one.
        UniqueConstraint(
            "match_result_id",
            "category",
            "requirement_index",
            name="uq_match_requirement_results_result_category_index",
        ),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_match_requirement_results_status"),
        CheckConstraint(
            f"category IN ({_CATEGORY_SQL})", name="ck_match_requirement_results_category"
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_match_requirement_results_confidence"
        ),
        CheckConstraint(
            "requirement_index >= 0", name="ck_match_requirement_results_index_not_negative"
        ),
        CheckConstraint(
            "char_length(explanation) > 0",
            name="ck_match_requirement_results_explanation_present",
        ),
        CheckConstraint(
            f"evidence_standard IN ({_STANDARD_SQL})",
            name="ck_match_requirement_results_evidence_standard",
        ),
        # A verdict of MATCHED with nothing to point at is exactly the failure
        # mode this domain exists to prevent, but "has evidence" spans two
        # tables and cannot be a CHECK. It is enforced in
        # app/services/matching/engine.py and asserted in the test suite
        # instead; this comment is here so the absence looks deliberate.
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    match_result_id: Mapped[int] = mapped_column(
        ForeignKey(
            "match_results.id",
            ondelete="CASCADE",
            name="fk_match_requirement_results_match_result_id",
        ),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # The requirement as the posting expressed it, so a person reading the
    # result recognizes it. Never rewritten into our own words.
    requirement_label: Mapped[str] = mapped_column(String(255), nullable=False)
    # The canonical token it normalized to ("postgresql"), when the category
    # tokenizes at all. Null for experience and education, which are judged on
    # structure rather than on a single name. Queryable: "which canonical
    # skills are most often missing" is one GROUP BY over this column.
    requirement_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Kept separate from the verdict, never folded into it: a missing PREFERRED
    # requirement and a missing REQUIRED one are the same verdict about very
    # different facts.
    is_required: Mapped[bool] = mapped_column(nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)

    # Our own words, from a fixed vocabulary — never a model's prose, never an
    # exception's text, and never the job description itself.
    explanation: Mapped[str] = mapped_column(String(500), nullable=False)

    # What the POSTING demanded of the evidence, read from its own wording.
    # "Kubernetes" and "Kubernetes in production, not just in a course" set
    # different bars, and without this column a reader cannot tell why a listed
    # skill satisfied one requirement and not another — which is precisely the
    # question the real-data benchmark showed users are left with.
    evidence_standard: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=STANDARD_ANY_EVIDENCE
    )

    # The normalized profile-side concept that satisfied it, when one did:
    # "postgresql" for a Postgres skill matched against a PostgreSQL
    # requirement. Null for MISSING.
    matched_value: Mapped[str | None] = mapped_column(String(255), nullable=True)

    match_result: Mapped["MatchResult"] = relationship(back_populates="requirement_results")

    evidence: Mapped[list["MatchEvidence"]] = relationship(
        back_populates="requirement_result",
        cascade="all, delete-orphan",
        order_by="MatchEvidence.id",
    )

    def __repr__(self) -> str:
        return (
            f"<MatchRequirementResult {self.category}:{self.requirement_label!r} "
            f"{self.status} required={self.is_required}>"
        )


class MatchEvidence(Base):
    """A profile-version row that justifies a judgment.

    `source_id` is the primary key of the row in whichever profile-version
    child table `source_type` names. Deliberately NOT a foreign key: it is
    polymorphic across five tables, and the alternative — five nullable
    columns with a check constraint that exactly one is set — buys referential
    integrity at the cost of a schema nobody can read. The parent result's
    `profile_version_id` is what makes the id resolvable, and that IS a foreign
    key, held with RESTRICT.

    `label` and `detail` are denormalized copies of what the evidence said at
    match time. That is the point rather than a shortcut: they must still read
    correctly if the underlying profile version is ever deleted, and a match
    result that cannot explain itself without a join into live data is not a
    record of anything.
    """

    __tablename__ = "match_evidence"

    __table_args__ = (
        CheckConstraint(f"source_type IN ({_EVIDENCE_SQL})", name="ck_match_evidence_source_type"),
        CheckConstraint("char_length(label) > 0", name="ck_match_evidence_label_present"),
        CheckConstraint(
            f"tier IS NULL OR tier IN ({_TIER_SQL})", name="ck_match_evidence_tier"
        ),
        # Queryable on purpose: "show me every requirement where the profile
        # claimed the skill and the posting wanted more than a claim" is one
        # predicate, and it is the most actionable thing this table knows.
        Index("ix_match_evidence_tier", "tier"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    match_requirement_result_id: Mapped[int] = mapped_column(
        ForeignKey(
            "match_requirement_results.id",
            ondelete="CASCADE",
            name="fk_match_evidence_requirement_result_id",
        ),
        nullable=False,
        index=True,
    )

    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    label: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # How strong this item is: declared and corroborated, demonstrated in paid
    # work, declared only, or merely mentioned. Nullable because education and
    # certification evidence is not graded — there is no weaker or stronger way
    # to hold a degree, and inventing a tier for it would be noise.
    tier: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Whether THIS item met what the requirement asked for. False is the
    # interesting value: it is how a result explains an UNCERTAIN by showing
    # what was found and saying why it fell short, instead of showing nothing.
    qualifies: Mapped[bool] = mapped_column(nullable=False, server_default="true")

    # Why it qualifies, or why it does not. Our own words, from the fixed
    # vocabulary in app/services/matching/evidence.py.
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # The clause the token was actually found in, when the evidence came from
    # prose. This is the provenance guarantee applied to the profile side: a
    # reader can judge "Evaluated React Native, but the project was cancelled"
    # for themselves rather than trusting the tier we assigned it.
    quote: Mapped[str | None] = mapped_column(String(300), nullable=True)

    requirement_result: Mapped["MatchRequirementResult"] = relationship(back_populates="evidence")

    def __repr__(self) -> str:
        return f"<MatchEvidence {self.source_type}:{self.source_id} {self.label!r}>"
