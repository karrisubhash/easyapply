"""Extraction, OCR routing, and the structuring boundary."""

import re
from pathlib import Path

import pytest

from app.schemas.resume_candidate import Provenance, ResumeCandidate, CandidateSkill
from app.services.resume.extraction import (
    METHOD_OCR,
    METHOD_TEXT,
    ExtractedDocument,
    ExtractedLink,
    ExtractedPage,
    ExtractionError,
    extract,
    extract_docx,
    extract_pdf,
    pages_needing_ocr,
)
from evaluation.datasets.resume_profile_v1.resume_source import build_resume_pdf
from app.services.resume.ocr import OcrError, UnavailableOcrEngine, apply_ocr
from app.services.resume.structuring import (
    DeterministicResumeStructurer,
    parse_candidate_date,
    verify_provenance,
)
from app.services.resume.validation import DOCX_MEDIA_TYPE, PDF_MEDIA_TYPE
from tests.resume_fixtures import (
    make_docx,
    make_docx_with_hyperlink,
    make_image_only_pdf,
    make_pdf,
    make_resume_docx,
    make_resume_pdf,
)


#: The document the "Git-based" defect was found on. A real person's resume, so
#: it is not checked in; the test that uses it says so when it is absent.
REAL_RESUME = (
    Path(__file__).resolve().parents[1]
    / "var"
    / "resume-storage"
    / "resumes"
    / "40"
    / "2ca78284771231c97b8b4c6662bd96685a35e6abf40923d8a15bcb57ede8a3a1.pdf"
)


class FakeOcrEngine:
    """Stands in for Tesseract, which is not installed on every machine."""

    name = "fake"

    def __init__(self, text: str = "OCR RECOVERED TEXT", available: bool = True):
        self._text = text
        self._available = available
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    def image_to_text(self, image: bytes) -> str:
        self.calls += 1
        return self._text


class ExplodingOcrEngine:
    name = "exploding"

    def is_available(self) -> bool:
        return True

    def image_to_text(self, image: bytes) -> str:
        raise OcrError("ocr_failed", "The OCR engine could not read the page.")


# --- PDF ---------------------------------------------------------------------


def test_pdf_extraction_preserves_page_boundaries():
    document = extract(make_resume_pdf(), media_type=PDF_MEDIA_TYPE)

    assert document.page_count == 2
    assert [page.page_number for page in document.pages] == [1, 2]
    assert "EXPERIENCE" in document.pages[0].text
    assert "EDUCATION" in document.pages[1].text
    assert document.methods == {METHOD_TEXT}


def test_pdf_text_is_addressable_by_page_for_grounding():
    document = extract(make_resume_pdf(), media_type=PDF_MEDIA_TYPE)
    combined = document.text()

    assert '<page number="1">' in combined
    assert '<page number="2">' in combined


def test_an_empty_page_is_kept_so_numbering_stays_honest():
    document = extract_pdf(make_pdf(["First page", "", "Third page"]))

    assert document.page_count == 3
    assert document.pages[1].char_count == 0
    assert "Third page" in document.pages[2].text


def test_a_malformed_pdf_fails_cleanly():
    with pytest.raises(ExtractionError) as caught:
        extract_pdf(b"%PDF-1.4\nthis is not really a pdf")
    assert caught.value.code in ("malformed_pdf", "empty_document")


# --- DOCX --------------------------------------------------------------------


def test_docx_extraction_preserves_structure_and_order():
    document = extract(make_resume_docx(), media_type=DOCX_MEDIA_TYPE)
    page = document.pages[0]
    kinds = [block["type"] for block in page.metadata["blocks"]]

    assert kinds.index("heading") < kinds.index("table")
    assert "## EXPERIENCE" in page.text
    assert "Python | Advanced" in page.text


def test_docx_does_not_invent_page_boundaries():
    """Pagination is a Word rendering decision, not a property of the file."""
    document = extract(make_resume_docx(), media_type=DOCX_MEDIA_TYPE)

    assert document.page_count == 1
    assert "no page boundaries" in document.pages[0].metadata["note"]


def test_an_empty_docx_extracts_to_nothing_rather_than_failing():
    document = extract_docx(make_docx(headings_and_paragraphs=[]))
    assert document.total_chars == 0


def test_a_malformed_docx_fails_cleanly():
    with pytest.raises(ExtractionError) as caught:
        extract_docx(b"PK\x03\x04 truncated garbage")
    assert caught.value.code == "malformed_docx"


# --- links -------------------------------------------------------------------


