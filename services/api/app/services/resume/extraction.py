"""Turning an uploaded document into normalized, page-addressed text.

This stage produces text and nothing else. It does not interpret, classify or
guess — a resume is still just pages of characters when it leaves here. Keeping
raw extraction separate from AI interpretation is what makes it possible to
re-run structuring later, against a different model or a better prompt, without
re-reading the original file.

Page boundaries are preserved because provenance depends on them: an AI-extracted
field is only checkable if we can say which page it came from.
"""

import logging
import re
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Any

from app.services.resume.validation import DOCX_MEDIA_TYPE, PDF_MEDIA_TYPE

logger = logging.getLogger(__name__)

METHOD_TEXT = "text"
METHOD_OCR = "ocr"

#: C0 controls that survive some font encodings — a hyphen or ligature whose
#: ToUnicode map points at an unassigned slot arrives as \x02 rather than as the
#: character it was drawn as. They become spaces rather than being deleted:
#: deleting one welds the words either side of it together ("Gitbased"), and a
#: wrong word is harder to recover from than a wrong space.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


#: The only character this module will restore from a control code, and only on
#: a second decoder's evidence. Deliberately not "whatever the other decoder
#: said": two extractors disagree about all sorts of things, and a general
#: substitution would let any of those disagreements rewrite the document.
_HYPHENS = frozenset("-‐‑‒–—")

#: How much surrounding text must line up before corroboration is believed,
#: in characters either side, whitespace removed. Shorter contexts match in too
#: many places on a page; longer ones stop matching as soon as the two decoders
#: disagree about anything nearby.
_CONTEXT = 4


def _clean(text: str) -> str:
    """Normalize a page's text to one canonical form.

    Line endings first: PDFium returns CRLF and python-docx returns LF, and a
    page whose line endings depend on which extractor ran is a page every
    downstream matcher has to be defensive about. \\r is removed here rather than
    being caught by the control-character rule below, which would turn each one
    into a stray space.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_CHARS.sub(" ", text)


def _recover_hyphens(text: str, raw_page) -> str:
    """Put back a drawn hyphen that PDFium could not decode, on a second opinion.

    A subset TeX font can carry a glyph with no usable ToUnicode entry. PDFium
    then emits the raw character code, so a page that visibly reads
    "using Git-based version control" arrives as "using Git\\x02based version
    control" — and `_clean` turns that into "Git based", quietly changing a word
    of the user's resume. pypdf resolves the same glyph through the font's
    /Differences array and reads it correctly as "-".

    So this asks pypdf, which is already open for this document's link
    annotations, what sits between the same neighbouring characters. A control
    code is replaced ONLY when pypdf independently and unambiguously says a
    hyphen is there. No match, an ambiguous match, or any other character leaves
    the control code alone for `_clean` to turn into a space — the conservative
    reading, which this function narrows rather than widens.

    Whitespace is stripped from both sides before comparing because the two
    decoders disagree about line breaks: pypdf keeps the hyphen at the end of
    one line and "based" at the start of the next, while PDFium joins them.
    """
    if not _CONTROL_CHARS.search(text):
        return text

    try:
        reference = re.sub(r"\s+", "", raw_page.extract_text() or "")
    except Exception as exc:
        # No second opinion available is not an error; it is the status quo.
        logger.warning("could not read a corroborating decode: %s", exc)
        return text
    if not reference:
        return text

    def corroborated(match: re.Match) -> str:
        index = match.start()
        left = re.sub(r"\s+", "", text[max(0, index - _CONTEXT * 2) : index])[-_CONTEXT:]
        right = re.sub(r"\s+", "", text[index + 1 : index + 1 + _CONTEXT * 2])[:_CONTEXT]
        # Too little context to identify the spot is no evidence at all.
        if len(left) < 2 or len(right) < 2:
            return match.group(0)

        pattern = f"{re.escape(left)}(.){re.escape(right)}"
        candidates = {found.group(1) for found in re.finditer(pattern, reference)}
        # Exactly one answer, and that answer is a hyphen. Two different
        # characters in two places is the page failing to identify itself.
        if len(candidates) == 1:
            (character,) = candidates
            if character in _HYPHENS:
                return character
        return match.group(0)

    return _CONTROL_CHARS.sub(corroborated, text)


class ExtractionError(RuntimeError):
    """The document could not be read at all."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExtractedLink:
    """A hyperlink the document carries as an annotation rather than as text.

    A resume that renders "Github" as a word linked to a URL puts the word in
    the text layer and the URL nowhere near it — the URL lives in the page's
    /Annots. Extracting only the text layer therefore loses every link on the
    document, which is why these are carried separately rather than being left
    to the structuring step to guess at.
    """

    url: str
    #: The text the link was drawn over, when it can be located. "Github".
    label: str | None = None
    #: Every word of this page's OWN text that reads before this link, in
    #: document order — never interpreted here (this module "does not
    #: interpret, classify or guess", per its own docstring), only measured, so
    #: the structuring step can decide whether a heading came before this link
    #: without reaching back into a PDF or a DOCX itself.
    #:
    #: This is what a personal LinkedIn/GitHub/portfolio link in the header is
    #: told apart from a Demo/GitHub link drawn next to one particular project
    #: further down the SAME page: both are `page.links` entries with no
    #: section of their own, and a caption alone cannot tell them apart — three
    #: projects can each draw a link captioned "GitHub". Position can, because
    #: a resume reads top to bottom and a heading it has already passed is a
    #: heading it is now under.
    #:
    #: None when the position could not be measured — PDFium unavailable, or an
    #: annotation with no usable rectangle. The caller's answer to "I don't
    #: know" is to treat the link as if it were still in the header, which is
    #: the behaviour every link had before this field existed.
    preceding_text: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "label": self.label}


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    method: str
    metadata: dict[str, Any] = field(default_factory=dict)
    links: list[ExtractedLink] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        """Length of the text layer alone.

        Deliberately excludes `links`: this count decides whether a page needs
        OCR, and a scanned page whose only machine-readable content is a link
        annotation still has no readable text.
        """
        return len(self.text.strip())

    def text_with_links(self) -> str:
        """The page as the structuring step should see it.

        Links are appended under a marker rather than being spliced in beside
        the word they were drawn over: the position is a guess, the existence
        of the link is not.
        """
        if not self.links:
            return self.text
        rendered = "\n".join(
            f"{link.label} <{link.url}>" if link.label else f"<{link.url}>"
            for link in self.links
        )
        return f"{self.text}\n[links]\n{rendered}"


