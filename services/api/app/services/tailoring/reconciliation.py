"""Narrowing a model's document down to what the snapshot actually supports.

This module exists because of one difference between the two generators, and it
is worth stating plainly before any of the code below makes sense.

`DeterministicTailor` cannot fail grounding. It copies clauses out of the
snapshot and copies identity fields off the row, so a grounding error from it is
a bug in our own selection code — something we want to see, loudly, not
something we want quietly tidied away.

`ProviderTailor` fails grounding the way models fail: not wholesale, but in ones
and twos. Twenty-eight lines land verbatim and the twenty-ninth cites a sentence
that reads right and is not in the profile; nine identity fields are transcribed
and the tenth has had "Ltd" helpfully appended. Under the pipeline as it stood,
that document — twenty-eight good bullets and one bad citation — produced no
artifact at all. The user asked for a resume and got a page of findings.

So there is a step between "a model wrote something" and "here is the verdict",
and the whole design of it is one sentence:

    **It can only ever remove a claim, or restore a copied field to exactly what
    the snapshot says. It can never add one.**

Two narrow extensions of "restore", each a reorganisation of values the document
or the snapshot already holds and never a new word: experience entries are
permuted back into the profile's own order, and the skills under an invented
heading are refiled under the categories the profile stores them under.

That is not a description of intent, it is the property the tests assert
(`tests/test_tailoring_reconciliation.py`), because a repair stage that could
add is a second generator with no prompt and no review.

Two further decisions hold the rest of it up:

**It is driven by grounding's own findings, not by a second opinion.** Nothing
here re-decides whether a line is supported; it reads the ERROR findings
`grounding.validate` produced and addresses exactly those. A repairer with its
own idea of what "grounded" means is a second validator that will eventually
disagree with the first, and the disagreement would always resolve in the
document's favour, because this is the one that runs last.

**Grounding runs again afterwards, unchanged, and still decides.** This module
never returns a verdict, sets a status or touches a database. It returns a
narrower document; `pipeline._run_stages` validates that document from scratch,
and if it still fails, the run still completes with no artifact. An address this
module does not understand is therefore harmless: the finding survives
re-validation and the document is withheld, which is the direction a parser gap
has to fail in.

What comes back out is also a record. Every removal and every restoration
becomes a WARNING finding, stored beside the grounding ones, so the review
screen can say "two lines were removed because your profile does not support
them" rather than showing a document that quietly differs from what was written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import ProfileVersion
from app.models.tailoring import SEVERITY_WARNING
from app.schemas.tailored_resume import (
    TailoredContact,
    TailoredLine,
    TailoredResumeDocument,
    TailoredSkillGroup,
)
from app.services.tailoring import grounding
from app.services.tailoring.grounding import (
    ISSUE_CLAIM_LIMIT,
    CONTACT_FIELDS,
    UNCATEGORISED_SKILL_GROUP,
    Finding,
    GroundingReport,
    SnapshotIndex,
    _normalise,
    experience_ranks,
    index_snapshot,
    stored_titles,
    validate,
)

# --- the vocabulary of repairs ---------------------------------------------------
#
# Our own words, from a fixed set, in the same register as grounding's own
# reasons — these sit in the same list on the same screen, and a user reading
# them should not have to work out which module wrote which.

REASON_REMOVED_ENTRY = "This entry was removed: it names career data that is not in the selected profile version."
REASON_REMOVED_LINE = "This line was removed: your profile does not support what it said."
REASON_RESTORED_FIELD = "This was restored to exactly what your profile says."
REASON_REMOVED_SKILL = "This skill was removed: it is not listed in your profile."
REASON_REMOVED_TECHNOLOGY = "This technology was removed: it is not listed on that project in your profile."
REASON_REMOVED_LINK = "This link was removed: your profile does not hold it."
#: For a repeat, which the profile DOES support — telling the user "your profile
#: does not support what it said" about a sentence that is in their profile
#: would be false. NOTE: `evaluation/tailoring_repairs.ACTIONS` must map this to
#: "removed" (test_every_repair_reason_has_an_action enforces it).
REASON_REMOVED_DUPLICATE = "This was removed: the same thing already appears earlier on this resume."


#: The transcribed fields of each entry kind, and the attribute each is restored
#: from. The names are identical on both sides on purpose: the document field
#: `education[0].field_of_study` is restored from `Education.field_of_study`, and
#: a mapping that renamed anything would be somewhere for the two to drift.
_RESTORABLE: dict[str, frozenset[str]] = {
    "experience": frozenset(
        {
            "company",
            "job_title",
            "location",
            "start_date",
            "start_date_precision",
            "end_date",
            "end_date_precision",
        }
    ),
    "projects": frozenset({"name", "role"}),
    "education": frozenset(
        {
            "institution",
            "degree",
            "field_of_study",
            "start_date",
            "start_date_precision",
            "end_date",
            "end_date_precision",
        }
    ),
    "certifications": frozenset({"name", "issuing_organization", "issue_date"}),
}

#: The contact columns that are restored rather than dropped. `links` is not one
#: of them: a link the profile does not hold cannot be corrected into one it
#: does, so it goes.
_CONTACT_FIELDS = frozenset({"full_name", "email", "phone", "location"})

#: Splits a finding's `field_path` into the thing it points at.
#:
#: Every path grounding emits is built from a format string in that module, so
#: this pattern is the inverse of those few lines rather than a general-purpose
#: parser. The skills path is handled before this is reached, because its second
#: segment is a category name written by the generator and may contain anything.
_ADDRESS = re.compile(
    r"^(?P<section>[a-z_]+)"
    r"(?:\[(?P<index>\d+)\])?"
    r"(?:\.(?P<field>[a-z_]+))?"
    r"(?:\[(?P<subindex>\d+)\])?"
    r"(?:\.(?P<subfield>[a-z_]+))?$"
)

_SKILLS_PREFIX = "skills."


@dataclass
class _Plan:
    """What to remove and what to restore, addressed against the ORIGINAL indices.

    Collected in full before anything is rebuilt, because dropping the first
    bullet of a job renumbers the rest — and a plan applied as it is read would
    address the wrong line half the time.
    """

    drop_headline: bool = False
    #: The headline's text is not a stored job title. Restored to the title its
    #: own quote names when there is one; removed otherwise (it is optional).
    restore_headline: bool = False
    #: summary line position -> the reason it is removed.
    drop_summary: dict[int, str] = field(default_factory=dict)
    drop_links: set[int] = field(default_factory=set)
    restore_contact: set[str] = field(default_factory=set)
    #: The whole contact block was left out; rebuild it from the version.
    restore_contact_block: bool = False
    restore_link_labels: set[int] = field(default_factory=set)
    #: skill group category -> the exact names to remove from it.
    drop_skills: dict[str, set[str]] = field(default_factory=dict)
    #: (group position, skill position) of later copies of a skill already listed.
    drop_skill_repeats: set[tuple[int, int]] = field(default_factory=set)
    #: positions of skill groups whose HEADING the profile does not hold. Their
    #: skills are refiled under the categories the profile stores them under.
    regroup_skills: set[int] = field(default_factory=set)
    #: section -> entry position -> the reason it is removed: it names no row
    #: of this version, or it repeats an entry printed earlier.
    drop_entries: dict[str, dict[int, str]] = field(default_factory=dict)
    #: Put the experience entries back into the profile's own order.
    reorder_experience: bool = False
    #: section -> entry position -> bullet positions to remove.
    #: ...each bullet position mapped to the reason it is removed.
    drop_bullets: dict[str, dict[int, dict[int, str]]] = field(default_factory=dict)
    #: section -> entry position -> field names to restore from the snapshot.
    restore_fields: dict[str, dict[int, set[str]]] = field(default_factory=dict)
    #: project position -> the exact technology names to remove.
    drop_technologies: dict[int, set[str]] = field(default_factory=dict)

    def touched(self) -> bool:
        return any(
            (
                self.drop_headline,
                self.restore_headline,
                self.drop_summary,
                self.drop_links,
                self.restore_contact,
                self.restore_contact_block,
                self.restore_link_labels,
                self.drop_skills,
                self.drop_skill_repeats,
                self.regroup_skills,
                self.drop_entries,
                self.reorder_experience,
                self.drop_bullets,
                self.restore_fields,
                self.drop_technologies,
            )
        )


@dataclass(frozen=True)
class RepairResult:
    """A narrower document, what was done to it, and what could not be."""

    document: TailoredResumeDocument
    #: One WARNING per action taken. Stored as issues beside grounding's own.
    repairs: tuple[Finding, ...] = ()
    #: Errors this module has no action for. Not a failure — the document keeps
    #: them and re-validation reports them again — but a test asserts it is
    #: empty for every error grounding can raise, so a new check that grows a
    #: new address shape is caught there rather than in production.
    unrepairable: tuple[Finding, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.repairs)


def repair(
    document: TailoredResumeDocument,
    version: ProfileVersion,
    report: GroundingReport,
) -> RepairResult:
    """Remove what the snapshot does not support; restore what it states.

    Takes the report rather than re-deriving one, so that this module and the
    validator cannot hold different opinions: the errors addressed here are
    literally the errors that were raised there.

    WARNINGS are deliberately left alone. They do not withhold the artifact, so
    acting on one would be this module editing a document nobody objected to —
    and "this wording is new" is precisely the contribution a model is here for.
    """
    index = index_snapshot(version)
    plan, unrepairable = _plan(report.errors)

    if not plan.touched():
        return RepairResult(document=document, unrepairable=unrepairable)

    repaired, repairs = _apply(document, index, plan)
    return RepairResult(
        document=repaired,
        repairs=tuple(repairs),
        unrepairable=unrepairable,
    )


# --- reading the findings ---------------------------------------------------------


@dataclass(frozen=True)
class Settlement:
    """A document, the verdict on it, and what it took to get there."""

    document: TailoredResumeDocument
    report: GroundingReport
    #: The WARNING findings describing what was removed or restored. Empty
    #: unless a repair was both attempted AND rescued the document.
    repairs: tuple[Finding, ...] = ()
    #: Errors the first pass raised, kept for measurement: the difference
    #: between this and `report.errors` is what the repair was worth.
    errors_before: int = 0

    @property
    def repaired(self) -> bool:
        return bool(self.repairs)

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self.report.findings + self.repairs


def settle(
    document: TailoredResumeDocument,
    version: ProfileVersion,
    *,
    job_requirement_labels: frozenset[str] | None = None,
    repair_allowed: bool = True,
) -> Settlement:
    """Validate, repair if that is allowed, validate again, and decide.

    The whole sequence in one place because two callers need to agree on it
    exactly: `pipeline._run_stages`, which stores the result, and
    `evaluation/tailoring_runner.py`, which measures it. A measurement of a
    slightly different sequence from the one that runs in production is a
    measurement of nothing.

    `repair_allowed` is the caller's statement about which generator wrote this —
    the pipeline passes `run.generator == GENERATOR_AI` — rather than something
    inferred here, because this module has no business deciding whose output it
    is looking at.

    One rule decides the outcome: **a repair that does not produce a sendable
    document is not applied.** A run that is going to withhold the artifact
    anyway should show the user what the model actually wrote, not our edit of
    it; the stored document is the only record of that, and a half-repaired
    version of a document nobody can send explains neither what was written nor
    why it was refused.
    """
    report = validate(document, version, job_requirement_labels=job_requirement_labels)
    errors_before = len(report.errors)

    if report.passed or not repair_allowed:
        return Settlement(document=document, report=report, errors_before=errors_before)

    outcome = repair(document, version, report)
    if not outcome.changed:
        return Settlement(document=document, report=report, errors_before=errors_before)

    narrowed = validate(
        outcome.document, version, job_requirement_labels=job_requirement_labels
    )
    if not narrowed.passed:
        return Settlement(document=document, report=report, errors_before=errors_before)

    return Settlement(
        document=outcome.document,
        report=narrowed,
        repairs=outcome.repairs,
        errors_before=errors_before,
    )


def _plan(errors: tuple[Finding, ...]) -> tuple[_Plan, tuple[Finding, ...]]:
    plan = _Plan()
    unrepairable: list[Finding] = []

    for finding in errors:
        if not _record(plan, finding):
            unrepairable.append(finding)

    return plan, tuple(unrepairable)


def _drop_line(planned: dict[int, str], position: int, finding: Finding) -> None:
    """Plan one line's removal, and say truthfully why.

    A line removed ONLY for repeating an earlier one is supported by the
    profile, so the user is told it was a repeat. A line with any other error
    against it is removed as unsupported, whatever else was wrong with it: the
    stronger statement is the one that is true of every such line.
    """
    reason = (
        REASON_REMOVED_DUPLICATE
        if finding.reason == grounding.REASON_DUPLICATE_LINE
        else REASON_REMOVED_LINE
    )
    if planned.get(position) == REASON_REMOVED_LINE:
        return
    planned[position] = reason


def _record(plan: _Plan, finding: Finding) -> bool:
    """Turn one error into one planned action. False when there is none.

    Three findings have no action, by design, because each would need something
    ADDED: the empty document (a resume with no career cannot be repaired by
    removing something else from it), a job missing from the document, and a
    job left with no bullets although its row has text.
    """
    path = finding.field_path
    if not path:
        return False

    if path.startswith(_SKILLS_PREFIX):
        category = path[len(_SKILLS_PREFIX) :]
        if finding.claim is None:
            return False
        plan.drop_skills.setdefault(category, set()).add(finding.claim)
        return True

    match = _ADDRESS.match(path)
    if match is None:
        return False

    section = match.group("section")
    index = match.group("index")
    field_name = match.group("field")
    subindex = match.group("subindex")

    if section == "skills":
        # `skills[<n>].category`: a heading the profile does not use.
        # `skills[<n>].skills[<m>]`: a later copy of a skill already listed.
        # The name-level `skills.<category>` path was handled above.
        if index is not None and field_name == "category":
            plan.regroup_skills.add(int(index))
            return True
        if index is not None and field_name == "skills" and subindex is not None:
            plan.drop_skill_repeats.add((int(index), int(subindex)))
            return True
        return False

    if section == "headline":
        # `headline.text`: not a stored title, so restored from its own quote
        # when that quote IS one, else removed. Anything else (a bad citation,
        # an invented token): removed.
        if field_name == "text":
            plan.restore_headline = True
        else:
            plan.drop_headline = True
        return True

    if section == "summary" and index is not None:
        _drop_line(plan.drop_summary, int(index), finding)
        return True

    if section == "contact":
        if field_name is None and index is None:
            # `contact` alone: the block was left out although the version
            # holds contact columns. Restored from those columns.
            plan.restore_contact_block = True
            return True
        if field_name in _CONTACT_FIELDS:
            plan.restore_contact.add(field_name)
            return True
        if field_name == "links" and subindex is not None:
            # A url the profile does not hold is dropped; a label contradicting
            # the stored one is restored, because the link itself is the user's
            # and only its caption is wrong.
            if match.group("subfield") == "label":
                plan.restore_link_labels.add(int(subindex))
            else:
                plan.drop_links.add(int(subindex))
            return True
        return False

    if section == "experience" and index is None and field_name == "order":
        # A permutation of what is already there, into the profile's order.
        plan.reorder_experience = True
        return True

    # `experience` alone is a job MISSING from the document. It is left
    # unrepairable by design: the only repair would be to add the job back,
    # and this module never adds.
    if section not in _RESTORABLE or index is None:
        return False

    position = int(index)

    if field_name is None:
        # The entry itself: an id that resolves to nothing in this version, or
        # a later copy of an entry already printed.
        duplicate = finding.reason == grounding.REASON_DUPLICATE_ENTRY
        plan.drop_entries.setdefault(section, {})[position] = (
            REASON_REMOVED_DUPLICATE if duplicate else REASON_REMOVED_ENTRY
        )
        return True

    if field_name == "bullets" and subindex is None:
        # A job with no bullets although its row has text. Unrepairable by
        # design: the repair would be to write some.
        return False

    if field_name == "bullets" and subindex is not None:
        _drop_line(
            plan.drop_bullets.setdefault(section, {}).setdefault(position, {}),
            int(subindex),
            finding,
        )
        return True

    if section == "projects" and field_name == "technologies":
        if finding.claim is None:
            return False
        plan.drop_technologies.setdefault(position, set()).add(finding.claim)
        return True

    if field_name in _RESTORABLE[section]:
        plan.restore_fields.setdefault(section, {}).setdefault(position, set()).add(
            field_name
        )
        return True

    return False


# --- applying them ----------------------------------------------------------------


def _rows(index: SnapshotIndex, section: str) -> dict[int, object]:
    return {
        "experience": index.experiences,
        "projects": index.projects,
        "education": index.education,
        "certifications": index.certifications,
    }[section]


def _note(
    repairs: list[Finding],
    *,
    section: str,
    path: str | None,
    claim: str | None,
    reason: str,
) -> None:
    repairs.append(
        Finding(
            severity=SEVERITY_WARNING,
            section=section,
            field_path=path,
            claim=claim[:600] if claim else None,
            reason=reason,
        )
    )


def _apply(
    document: TailoredResumeDocument, index: SnapshotIndex, plan: _Plan
) -> tuple[TailoredResumeDocument, list[Finding]]:
    repairs: list[Finding] = []

    contact = _apply_contact(document.contact, index, plan, repairs)

    headline = _apply_headline(document.headline, index, plan, repairs)

    summary = []
    for position, line in enumerate(document.summary):
        if position in plan.drop_summary:
            _note(
                repairs,
                section="summary",
                path=f"summary[{position}]",
                claim=line.text,
                reason=plan.drop_summary[position],
            )
            continue
        summary.append(line)

    skills = _apply_skills(document.skills, index, plan, repairs)

    experience = _apply_entries(
        document.experience, index, plan, repairs, section="experience"
    )
    projects = _apply_entries(document.projects, index, plan, repairs, section="projects")
    education = _apply_entries(
        document.education, index, plan, repairs, section="education"
    )
    certifications = _apply_entries(
        document.certifications, index, plan, repairs, section="certifications"
    )

    repaired = document.model_copy(
        update={
            "contact": contact,
            "headline": headline,
            "summary": summary,
            "skills": skills,
            "experience": experience,
            "projects": projects,
            "education": education,
            "certifications": certifications,
        }
    )
    return repaired, repairs


def _apply_headline(
    headline: TailoredLine | None,
    index: SnapshotIndex,
    plan: _Plan,
    repairs: list[Finding],
) -> TailoredLine | None:
    """Restore a promoted headline from its own citation, or remove it.

    "Principal Backend Engineer" citing "Senior Backend Engineer": the quote is a
    title the profile holds, so the snapshot has the right answer and the
    headline becomes exactly that title, cited to the column it lives in. A
    headline whose quote is not a stored title has nothing to be restored FROM
    — choosing some other title for it would be this module writing the
    headline — so it goes, which costs nothing: the headline is optional and
    the job titles are all still on the page.
    """
    if headline is None:
        return None

    if plan.restore_headline and not plan.drop_headline:
        title = stored_titles(index).get(_normalise(headline.source_quote))
        if title is not None:
            _note(
                repairs,
                section="headline",
                path="headline",
                claim=headline.text,
                reason=REASON_RESTORED_FIELD,
            )
            return TailoredLine(text=title, source_quote=title, source_field="job_title")

    if plan.restore_headline or plan.drop_headline:
        _note(
            repairs,
            section="headline",
            path="headline",
            claim=headline.text,
            reason=REASON_REMOVED_LINE,
        )
        return None
    return headline


def _apply_contact(
    contact: TailoredContact | None,
    index: SnapshotIndex,
    plan: _Plan,
    repairs: list[Finding],
) -> TailoredContact | None:
    if contact is None:
        if not plan.restore_contact_block:
            return None
        # The four columns exactly as the version stores them. Links are NOT
        # restored: which of the version's links belong in a header is a
        # selection (personal ones only, deduplicated, capped), and this module
        # does not select — a header without links is incomplete, never false.
        _note(
            repairs,
            section="contact",
            path="contact",
            claim=None,
            reason=REASON_RESTORED_FIELD,
        )
        return TailoredContact(**{name: index.contact.get(name) for name in CONTACT_FIELDS})

    update: dict[str, object] = {}
    for name in sorted(plan.restore_contact):
        _note(
            repairs,
            section="contact",
            path=f"contact.{name}",
            claim=getattr(contact, name),
            reason=REASON_RESTORED_FIELD,
        )
        update[name] = index.contact.get(name)

    links = []
    for position, link in enumerate(contact.links):
        if position in plan.drop_links:
            _note(
                repairs,
                section="contact",
                path=f"contact.links[{position}]",
                claim=link.url,
                reason=REASON_REMOVED_LINK,
            )
            continue
        if position in plan.restore_link_labels:
            _note(
                repairs,
                section="contact",
                path=f"contact.links[{position}].label",
                claim=link.label,
                reason=REASON_RESTORED_FIELD,
            )
            link = link.model_copy(update={"label": index.links.get(link.url)})
        links.append(link)
    update["links"] = links

    return contact.model_copy(update=update)


def _apply_skills(
    groups: list[TailoredSkillGroup],
    index: SnapshotIndex,
    plan: _Plan,
    repairs: list[Finding],
) -> list[TailoredSkillGroup]:
    """Remove unlisted skills; refile the skills of an invented heading.

    A heading the profile does not use ("Expert, 10+ years, ex-Google") cannot
    be corrected into one it does — there is no stored heading it was a typo
    of. But the skills under it are the profile's, and dropping them to punish
    the heading would remove true things. So each goes back under the category
    the PROFILE files it under: merged into the document's own group of that
    heading when there is one, otherwise into a new group at the invented
    group's position. A skill the profile stores without a category (and a
    project technology, which never has one) goes under "Other", the neutral
    heading the deterministic generator uses. Every heading this writes is a
    stored category or one of the two fixed ones, so this is a reorganisation
    of profile values and never a new word.
    """
    # Pass 1: removals, and the skills set aside from invented headings. A
    # `None` slot marks where an invented group stood, so a new group created
    # for its skills lands there rather than at the end of the section.
    kept_groups: list[TailoredSkillGroup | None] = []
    refile: list[tuple[int, str]] = []
    for position, group in enumerate(groups):
        unlisted = plan.drop_skills.get(group.category, set())
        kept = []
        for skill_position, name in enumerate(group.skills):
            if _as_claimed(name) in unlisted:
                _note(
                    repairs,
                    section="skills",
                    path=f"skills.{group.category}",
                    claim=name,
                    reason=REASON_REMOVED_SKILL,
                )
                continue
            if (position, skill_position) in plan.drop_skill_repeats:
                _note(
                    repairs,
                    section="skills",
                    path=f"skills[{position}].skills[{skill_position}]",
                    claim=name,
                    reason=REASON_REMOVED_DUPLICATE,
                )
                continue
            kept.append(name)
        removed = len(kept) != len(group.skills)

        if position in plan.regroup_skills:
            _note(
                repairs,
                section="skills",
                path=f"skills[{position}].category",
                claim=group.category,
                reason=REASON_RESTORED_FIELD,
            )
            refile.extend((len(kept_groups), name) for name in kept)
            kept_groups.append(None)
            continue

        if not removed:
            kept_groups.append(group)
        # An emptied group is dropped rather than printed as a bare heading.
        elif kept:
            kept_groups.append(group.model_copy(update={"skills": kept}))

    if not refile:
        return [group for group in kept_groups if group is not None]

    # Pass 2: refile. Slots keep the section's order; a slot holds the group
    # that stood there, or the groups created for an invented heading's skills.
    slots: list[list[TailoredSkillGroup]] = [
        [group] if group is not None else [] for group in kept_groups
    ]
    for slot_position, name in refile:
        heading = _stored_heading(index, name)
        target = _find_group(slots, heading)
        if target is None:
            slots[slot_position].append(TailoredSkillGroup(category=heading, skills=[name]))
            continue
        slot, offset = target
        group = slot[offset]
        if _normalise(name) in {_normalise(existing) for existing in group.skills}:
            # Already printed under its own heading: refiling it again would
            # print it twice. Nothing is lost — the skill stays on the page.
            continue
        slot[offset] = group.model_copy(update={"skills": [*group.skills, name]})

    return [group for slot in slots for group in slot]


def _as_claimed(name: str) -> str:
    """A name as it appears in a finding's claim: truncated to the column.

    Skills and technologies are planned for removal BY the name a finding
    carries, and `Finding` truncates its claim at construction. Comparing the
    document's name under the same truncation is what keeps an over-long name
    removable; comparing it whole would leave it in place, re-validation would
    raise it again, and a one-bad-skill document would be withheld.
    """
    return name[:ISSUE_CLAIM_LIMIT]


def _stored_heading(index: SnapshotIndex, name: str) -> str:
    """The heading the PROFILE files this skill under, exactly as stored."""
    category = index.skill_categories.get(_normalise(name))
    if category and category.strip():
        return category
    return UNCATEGORISED_SKILL_GROUP


def _find_group(
    slots: list[list[TailoredSkillGroup]], heading: str
) -> tuple[list[TailoredSkillGroup], int] | None:
    wanted = _normalise(heading)
    for slot in slots:
        for offset, group in enumerate(slot):
            if _normalise(group.category) == wanted:
                return slot, offset
    return None


def _apply_entries(
    entries: list,
    index: SnapshotIndex,
    plan: _Plan,
    repairs: list[Finding],
    *,
    section: str,
) -> list:
    rows = _rows(index, section)
    dropped = plan.drop_entries.get(section, {})
    bullets_by_entry = plan.drop_bullets.get(section, {})
    fields_by_entry = plan.restore_fields.get(section, {})

    out = []
    for position, entry in enumerate(entries):
        path = f"{section}[{position}]"
        row = rows.get(entry.source_id)

        if position in dropped or row is None:
            # `row is None` is the same fact the plan already holds; it is
            # re-checked because everything below restores FROM that row, and a
            # restore with nothing to restore from is how a repair invents.
            _note(
                repairs,
                section=section,
                path=path,
                claim=_entry_label(entry),
                reason=dropped.get(position, REASON_REMOVED_ENTRY),
            )
            continue

        update: dict[str, object] = {}

        for name in sorted(fields_by_entry.get(position, set())):
            _note(
                repairs,
                section=section,
                path=f"{path}.{name}",
                claim=_as_claim(getattr(entry, name, None)),
                reason=REASON_RESTORED_FIELD,
            )
            update[name] = getattr(row, name, None)

        if section == "projects":
            unlisted = plan.drop_technologies.get(position, set())
            if unlisted:
                kept_technologies = []
                for technology in entry.technologies:
                    if _as_claimed(technology) in unlisted:
                        _note(
                            repairs,
                            section=section,
                            path=f"{path}.technologies",
                            claim=technology,
                            reason=REASON_REMOVED_TECHNOLOGY,
                        )
                        continue
                    kept_technologies.append(technology)
                update["technologies"] = kept_technologies

        drop = bullets_by_entry.get(position, {})
        if drop:
            kept_bullets = []
            for bullet_position, bullet in enumerate(entry.bullets):
                if bullet_position in drop:
                    _note(
                        repairs,
                        section=section,
                        path=f"{path}.bullets[{bullet_position}]",
                        claim=bullet.text,
                        reason=drop[bullet_position],
                    )
                    continue
                kept_bullets.append(bullet)
            update["bullets"] = kept_bullets

        out.append(entry.model_copy(update=update) if update else entry)

    if section == "experience" and plan.reorder_experience:
        out = _in_profile_order(out, index, repairs)

    return out


def _in_profile_order(entries: list, index: SnapshotIndex, repairs: list[Finding]) -> list:
    """The same entries, sorted into the profile's order. A permutation only.

    Sorted by `grounding.experience_ranks`, the same ranking the validator
    judged the order by, so the two cannot disagree about what "in order"
    means. The sort is stable, so jobs that share a rank keep the document's
    order between them. Every entry reaching here resolved to a row — the ones
    that did not were removed above — so every one has a rank.
    """
    ranks = experience_ranks(index)
    ordered = sorted(entries, key=lambda entry: ranks[entry.source_id])
    if ordered != entries:
        _note(
            repairs,
            section="experience",
            path="experience.order",
            claim=", ".join(entry.company for entry in entries),
            reason=REASON_RESTORED_FIELD,
        )
    return ordered


def _entry_label(entry) -> str | None:
    """What to call an entry in a finding. Never more than the entry named."""
    for attribute in ("company", "name", "institution"):
        value = getattr(entry, attribute, None)
        if value:
            return value
    return None


def _as_claim(value) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


__all__ = [
    "REASON_REMOVED_DUPLICATE",
    "REASON_REMOVED_ENTRY",
    "REASON_REMOVED_LINE",
    "REASON_REMOVED_LINK",
    "REASON_REMOVED_SKILL",
    "REASON_REMOVED_TECHNOLOGY",
    "REASON_RESTORED_FIELD",
    "RepairResult",
    "Settlement",
    "repair",
    "settle",
]