def test_pdf_hyperlinks_are_extracted_with_the_words_they_cover():
    """The text says "GitHub"; the address is in an annotation somewhere else.

    Reading only the text layer loses every URL on the document, and no later
    stage can put one back — which is how a resume's LinkedIn, GitHub and every
    project repository were dropped before the AI was ever asked about them.
    """
    document = extract(build_resume_pdf(), media_type=PDF_MEDIA_TYPE)
    links = {link.url: link.label for link in document.pages[0].links}

    assert "https://github.com/alexmorgan-example" in links
    assert links["https://github.com/alexmorgan-example"] == "GitHub"
    assert links["https://alexmorgan.example.com"] == "Portfolio"


def test_docx_hyperlinks_are_extracted():
    """Word puts the words in the run and the URL in the part's relationships."""
    data = make_docx_with_hyperlink(
        text="Portfolio", url="https://alexmorgan.example.com"
    )
    document = extract(data, media_type=DOCX_MEDIA_TYPE)

    assert [(link.label, link.url) for link in document.pages[0].links] == [
        ("Portfolio", "https://alexmorgan.example.com")
    ]


def test_links_do_not_count_towards_the_text_a_page_has():
    """A scanned page with one link annotation still has no readable text.

    char_count decides whether a page goes to OCR, so counting link URLs as text
    would let exactly the pages that need OCR skip it.
    """
    page = ExtractedPage(
        page_number=1,
        text="",
        method=METHOD_TEXT,
        links=[ExtractedLink(url="https://example.com/a-very-long-url-indeed")],
    )
    assert page.char_count == 0
    assert pages_needing_ocr(ExtractedDocument(pages=[page]), min_chars=80) == [1]


def test_links_are_shown_to_the_structuring_step():
    page = ExtractedPage(
        page_number=1,
        text="CONTACT",
        method=METHOD_TEXT,
        links=[ExtractedLink(url="https://example.com", label="Portfolio")],
    )
    assert "Portfolio <https://example.com>" in page.text_with_links()
    assert "https://example.com" in ExtractedDocument(pages=[page]).text()


def test_a_citation_quoting_a_link_verifies():
    """Links are part of the page's evidence, so quoting one is not a fabrication."""
    page = ExtractedPage(
        page_number=1,
        text="CONTACT",
        method=METHOD_TEXT,
        links=[ExtractedLink(url="https://example.com", label="Portfolio")],
    )
    candidate = ResumeCandidate(
        skills=[
            CandidateSkill(
                name="Portfolio",
                provenance=[Provenance(page=1, quote="https://example.com", confidence=0.5)],
            )
        ]
    )

    verify_provenance(candidate, ExtractedDocument(pages=[page]))
    assert candidate.skills[0].provenance[0].verified is True


# --- text quality ------------------------------------------------------------


def test_words_keep_their_spaces():
    """A PDF stores glyphs at coordinates, never words, so spacing is inferred.

    Getting that inference wrong is silent and unrecoverable downstream: welded
    words ("Languages:Python") become one wrong skill, and a heading split in
    half ("Educa tion") stops matching and drops its whole section.
    """
    document = extract(build_resume_pdf(), media_type=PDF_MEDIA_TYPE)
    text = document.pages[0].text

    assert "Languages: Python, Go, SQL, TypeScript" in text
    assert "\nEducation\n" in text
    assert not re.search(r"[A-Za-z]{25,}", text)


def test_line_endings_are_normalised():
    """PDFium returns CRLF and python-docx returns LF; downstream sees neither."""
    document = extract(build_resume_pdf(), media_type=PDF_MEDIA_TYPE)
    assert "\r" not in document.pages[0].text


def test_control_characters_become_spaces_rather_than_joining_words():
    """A font whose ToUnicode maps a hyphen to an unassigned slot yields \\x02.

    Deleting it welds the words either side together; a space keeps them apart.
    """
    document = extract_pdf(make_pdf(["Git\x02based control"]))
    assert "Git based control" in document.pages[0].text


# --- recovering a hyphen PDFium could not decode ------------------------------
#
# These drive the real `extract_pdf` and the real pypdf decoder, with only
# PDFium's contribution stubbed. PDFium's failure cannot be reproduced from a
# hand-built PDF: it needs an embedded subset font whose glyph carries no usable
# ToUnicode entry, and writing one would mean checking in a font binary. What is
# stubbed is therefore exactly the one thing that cannot be built — what PDFium
# returned — and everything downstream of it is the production code path.


def _as_pdfium(monkeypatch, *pages: str):
    """Make `extract_pdf` believe PDFium returned this text for each page."""

    def fake_layer(data, *, annotations):
        return list(pages), [[] for _ in annotations], [[] for _ in annotations], "pypdfium2"

    monkeypatch.setattr(
        "app.services.resume.extraction._pdf_text_layer", fake_layer
    )