@dataclass(frozen=True)
class ExtractedDocument:
    """The normalized extraction result: a document is a sequence of pages."""

    pages: list[ExtractedPage]
    #: Pages past `MAX_PDF_PAGES` that were never opened. Not emitted as empty
    #: pages — three thousand blank pages would each be sent to OCR — but
    #: counted, so the review screen can say the document was cut short.
    pages_not_read: int = 0

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def total_chars(self) -> int:
        return sum(page.char_count for page in self.pages)

    @property
    def methods(self) -> set[str]:
        return {page.method for page in self.pages}

    def text(self) -> str:
        """The whole document, with page markers the AI layer can cite.

        The page text is the uploader's, and it sits inside the prompt's own
        fences. A resume containing "</page>" — or "</resume>", the fence the
        provider structurer wraps the whole document in — could otherwise close
        the fence early and have whatever follows read as something other than
        the document: a forged page number to cite, or text posing as the
        instruction around it. Such a tag is neutralised by turning its "<"
        into "‹", which a model reads as the same word and no fence matches.
        """
        return "\n\n".join(
            f"<page number=\"{page.page_number}\">\n"
            f"{_neutralise_fences(page.text_with_links().strip())}\n</page>"
            for page in self.pages
        )


#: The fence names the structuring prompt uses around document text. An
#: opening or closing tag of either, however spaced or cased, inside a page.
_FENCE_TAG = re.compile(r"<(\s*/?\s*)(page|resume)\b", re.IGNORECASE)


def _neutralise_fences(text: str) -> str:
    return _FENCE_TAG.sub(lambda match: f"‹{match.group(1)}{match.group(2)}", text)


#: What `_neutralise_fences` leaves behind.
_NEUTRALISED_TAG = re.compile(r"‹(\s*/?\s*)(page|resume)\b", re.IGNORECASE)


def restore_fences(text: str) -> str:
    """The inverse of `_neutralise_fences`, for text a model copied OUT of the
    prompt.

    The model is told to quote the page exactly, and the page it is shown has
    "‹Page />" where the resume says "<Page />" (a front-end developer's
    resume says that often). A faithful quote then failed provenance — checked
    against the document's own text — and "‹", a character the resume never
    had, was stored in the draft. Restoring on the way out keeps the fence
    closed on the way in: the model still never sees a tag that could end it.
    """
    return _NEUTRALISED_TAG.sub(lambda match: f"<{match.group(1)}{match.group(2)}", text)


def extract(data: bytes, *, media_type: str) -> ExtractedDocument:
    """Extract text from an already-validated upload."""
    if media_type == PDF_MEDIA_TYPE:
        return extract_pdf(data)
    if media_type == DOCX_MEDIA_TYPE:
        return extract_docx(data)
    raise ExtractionError("unsupported_media_type", f"Cannot extract {media_type}.")


# --- PDF ---------------------------------------------------------------------

#: The most pages of a PDF this service reads. A resume is one to three pages
#: and a long academic CV perhaps fifteen; a PDF of three thousand blank pages
#: (376 KB, well inside the upload limit) used to be read in full and then
#: sent, every page of it, to OCR. Pages past this are counted and reported,
#: never opened.
MAX_PDF_PAGES = 20

_ENCRYPTION_WE_CANNOT_OPEN = (
    "The PDF uses encryption this service cannot open. Save or print it to a "
    "new PDF without protection, and upload that."
)


