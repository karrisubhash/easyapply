"""How skills and links are shown to the model (`generation._profile_block`, v3).

The run2 evaluation's defect, pinned so it cannot come back. v2 printed a skill
as "Python (Languages)" and a link as "url (LinkedIn)"; the model copied both
verbatim, and grounding — rightly comparing bare names and stored labels —
removed 88 valid skills and restored 3 labels across 9 documents. The property
held here is the contract between the two modules: **every value the block
shows the model is, copied exactly, a value grounding accepts.**

No database: the frozen `tailoring_v1` profile and hand-built unsaved rows are
enough, because `_profile_block` touches nothing but the version it is handed.
"""

from __future__ import annotations

import re

from app.models import ProfileVersion
from app.models.profile_link import OWNER_PERSONAL, ProfileLink
from app.models.skill import Skill
from app.services.tailoring import grounding
from app.services.tailoring.generation import PROMPT_VERSION_AI, SYSTEM_PROMPT, _profile_block
from evaluation.datasets.tailoring_v1.inputs import confirmed_version

_SECTION_HEADERS = ("CONTACT", "SUMMARY", "EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION", "CERTIFICATIONS")


def section(block: str, name: str) -> list[str]:
    """The lines of one section of the block, header excluded."""
    lines = block.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(name))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith(_SECTION_HEADERS)),
        len(lines),
    )
    return lines[start + 1 : end]


def parse_skills(block: str) -> tuple[list[str], dict[str, list[str]]]:
    """Read the SKILLS section back: uncategorised names, then category -> names."""
    uncategorised: list[str] = []
    grouped: dict[str, list[str]] = {}
    current: str | None = None
    for line in section(block, "SKILLS"):
        # The '- ' prefix decides, as the header says: a skill named "Docs:" is
        # still a skill, not a heading.
        if match := re.fullmatch(r"  - (.+)", line):
            assert current is None, "an uncategorised skill after a heading would read as that category's"
            uncategorised.append(match[1])
        elif match := re.fullmatch(r"    - (.+)", line):
            assert current is not None
            grouped[current].append(match[1])
        elif match := re.fullmatch(r"  ([^- ].*):", line):
            current = match[1]
            grouped[current] = []
        else:
            raise AssertionError(f"unexpected SKILLS line: {line!r}")
    return uncategorised, grouped


def parse_links(block: str) -> list[tuple[str, str | None]]:
    links: list[tuple[str, str | None]] = []
    for line in section(block, "CONTACT"):
        if match := re.fullmatch(r"      - url: (.+)", line):
            links.append((match[1], None))
        elif match := re.fullmatch(r"        label: (.+)", line):
            url, _ = links[-1]
            links[-1] = (url, match[1])
    return links


def expected_links(version) -> list[tuple[str, str | None]]:
    # An empty label is no label, as grounding's own comparison treats it.
    return [(link.url, link.label or None) for link in version.links]


def expected_groups(version) -> tuple[list[str], dict[str, list[str]]]:
    uncategorised = [row.name for row in version.skills if not row.category]
    grouped: dict[str, list[str]] = {}
    for row in version.skills:
        if row.category:
            grouped.setdefault(row.category, []).append(row.name)
    return uncategorised, grouped


def hand_built_version() -> ProfileVersion:
    """Row order deliberately unsorted; categories None and '', labels None and '';
    a skill whose own name ends in ':'."""
    version = ProfileVersion(
        id=9, profile_id=1, version_number=1, status="confirmed",
        created_by_type="user", is_current=True, full_name="Sam Example",
    )
    version.skills = [
        Skill(id=1, profile_version_id=9, name="Python", category="Languages"),
        Skill(id=2, profile_version_id=9, name="Go (Golang)", category="Languages"),
        Skill(id=3, profile_version_id=9, name="Pairing", category=None),
        Skill(id=4, profile_version_id=9, name="PostgreSQL", category="Databases"),
        Skill(id=5, profile_version_id=9, name="Django", category="Backend"),
        Skill(id=6, profile_version_id=9, name="SQL", category="Languages"),
        Skill(id=7, profile_version_id=9, name="Code review", category=""),
        Skill(id=8, profile_version_id=9, name="Docs:", category="Backend"),
    ]
    version.links = [
        ProfileLink(id=1, profile_version_id=9, url="https://example.com/sam", label=None, owner_type=OWNER_PERSONAL),
        ProfileLink(id=2, profile_version_id=9, url="https://github.com/sam", label="GitHub", owner_type=OWNER_PERSONAL),
        ProfileLink(id=3, profile_version_id=9, url="https://sam.example.org", label="", owner_type=OWNER_PERSONAL),
    ]
    return version


# --- A. a skill is shown as its bare name -------------------------------------------