SENTENCE = "deployment using Git-based version control."


def test_a_hyphen_pdfium_could_not_decode_is_restored_from_pypdf(monkeypatch):
    """The real defect: the page draws "Git-based", the profile said "Git based".

    A subset TeX font left PDFium with no mapping for a drawn hyphen, so it
    emitted a raw control code and `_clean` turned it into a space — silently
    changing a word of the user's resume. pypdf resolves the same glyph through
    the font's /Differences array, and is already open for this document's link
    annotations, so the evidence costs nothing.
    """
    data = make_pdf([SENTENCE])
    _as_pdfium(monkeypatch, SENTENCE.replace("-", "\x02"))

    text = extract_pdf(data).pages[0].text

    assert "Git-based version control." in text
    assert "Git based" not in text


def test_only_the_undecodable_character_changes(monkeypatch):
    """The repair may not touch anything else on the page."""
    other = "Reduced latency by 40% across 1,000+ well-formed lines."
    data = make_pdf([f"{SENTENCE}\n{other}"])
    _as_pdfium(monkeypatch, f"{SENTENCE.replace('-', chr(2))}\n{other}")

    text = extract_pdf(data).pages[0].text

    assert text.strip() == f"{SENTENCE}\n{other}"


def test_a_control_character_pypdf_cannot_decode_either_stays_a_space(monkeypatch):
    """No second opinion, so the conservative reading stands."""
    data = make_pdf(["Git\x02based control"])
    _as_pdfium(monkeypatch, "Git\x02based control")

    text = extract_pdf(data).pages[0].text

    assert "Git based control" in text
    assert "\x02" not in text


def test_a_control_character_corroborated_as_a_letter_is_not_restored(monkeypatch):
    """Only a hyphen is ever put back.

    pypdf reading an ordinary letter there is a disagreement between decoders,
    not permission to rewrite the word.
    """
    data = make_pdf(["Gitxbased control"])
    _as_pdfium(monkeypatch, "Git\x02based control")

    text = extract_pdf(data).pages[0].text

    assert "Git based control" in text
    assert "Gitxbased" not in text


def test_an_ambiguous_corroboration_is_not_evidence(monkeypatch):
    """The same neighbourhood on one page, answered two ways, decides nothing.

    Corroboration is per page, so both readings have to sit on the same page to
    be in genuine conflict.
    """
    data = make_pdf(["alpha-omega here\nalpha+omega here"])
    _as_pdfium(monkeypatch, "alpha\x02omega here\nalpha+omega here")

    text = extract_pdf(data).pages[0].text

    assert "alpha omega here" in text
    assert "alpha-omega here" not in text


def test_hyphens_that_decode_normally_are_left_alone(monkeypatch):
    """Nothing happens to a page PDFium read correctly in the first place."""
    line = "A 3-engineer team shipped the real-time, end-to-end pipeline."
    data = make_pdf([line])
    _as_pdfium(monkeypatch, line)

    assert extract_pdf(data).pages[0].text.strip() == line


def test_the_pypdf_fallback_is_not_asked_to_corroborate_itself(monkeypatch):
    """When pypdf IS the source there is no independent decoder left to ask.

    Repairing there would be pypdf agreeing with pypdf, which is not evidence.
    """

    def no_pdfium(data, *, annotations):
        return [None], [[] for _ in annotations], [[] for _ in annotations], "pypdf"

    monkeypatch.setattr(
        "app.services.resume.extraction._pdf_text_layer", no_pdfium
    )

    text = extract_pdf(make_pdf(["Git\x02based control"])).pages[0].text
    assert "Git based control" in text


@pytest.mark.skipif(
    not REAL_RESUME.exists(),
    reason=f"the real resume this defect was found on is not present at {REAL_RESUME}",
)
def test_the_real_resume_keeps_its_hyphen():
    """The document this bug was actually found on, read end to end.

    Machine-local: it is a real person's resume and is not checked in. It runs
    where the document exists and says plainly why it does not run elsewhere.
    """
    document = extract(REAL_RESUME.read_bytes(), media_type=PDF_MEDIA_TYPE)
    text = document.pages[0].text

    assert "using Git-based version control." in text
    assert "Git based" not in text
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text)


# --- OCR routing -------------------------------------------------------------


def test_a_text_pdf_does_not_need_ocr():
    document = extract_pdf(make_resume_pdf())
    assert pages_needing_ocr(document, min_chars=80) == []


def test_an_image_only_pdf_needs_ocr_on_every_page():
    document = extract_pdf(make_image_only_pdf(2))
    assert pages_needing_ocr(document, min_chars=80) == [1, 2]