def _page_tree(reader) -> list:
    """Every page the document's page tree holds, found by walking it.

    Never the tree's declared /Count, which is what pypdf's `len(reader.pages)`
    returns for an encrypted document — the owner-password PDFs this module now
    opens. A /Count that understated the tree dropped pages without a word (a
    three-page resume declaring one was read as one page, with nothing
    reported as unread); one that overstated it raised IndexError, an
    unexpected_error retried to exhaustion. `get_page` flattens the whole tree,
    encrypted or not, the first time it is asked for a page.
    """
    if reader.flattened_pages is None:
        try:
            reader.get_page(0)
        except IndexError:
            return []
    return list(reader.flattened_pages or [])


def extract_pdf(data: bytes) -> ExtractedDocument:
    """Per-page text from a text-based PDF.

    Pages that yield nothing are still emitted, as empty pages. Dropping them
    would renumber everything after, and a page number that does not match the
    document a human is looking at is worse than an empty page.

    Text comes from PDFium, with pypdf as the fallback. A PDF does not store
    words — it stores glyphs at coordinates — so every extractor infers spaces
    from the gaps between them, and pypdf's inference is measurably wrong on
    ordinary resume typography: on the fixture in evaluation/datasets it both
    welds words together ("Languages:Python", and a whole summary paragraph with
    no spaces at all) and splits them apart ("Educa tion", which stops the
    section heading matching and silently drops the section). Both failures are
    invisible downstream — they look like a resume that simply did not say those
    things. PDFium reads the same page correctly. pypdf is still what reads the
    annotations, where its object model is the better tool.
    """
    from pypdf import PasswordType, PdfReader
    from pypdf.errors import (
        DependencyError,
        FileNotDecryptedError,
        LimitReachedError,
        PyPdfError,
    )

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            # Encrypted is not the same as password protected. A PDF with only
            # an OWNER password — "no printing", "no copying" — opens in every
            # viewer without asking for anything, because its user password is
            # empty; refusing it refused a resume anyone could read. So the
            # empty password is tried first, and only a document it does not
            # open is the legitimate document we simply cannot read.
            if reader.decrypt("") == PasswordType.NOT_DECRYPTED:
                raise ExtractionError(
                    "document_encrypted",
                    "The PDF is password protected and cannot be read.",
                )
        all_pages = _page_tree(reader)
        page_total = len(all_pages)
        raw_pages = all_pages[:MAX_PDF_PAGES]
    except ExtractionError:
        raise
    except DependencyError as exc:
        # AES encryption, which pypdf can only undo with the `cryptography`
        # package — not a dependency of this service. It used to escape as an
        # unexpected_error and be retried to exhaustion, three attempts to reach
        # the same missing library. It is permanent, and the user can act on it.
        raise ExtractionError(
            "document_encrypted", _ENCRYPTION_WE_CANNOT_OPEN
        ) from exc
    except NotImplementedError as exc:
        # The same failure by another route: a certificate (/Adobe.PubSec)
        # handler, or a /V or /R pypdf does not implement, raises this rather
        # than DependencyError — and escaped as an unexpected_error retried to
        # exhaustion. Outside encryption pypdf raises it for a filter it does
        # not support: permanent too, and not the user's password.
        if b"/Encrypt" in data:
            raise ExtractionError("document_encrypted", _ENCRYPTION_WE_CANNOT_OPEN) from exc
        raise ExtractionError("malformed_pdf", "The PDF could not be read.") from exc
    except FileNotDecryptedError as exc:
        raise ExtractionError(
            "document_encrypted",
            "The PDF is password protected and cannot be read.",
        ) from exc
    except LimitReachedError as exc:
        # pypdf's own decompression bound tripped: a stream that inflates past
        # anything a resume holds.
        raise ExtractionError(
            "document_too_large",
            "The document unpacks to far more data than a resume contains, so "
            "it was not read. Save it again, or export it as a new PDF, and "
            "upload that.",
        ) from exc
    except (
        PyPdfError, ValueError, OSError, KeyError, IndexError, TypeError, AttributeError, RecursionError
    ) as exc:
        # PyPdfError is pypdf's base class, so a parse failure it has not named
        # yet is still "malformed", never an unexpected_error retried three
        # times. The others are what pypdf raises from inside a broken object
        # graph (a /Kids that is an integer, a page tree that points at itself).
        raise ExtractionError("malformed_pdf", "The PDF could not be read.") from exc

    if not raw_pages:
        raise ExtractionError("empty_document", "The PDF has no pages.")

    # The URL and the rectangle come from pypdf's object model; the words under
    # that rectangle come from PDFium, which is the only one of the two that can
    # answer "what text is inside this box".
    annotations = [_pdf_link_annotations(page) for page in raw_pages]
    page_texts, labels, preceding_texts, extractor = _pdf_text_layer(
        data, annotations=annotations
    )

    pages: list[ExtractedPage] = []
    for index, raw_page in enumerate(raw_pages, start=1):
        text = page_texts[index - 1]
        if text is None:
            try:
                text = raw_page.extract_text() or ""
            except Exception as exc:  # one unreadable page must not lose the rest
                logger.warning("pdf page %s failed to extract: %s", index, exc)
                text = ""
        else:
            # Only when PDFium produced the text. On the branch above pypdf IS
            # the source, so there is no independent decoder left to ask.
            text = _recover_hyphens(text, raw_page)

        links = [
            ExtractedLink(url=annotation.url, label=label, preceding_text=preceding)
            for annotation, label, preceding in zip(
                annotations[index - 1], labels[index - 1], preceding_texts[index - 1]
            )
        ]
        pages.append(
            ExtractedPage(
                page_number=index,
                text=_clean(text),
                method=METHOD_TEXT,
                metadata={"extractor": extractor},
                links=links,
            )
        )

    return ExtractedDocument(pages=pages, pages_not_read=page_total - len(pages))