def test_a_skill_is_shown_as_its_bare_name_never_with_its_category():
    version = confirmed_version()
    block = _profile_block(version)

    assert "    - Python" in block.splitlines()
    assert "Python (Languages)" not in block
    for row in version.skills:
        assert f"{row.name} ({row.category})" not in block, row.name


def test_every_skill_shown_is_one_grounding_accepts_as_written():
    """Copying a displayed skill verbatim must ground — the whole point of v3."""
    version = confirmed_version()
    uncategorised, grouped = parse_skills(_profile_block(version))
    shown = uncategorised + [name for names in grouped.values() for name in names]
    accepted = grounding.index_snapshot(version).skill_names

    assert shown
    for name in shown:
        assert grounding._normalise(name) in accepted, name


# --- B. categories are structure ---------------------------------------------------


def test_the_category_is_a_heading_above_its_skills():
    block = _profile_block(confirmed_version())
    lines = block.splitlines()

    heading = lines.index("  Languages:")
    group = []
    for line in lines[heading + 1 :]:
        if not line.startswith("    - "):
            break
        group.append(line)
    assert "    - Python" in group


# --- C. a link's label is separate from its url ------------------------------------


def test_a_link_is_shown_as_separate_url_and_label_fields():
    version = confirmed_version()
    block = _profile_block(version)

    for link in version.links:
        assert f"{link.url} (" not in block
        assert f"({link.label})" not in block
        assert f"      - url: {link.url}" in block.splitlines()
        assert f"        label: {link.label}" in block.splitlines()


def test_every_link_shown_is_one_grounding_accepts_as_written():
    version = confirmed_version()
    stored = grounding.index_snapshot(version).links
    shown = parse_links(_profile_block(version))

    assert len(shown) == len(version.links)  # not vacuous: every link was found as fields
    for url, label in shown:
        assert url in stored, url
        assert label == stored[url], url


# --- D. nothing is lost ------------------------------------------------------------


def test_every_skill_category_link_and_label_survives_the_rendering():
    for version in (confirmed_version(), hand_built_version()):
        block = _profile_block(version)
        assert parse_skills(block) == expected_groups(version)
        assert parse_links(block) == expected_links(version)


def test_an_uncategorised_skill_gets_no_invented_heading_and_an_unlabelled_link_no_label():
    block = _profile_block(hand_built_version())
    uncategorised, grouped = parse_skills(block)

    # Category None and '' alike: listed before any heading, never under a blank one.
    assert uncategorised == ["Pairing", "Code review"]
    assert set(grouped) == {"Languages", "Databases", "Backend"}
    assert "  :" not in block.splitlines()
    # Label None and '' alike: no label line at all.
    links = parse_links(block)
    assert ("https://example.com/sam", None) in links
    assert ("https://sam.example.org", None) in links
    for invented in ("Other:", "Uncategorised:", "label: None", "label: \n"):
        assert invented not in block + "\n"


def test_a_skill_whose_own_name_ends_in_a_colon_is_still_a_skill():
    _, grouped = parse_skills(_profile_block(hand_built_version()))
    assert "Docs:" in grouped["Backend"]
    assert "Docs" not in grouped


def test_a_skill_whose_own_name_has_parentheses_is_shown_unchanged():
    uncategorised, grouped = parse_skills(_profile_block(hand_built_version()))
    assert "Go (Golang)" in grouped["Languages"]


# --- E. deterministic --------------------------------------------------------------


def test_the_block_is_deterministic_and_keeps_row_order():
    first = _profile_block(hand_built_version())
    assert first == _profile_block(hand_built_version())
    assert _profile_block(confirmed_version()) == _profile_block(confirmed_version())

    _, grouped = parse_skills(first)
    # Categories in the order they first appear; names in their row order.
    assert list(grouped) == ["Languages", "Databases", "Backend"]
    assert grouped["Languages"] == ["Python", "Go (Golang)", "SQL"]


# --- the instructions match the representation -------------------------------------


def test_the_instructions_describe_the_new_representation():
    flattened = " ".join(SYSTEM_PROMPT.split())
    assert "the whole name, nothing added" in flattened
    assert "never add one to a skill name" in flattened
    assert "`url:`" in flattened and "`label:`" in flattened
    assert "never put one inside the other" in flattened


def test_the_prompt_version_records_the_change():
    """Stored on every run; a document must not be read against an older prompt.

    v4 left this block alone and changed the requirement list; see
    `test_tailoring_requirements_block.py`. v5 left it alone too — the SKILLS
    representation above is exactly v3's — and aligned the skills RULE with
    grounding; see `test_tailoring_ai.py`.
    """
    assert PROMPT_VERSION_AI == "tailoring_ai_v6"