def test_ocr_replaces_only_the_pages_that_needed_it():
    """A scanned first page and a real second page must not both be re-read."""
    document = ExtractedDocument(
        pages=[
            ExtractedPage(page_number=1, text="", method=METHOD_TEXT),
            ExtractedPage(page_number=2, text="plenty of real text here", method=METHOD_TEXT),
        ]
    )
    engine = FakeOcrEngine()

    result = apply_ocr(
        document, source=make_image_only_pdf(2), page_numbers=[1], engine=engine
    )

    assert result.pages[0].method == METHOD_OCR
    assert result.pages[0].text == "OCR RECOVERED TEXT"
    assert result.pages[1].method == METHOD_TEXT
    assert result.pages[1].text == "plenty of real text here"
    assert engine.calls == 1


def test_ocr_is_refused_when_no_engine_is_available():
    document = extract_pdf(make_image_only_pdf(1))

    with pytest.raises(OcrError) as caught:
        apply_ocr(document, source=b"", page_numbers=[1], engine=UnavailableOcrEngine())
    assert caught.value.code == "ocr_unavailable"


def test_an_ocr_engine_failure_surfaces_as_an_ocr_error():
    document = extract_pdf(make_image_only_pdf(1))

    with pytest.raises(OcrError):
        apply_ocr(
            document, source=make_image_only_pdf(1), page_numbers=[1], engine=ExplodingOcrEngine()
        )


# --- date precision ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_value", "expected_precision"),
    [
        ("May 2024", "2024-05-01", "month"),
        ("Aug 2020", "2020-08-01", "month"),
        ("2024", "2024-01-01", "year"),
        ("September 2019", "2019-09-01", "month"),
    ],
)
def test_a_date_keeps_the_precision_the_resume_used(text, expected_value, expected_precision):
    """'May 2024' must never become a confident 1 May 2024."""
    parsed = parse_candidate_date(text)

    assert parsed is not None
    assert parsed.raw == text
    assert parsed.value.isoformat() == expected_value
    assert parsed.precision == expected_precision


def test_an_unparseable_date_keeps_its_words_and_claims_nothing():
    parsed = parse_candidate_date("sometime last summer")

    assert parsed.raw == "sometime last summer"
    assert parsed.value is None
    assert parsed.precision is None


# --- structuring -------------------------------------------------------------


def test_the_deterministic_structurer_splits_skill_lists():
    document = extract_pdf(make_resume_pdf())
    candidate = DeterministicResumeStructurer().structure(document).candidate

    names = {skill.name for skill in candidate.skills}
    assert {"Python", "Django", "PostgreSQL"} <= names


def test_the_structurer_never_invents_a_grade():
    """The fixture resume states no grade, so no grade may appear."""
    document = extract_pdf(make_resume_pdf())
    candidate = DeterministicResumeStructurer().structure(document).candidate

    assert all(entry.grade is None for entry in candidate.education)


def test_every_extracted_item_cites_a_page():
    document = extract_pdf(make_resume_pdf())
    candidate = DeterministicResumeStructurer().structure(document).candidate

    assert candidate.skills
    for skill in candidate.skills:
        assert skill.provenance
        assert all(entry.page >= 1 for entry in skill.provenance)


def test_provenance_is_verified_against_the_page_it_cites():
    document = extract_pdf(make_resume_pdf())
    candidate = DeterministicResumeStructurer().structure(document).candidate

    verified = verify_provenance(candidate, document)
    assert all(entry.verified for skill in verified.skills for entry in skill.provenance)


def test_a_fabricated_quote_is_marked_unverified():
    """The check doubles as a hallucination detector."""
    document = ExtractedDocument(
        pages=[ExtractedPage(page_number=1, text="Real text only", method=METHOD_TEXT)]
    )
    candidate = ResumeCandidate(
        skills=[
            CandidateSkill(
                name="Fabricated",
                provenance=[Provenance(page=1, quote="text that is not there", confidence=0.9)],
            )
        ]
    )

    verified = verify_provenance(candidate, document)
    assert verified.skills[0].provenance[0].verified is False


def test_a_citation_to_a_page_that_does_not_exist_is_unverified():
    document = ExtractedDocument(
        pages=[ExtractedPage(page_number=1, text="Page one", method=METHOD_TEXT)]
    )
    candidate = ResumeCandidate(
        skills=[
            CandidateSkill(
                name="Ghost",
                provenance=[Provenance(page=9, quote="Page one", confidence=0.9)],
            )
        ]
    )

    assert verify_provenance(candidate, document).skills[0].provenance[0].verified is False