@dataclass(frozen=True)
class _LinkAnnotation:
    url: str
    #: (left, bottom, right, top) in page space, or None when unusable.
    rect: tuple[float, float, float, float] | None


def _pdf_text_layer(
    data: bytes, *, annotations: list[list[_LinkAnnotation]]
) -> tuple[list[str | None], list[list[str | None]], list[list[str | None]], str]:
    """Every page's text, link labels and link positions, from PDFium.

    A None text entry means "this page has no PDFium text"; the caller falls
    back to pypdf for exactly those pages rather than for the whole document, so
    one bad page does not cost the rest their spacing.
    """
    page_count = len(annotations)
    empty_per_link = [[None] * len(page) for page in annotations]
    try:
        import pypdfium2 as pdfium
    except ImportError:  # pragma: no cover - pypdfium2 ships as a dependency
        logger.warning("pypdfium2 is unavailable; falling back to pypdf text")
        return [None] * page_count, empty_per_link, empty_per_link, "pypdf"

    document = None
    try:
        document = pdfium.PdfDocument(data)
        texts: list[str | None] = []
        labels: list[list[str | None]] = []
        preceding: list[list[str | None]] = []
        for index in range(min(len(document), page_count)):
            try:
                page = document[index]
                textpage = page.get_textpage()
                texts.append(textpage.get_text_bounded())
                labels.append(
                    [_link_label(textpage, item.rect) for item in annotations[index]]
                )
                preceding.append(
                    [_preceding_text(textpage, item.rect) for item in annotations[index]]
                )
            except Exception as exc:
                logger.warning("pdfium page %s failed to extract: %s", index + 1, exc)
                texts.append(None)
                labels.append([None] * len(annotations[index]))
                preceding.append([None] * len(annotations[index]))
        texts.extend([None] * (page_count - len(texts)))
        labels.extend(empty_per_link[len(labels) :])
        preceding.extend(empty_per_link[len(preceding) :])
        return texts, labels, preceding, "pypdfium2"
    except Exception as exc:
        logger.warning("pdfium could not open the document: %s", exc)
        return [None] * page_count, empty_per_link, empty_per_link, "pypdf"
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:  # pragma: no cover - closing is best effort
                pass


def _pdf_link_annotations(raw_page) -> list[_LinkAnnotation]:
    """The URI annotations on one page, with the box each was drawn in.

    Failure here is never fatal: a document with unreadable annotations is still
    a document whose text we have.
    """
    try:
        annotations = raw_page.get("/Annots") or []
    except Exception:
        return []

    found: list[_LinkAnnotation] = []
    seen: set[str] = set()
    for annotation in annotations:
        try:
            obj = annotation.get_object()
            action = obj.get("/A")
            if not action or "/URI" not in action:
                continue
            url = str(action["/URI"]).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            found.append(_LinkAnnotation(url=url, rect=_annotation_rect(obj)))
        except Exception:
            continue
    return found


def _annotation_rect(annotation) -> tuple[float, float, float, float] | None:
    try:
        rect = annotation.get("/Rect")
        if not rect or len(rect) != 4:
            return None
        x0, y0, x1, y1 = (float(value) for value in rect)
    except Exception:
        return None

    left, right = min(x0, x1), max(x0, x1)
    bottom, top = min(y0, y1), max(y0, y1)
    if right - left <= 0 or top - bottom <= 0:
        return None
    return left, bottom, right, top


def _link_label(textpage, rect: tuple[float, float, float, float] | None) -> str | None:
    """The words inside a link's rectangle.

    Returns None rather than guessing when the rectangle is missing or covers no
    text — a labelled link is nicer, an unlabelled one is still the URL, and a
    label taken from the wrong place would misdescribe where the link goes.
    """
    if rect is None:
        return None
    left, bottom, right, top = rect
    try:
        label = textpage.get_text_bounded(
            left=left, bottom=bottom, right=right, top=top
        )
    except Exception:
        return None
    label = re.sub(r"\s+", " ", _clean(label or "")).strip()
    return label[:100] or None


def _preceding_text(textpage, rect: tuple[float, float, float, float] | None) -> str | None:
    """Every word of the page's own text that reads before this link's rectangle.

    Bounded only by `bottom`: `left`, `right` and `top` are left at None, which
    `get_text_bounded` resolves to the page's own edges, so this is "the whole
    width of the page, from this link's own top edge up to the top of the
    page" — everything visually above it, in the same top-to-bottom reading
    order PDFium already uses for the page's plain text. A single-column
    resume's reading order and its vertical order are the same thing, which is
    the same assumption `text_with_links()`'s caller already makes about the
    page as a whole.

    Returns None on the same terms `_link_label` does: no rectangle, no answer.
    """
    if rect is None:
        return None
    _, _, _, top = rect
    try:
        text = textpage.get_text_bounded(bottom=top)
    except Exception:
        return None
    return _clean(text or "")


# --- DOCX --------------------------------------------------------------------


#: Bounds on what a DOCX may unpack to, checked against the ZIP's own central
#: directory BEFORE anything is decompressed or parsed.
#:
#: A resume's Word file unpacks to a few megabytes — its XML and a photo or two.
#: Nothing checked this before: validation reads only the central directory, and
#: python-docx then inflated every part it was pointed at, so a few kilobytes of
#: upload could ask a worker for gigabytes. `zipfile` never inflates an entry
#: past the size the directory declares for it, so bounding the declared sizes
#: bounds the real ones.
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
#: Unpacked-to-packed, over the whole archive. Word XML compresses around ten
#: to one; a hundred to one on anything sizeable is a bomb, not a resume.
MAX_DOCX_COMPRESSION_RATIO = 100
#: Below this many unpacked bytes the ratio is not checked: a tiny document of
#: repeated whitespace can compress absurdly well and still harm nothing.
_DOCX_RATIO_FLOOR_BYTES = 1024 * 1024
#: A Word document has a few dozen parts. Tens of thousands is not one.
MAX_DOCX_ENTRIES = 2000

_DOCX_TOO_LARGE = ExtractionError(
    "document_too_large",
    "The document unpacks to far more data than a resume contains, so it was "
    "not opened. Save it again, or export it as a PDF, and upload that.",
)


def _check_docx_expansion(data: bytes) -> None:
    """Refuse a DOCX whose declared contents exceed what a resume could be."""
    import zipfile

    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ExtractionError("malformed_docx", "The DOCX could not be read.") from exc

    unpacked = sum(max(entry.file_size, 0) for entry in entries)
    packed = sum(max(entry.compress_size, 0) for entry in entries)
    if len(entries) > MAX_DOCX_ENTRIES or unpacked > MAX_DOCX_UNCOMPRESSED_BYTES:
        raise ExtractionError(_DOCX_TOO_LARGE.code, _DOCX_TOO_LARGE.message)
    if unpacked > _DOCX_RATIO_FLOOR_BYTES and unpacked > MAX_DOCX_COMPRESSION_RATIO * max(packed, 1):
        raise ExtractionError(_DOCX_TOO_LARGE.code, _DOCX_TOO_LARGE.message)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W_P = f"{_W}p"
_W_TBL = f"{_W}tbl"
_W_TR = f"{_W}tr"
_W_TC = f"{_W}tc"
_W_SDT = f"{_W}sdt"
_W_SDT_CONTENT = f"{_W}sdtContent"
_W_CUSTOM_XML = f"{_W}customXml"
_W_TXBX_CONTENT = f"{_W}txbxContent"
_W_HYPERLINK = f"{_W}hyperlink"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

#: The run content that is visible text, and what each renders as. The same
#: mapping python-docx's `Run.text` uses, applied to EVERY run in a paragraph
#: rather than only its direct children — `Paragraph.text` reads `w:r` and
#: `w:hyperlink` children only, so a word inside an inline content control or
#: a tracked insertion was simply not there.
_TEXT_NODES = {
    f"{_W}t": None,
    f"{_W}tab": "\t",
    f"{_W}ptab": "\t",
    f"{_W}br": "\n",
    f"{_W}cr": "\n",
    f"{_W}noBreakHyphen": "-",
}

#: Subtrees a paragraph's own text must not read through. A text box is its own
#: block (read after the paragraph that anchors it); an `mc:Fallback` is a
#: second rendering of the `mc:Choice` beside it, so reading both would print
#: every text box twice; `w:moveFrom` is the old position of moved text.
_OPAQUE = frozenset({_W_TXBX_CONTENT, _MC_FALLBACK, f"{_W}moveFrom"})


def _under(node, stop, tags: frozenset[str]) -> bool:
    """Whether an ancestor of `node`, strictly below `stop`, has one of `tags`."""
    parent = node.getparent()
    while parent is not None and parent is not stop:
        if parent.tag in tags:
            return True
        parent = parent.getparent()
    return False


def _paragraph_text(p) -> str:
    return "".join(
        (node.text or "") if _TEXT_NODES[node.tag] is None else _TEXT_NODES[node.tag]
        for node in p.iter(*_TEXT_NODES)
        if not _under(node, p, _OPAQUE)
    )


def _text_boxes(p) -> list:
    """The text boxes anchored in this paragraph, once each, outermost only."""
    return [
        box
        for box in p.iter(_W_TXBX_CONTENT)
        if not _under(box, p, _OPAQUE)
    ]


def _unwrap(container, wanted: frozenset[str]):
    """`container`'s children with one of the `wanted` tags, looking through the
    content controls (`w:sdt`) and custom-XML wrappers a template puts around
    them. Word templates wrap whole paragraphs, rows and cells in content
    controls; iterating direct children only skipped every one of them."""
    for child in container.iterchildren():
        if child.tag in wanted:
            yield child
        elif child.tag == _W_SDT:
            content = child.find(_W_SDT_CONTENT)
            if content is not None:
                yield from _unwrap(content, wanted)
        elif child.tag == _W_CUSTOM_XML:
            yield from _unwrap(child, wanted)


_BLOCKS = frozenset({_W_P, _W_TBL})

#: Page-metadata key: how many of the page's leading lines came from Word page
#: headers and footers. Measured, not interpreted, like `ExtractedLink`'s
#: `preceding_text`: the structurer uses it to find the first line of the
#: BODY — where a resume puts the candidate's name — even when a template's
#: header ("email | phone | city") is read before it.
FURNITURE_LINES_KEY = "furniture_lines"


def _headers_and_footers(document) -> list[tuple[str, Any]]:
    """Every distinct page header, then every distinct page footer.

    Read FIRST because that is where a Word template puts the contact block —
    name, email, phone, LinkedIn — and where nothing read it: the extractor
    walked the body only, so the whole contact record of such a resume was
    missing without a trace. First, so the structurer sees it as the block
    above the first heading, which is what a contact block is; footers too,
    for the same reason, since a footer is a contact line as often as a page
    number.

    A header linked to the previous section's has no content of its own and is
    skipped; one part shared by several sections is read once. A first-page or
    even-page header is read only when the document actually shows it.
    """
    try:
        odd_even = bool(document.settings.odd_and_even_pages_header_footer)
    except Exception:
        odd_even = False

    seen: set[str] = set()
    headers: list[tuple[str, Any]] = []
    footers: list[tuple[str, Any]] = []
    for section in document.sections:
        try:
            first_page = bool(section.different_first_page_header_footer)
        except Exception:
            first_page = False
        for kind, attribute, shown, bucket in (
            ("header", "first_page_header", first_page, headers),
            ("header", "header", True, headers),
            ("header", "even_page_header", odd_even, headers),
            ("footer", "first_page_footer", first_page, footers),
            ("footer", "footer", True, footers),
            ("footer", "even_page_footer", odd_even, footers),
        ):
            if not shown:
                continue
            try:
                part_of_page = getattr(section, attribute)
                if part_of_page.is_linked_to_previous:
                    continue
                key = str(part_of_page.part.partname)
            except Exception as exc:
                # A header reference whose relationship is missing or broken:
                # python-docx raises on `.part`. That header cannot be read;
                # the others, and the body, still can.
                logger.warning("docx %s could not be opened: %s", kind, type(exc).__name__)
                continue
            if key in seen:
                continue
            seen.add(key)
            bucket.append((kind, part_of_page))
    return headers + footers


def extract_docx(data: bytes) -> ExtractedDocument:
    """Text from a DOCX, keeping the structure that carries meaning.

    A DOCX has no pages — pagination is a rendering decision made by Word, not
    a property of the file — so this emits a single page rather than inventing
    boundaries. Headings, paragraphs and table rows are kept in document order,
    with headings marked, because "EDUCATION" being a heading is a real signal
    for the structuring step.

    Read, in this order: page headers and footers (see `_headers_and_footers`),
    then the body. Within either, content controls are looked through, a text
    box is read straight after the paragraph that anchors it, a nested table
    straight after the row that holds it, and hyperlinks are collected from
    table cells and text boxes as well as body paragraphs. A Word template uses
    all of these, and each one used to be dropped without a word: the file
    "worked" and the resume simply came out shorter.

    A merged cell is read once. `row.cells` returns one cell per grid column,
    so a cell spanning three columns came back three times; this reads the
    row's own `w:tc` elements, one per cell as drawn.
    """
    _check_docx_expansion(data)

    import docx
    from docx.document import Document as DocxDocument
    from docx.text.paragraph import Paragraph

    try:
        document: DocxDocument = docx.Document(BytesIO(data))
    except Exception as exc:
        raise ExtractionError("malformed_docx", "The DOCX could not be read.") from exc

    blocks: list[dict[str, Any]] = []
    lines: list[str] = []
    links: list[ExtractedLink] = []

    def block_record(kind: str, **fields: Any) -> dict[str, Any]:
        return fields if kind == "body" else {**fields, "part": kind}

    def read_paragraph(p, parent, kind: str) -> None:
        paragraph = Paragraph(p, parent)
        text = _paragraph_text(p).strip()
        if text:
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            is_heading = style.lower().startswith("heading") or style == "Title"
            blocks.append(
                block_record(kind, type="heading" if is_heading else "paragraph", style=style)
            )
            # Everything the document has said SO FAR — before this
            # paragraph's own line joins it — is what "preceding" means for
            # a link drawn in this paragraph; see `ExtractedLink.preceding_text`.
            preceding_text = "\n".join(lines)
            lines.append(f"## {text}" if is_heading else text)
            links.extend(_docx_links(paragraph, preceding_text=preceding_text))
        for box in _text_boxes(p):
            read_container(box, parent, "textbox" if kind == "body" else kind)

    def read_table(tbl, parent, kind: str) -> None:
        """A table, one line per row, its cells joined with " | ".

        A link in a cell is preceded by everything before the row, then the
        row's earlier cells, then the earlier paragraphs of its own cell — the
        order the row's own text reads in. It used to be preceded only by what
        came before the ROW, and a common template puts the whole resume body
        in one row (sidebar | main): every project's GitHub or Demo link in it
        then read as sitting above the first heading, and was filed as the
        candidate's own contact link. Each earlier cell and paragraph is a line
        of its own there, so a heading that opens a cell ("PROJECTS") is a line
        a heading test can see.
        """
        rows = 0
        for tr in _unwrap(tbl, frozenset({_W_TR})):
            cells: list[str] = []
            cell_texts: list[list[str]] = []
            #: (paragraph, its cell's index, how many of that cell's paragraphs precede it)
            paragraphs: list[tuple[Any, int, int]] = []
            later: list[tuple[str, Any]] = []
            for tc in _unwrap(tr, frozenset({_W_TC})):
                texts: list[str] = []
                for child in _unwrap(tc, _BLOCKS):
                    if child.tag == _W_P:
                        paragraphs.append((Paragraph(child, parent), len(cells), len(texts)))
                        texts.append(_paragraph_text(child).strip())
                        later.extend(("box", box) for box in _text_boxes(child))
                    else:
                        later.append(("table", child))
                cells.append("\n".join(text for text in texts if text))
                cell_texts.append(texts)
            row = " | ".join(cells)
            if row.strip(" |\n"):
                before_row = list(lines)
                lines.append(row)
                rows += 1
                for paragraph, cell_index, position in paragraphs:
                    earlier = [cell for cell in cells[:cell_index] if cell]
                    earlier += [text for text in cell_texts[cell_index][:position] if text]
                    preceding_text = "\n".join(before_row + earlier)
                    links.extend(_docx_links(paragraph, preceding_text=preceding_text))
            for what, element in later:
                if what == "table":
                    read_table(element, parent, kind)
                else:
                    read_container(element, parent, "textbox" if kind == "body" else kind)
        if rows:
            blocks.append(block_record(kind, type="table", rows=rows))

    def read_container(container, parent, kind: str) -> None:
        for child in _unwrap(container, _BLOCKS):
            if child.tag == _W_P:
                read_paragraph(child, parent, kind)
            else:
                read_table(child, parent, kind)

    # Page headers and footers are extra material: one that cannot be read is
    # left out — whatever it had added is taken back, so no half of it remains
    # — and never costs the body. Reading them inside the body's `try` made a
    # dangling header reference fail the whole document as malformed_docx, a
    # permanent code, on a file whose body the baseline read without trouble.
    try:
        furniture = _headers_and_footers(document)
    except Exception as exc:
        logger.warning("docx headers and footers could not be listed: %s", type(exc).__name__)
        furniture = []
    for kind, part_of_page in furniture:
        mark = (len(lines), len(blocks), len(links))
        try:
            read_container(part_of_page._element, part_of_page, kind)
        except Exception as exc:
            logger.warning("docx %s could not be read: %s", kind, type(exc).__name__)
            del lines[mark[0] :], blocks[mark[1] :], links[mark[2] :]
    # How many of the page's lines are page furniture rather than body, so the
    # structurer can tell "the first line of the body" from "the first line of
    # the page header" — see `FURNITURE_LINES_KEY`.
    furniture_lines = len("\n".join(lines).splitlines()) if lines else 0

    try:
        read_container(document.element.body, document, "body")
    except Exception as exc:
        raise ExtractionError("malformed_docx", "The DOCX structure could not be read.") from exc

    page = ExtractedPage(
        page_number=1,
        text=_clean("\n".join(lines)),
        method=METHOD_TEXT,
        metadata={
            "extractor": "python-docx",
            "blocks": blocks,
            FURNITURE_LINES_KEY: furniture_lines,
            # Said out loud so nothing downstream mistakes this for a real page.
            "note": "DOCX has no page boundaries; the whole document is page 1.",
        },
        links=list({link.url: link for link in links}.values()),
    )
    return ExtractedDocument(pages=[page])


def _docx_links(paragraph, *, preceding_text: str) -> list[ExtractedLink]:
    """External hyperlinks in one paragraph, with the text they were drawn over.

    Word stores the visible words in the run and the URL in the part's
    relationships, so reading `paragraph.text` alone loses every URL in the
    document — the same loss as a PDF's annotations, by a different mechanism.

    `preceding_text` is everything the document said before this paragraph —
    a DOCX has no coordinates to measure, but it has an unambiguous document
    order, and that order is the same signal `_preceding_text` measures
    geometrically for a PDF. See `ExtractedLink.preceding_text`.
    """
    from docx.oxml.ns import qn

    found: list[ExtractedLink] = []
    try:
        relationships = paragraph.part.rels
        for element in paragraph._p.iter(qn("w:hyperlink")):
            # A link inside a text box is that text box's, and is collected
            # when the text box is read — with its own, later, position.
            if _under(element, paragraph._p, _OPAQUE):
                continue
            rel_id = element.get(qn("r:id"))
            if not rel_id:
                continue
            relationship = relationships.get(rel_id)
            if relationship is None or not relationship.is_external:
                continue
            url = str(relationship.target_ref).strip()
            if not url:
                continue
            label = "".join(node.text or "" for node in element.iter(qn("w:t"))).strip()
            found.append(
                ExtractedLink(
                    url=url, label=(label[:100] or None), preceding_text=preceding_text
                )
            )
    except Exception:
        return found
    return found


# --- OCR routing -------------------------------------------------------------


def pages_needing_ocr(document: ExtractedDocument, *, min_chars: int) -> list[int]:
    """Which pages produced too little text to be believable.

    A scanned page usually yields nothing, or a few characters of noise from an
    embedded logo. A genuinely sparse page — a cover sheet — will also land here,
    which is acceptable: OCR on a nearly-empty page is cheap and harmless.
    """
    return [page.page_number for page in document.pages if page.char_count < min_chars]


#: With no OCR engine, the largest share of a document's pages that may go
#: unread before the run fails instead of continuing without them.
#:
#: "Most of the document" is measured in pages because a page that needs OCR
#: has, by definition, too little text to measure it by. Strictly more than
#: half fails: a one-page scan, or a three-page resume with two scanned pages,
#: is still `ocr_unavailable` — the confidently empty draft that failure exists
#: to prevent. A two-page resume whose second page reads only "References
#: available on request." is not: it used to fail outright, a readable resume
#: refused over one sparse page, with no retry that could help until an OCR
#: engine was installed. It now continues, and says which page it could not
#: read (see `mark_unread` and the pipeline's review-screen note). A page with
#: no text at all is never part of that allowance; see `must_fail_without_ocr`.
MAX_UNREAD_PAGE_SHARE = 0.5

#: The page-metadata key saying a page was left unread, and why. Persisted with
#: the page (`resume_document_pages.extraction_metadata`), so "which pages of
#: this run did nobody read?" outlives the run.
UNREAD_KEY = "unread"

#: Why a page may be left unread. A closed vocabulary: the review screen's
#: sentence is chosen from it, never composed from an exception.
UNREAD_OCR_UNAVAILABLE = "ocr_unavailable"
UNREAD_TOO_LARGE_TO_RENDER = "too_large_to_render"
UNREAD_PAGE_LIMIT = "page_limit"


def must_fail_without_ocr(document: ExtractedDocument, ocr_pages: list[int]) -> bool:
    """Whether the pages OCR could not reach carry most of the document — or
    any one of them carries nothing at all.

    The allowance is for SPARSE pages: a page with a few words of its own
    ("References available on request.") that is short of the OCR threshold.
    A page with no text whatever is not sparse, it is unread — a whole scanned
    page, and when it is page 1 it holds the name, the contact block and the
    current role. One such page of two is exactly half, so the share alone
    let the run continue and build the confidently partial draft this failure
    exists to prevent. Without an engine nothing can tell a scan from a blank
    page, so it fails as `ocr_unavailable`, as every such page did before the
    allowance existed, and may be retried by hand once an engine is installed.
    """
    if not ocr_pages or not document.page_count:
        return False
    wanted = set(ocr_pages)
    if any(page.char_count == 0 for page in document.pages if page.page_number in wanted):
        return True
    return len(ocr_pages) / document.page_count > MAX_UNREAD_PAGE_SHARE


def mark_unread(
    document: ExtractedDocument, page_numbers: list[int], *, reason: str
) -> ExtractedDocument:
    """The same document, with the named pages flagged as not fully read.

    Their text is kept: a sparse page's few words ("References available on
    request.") are real text and still reach the structurer. What the flag
    says is that anything else on the page — an image, a scanned block — was
    never read by anyone, which is what the review screen must be able to say.
    """
    wanted = set(page_numbers)
    return replace(
        document,
        pages=[
            replace(page, metadata={**page.metadata, UNREAD_KEY: reason})
            if page.page_number in wanted
            else page
            for page in document.pages
        ],
    )


def unread_pages(document: ExtractedDocument) -> list[tuple[str, str]]:
    """(which pages, reason) for everything this document left unread.

    Flagged pages one by one, then — as one entry — any pages past
    `MAX_PDF_PAGES` that were never opened at all.
    """
    found = [
        (f"Page {page.page_number}", str(page.metadata[UNREAD_KEY]))
        for page in document.pages
        if page.metadata.get(UNREAD_KEY)
    ]
    if document.pages_not_read > 0:
        first = document.page_count + 1
        last = document.page_count + document.pages_not_read
        label = f"Page {first}" if first == last else f"Pages {first}–{last}"
        found.append((label, UNREAD_PAGE_LIMIT))
    return found
