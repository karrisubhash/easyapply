"""OCR, behind an interface.

The engine's whole contract is: given the bytes of a raster image, return text.
It knows nothing about PDFs, pages or resumes, which is what keeps the rest of
the system from depending on Tesseract. Swapping in Azure AI Document
Intelligence later means adding a class here that satisfies `OcrEngine`.

Rasterising a PDF page is a separate concern and lives in this module too, but
outside the engine — an engine that had to understand PDFs would be an engine
that could not be replaced by a cloud OCR service.
"""

import logging
import math
from dataclasses import replace
from typing import Protocol

from app.core.config import settings
from app.services.resume.extraction import (
    METHOD_OCR,
    UNREAD_KEY,
    UNREAD_TOO_LARGE_TO_RENDER,
    ExtractedDocument,
    ExtractedPage,
)

logger = logging.getLogger(__name__)


class OcrError(RuntimeError):
    """OCR was required and could not be performed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OcrEngine(Protocol):
    """Image bytes in, text out. Nothing else."""

    name: str

    def is_available(self) -> bool: ...

    def image_to_text(self, image: bytes) -> str: ...


class TesseractOcrEngine:
    """Local development OCR.

    Tesseract is an external binary, so availability is a runtime fact rather
    than an import-time one. `is_available()` is what the pipeline checks before
    committing to an OCR path, so a machine without the binary produces a clear
    failure at a known stage instead of an exception from inside a library.
    """

    name = "tesseract"

    def is_available(self) -> bool:
        try:
            import pytesseract

            pytesseract.get_tesseract_version()
            return True
        except Exception as exc:
            logger.info("tesseract unavailable: %s", exc)
            return False

    def image_to_text(self, image: bytes) -> str:
        from io import BytesIO

        import pytesseract
        from PIL import Image

        try:
            with Image.open(BytesIO(image)) as handle:
                return pytesseract.image_to_string(handle)
        except Exception as exc:
            raise OcrError("ocr_failed", "The OCR engine could not read the page.") from exc


class UnavailableOcrEngine:
    """An engine that honestly reports it cannot work.

    Used when OCR is switched off by configuration. It exists so the pipeline
    always has an engine object and never has to branch on None.
    """

    name = "disabled"

    def is_available(self) -> bool:
        return False

    def image_to_text(self, image: bytes) -> str:
        raise OcrError("ocr_unavailable", "OCR is not available in this environment.")


def get_ocr_engine() -> OcrEngine:
    if not settings.RESUME_OCR_ENABLED:
        return UnavailableOcrEngine()
    return TesseractOcrEngine()


#: The largest bitmap one page may be rendered to, in pixels. PDFium allocates
#: the whole bitmap up front, four bytes a pixel, from nothing but the page's
#: declared size — so a 435-byte PDF declaring a 14400-point square page asked
#: for a 28800 x 28800 bitmap at the default scale: 3.3 GB, for one page. A
#: Letter page at that scale is about two megapixels, and A4 at 300 dpi under
#: nine, so twenty-five is generous for anything a resume is printed on.
#:
#: A page above it at the configured scale is rendered at the largest scale
#: that fits, not skipped. Image-to-PDF tools and phone scanners size the page
#: in PIXELS at 72 dpi — an A4 scan at 300 dpi is a 2480 x 3508-point page, a
#: 12 MP photo 3024 x 4032 — and at scale 2 those are 35 and 49 megapixels.
#: Skipping them left the run with no text at all: a permanent
#: no_text_extracted on an ordinary scan, with an OCR engine installed. At
#: scale 1 such a page is its own image, pixel for pixel.
MAX_RENDER_PIXELS = 25_000_000


def _render_scale(width: float, height: float, wanted: float) -> float | None:
    """The scale to render a `width` x `height`-point page at: `wanted`, or the
    largest smaller scale whose bitmap stays within `MAX_RENDER_PIXELS`.

    None for a page with no renderable area. PDFium sizes the bitmap as
    ceil(width x scale) by ceil(height x scale), so the bound is checked on
    exactly that.
    """
    if not (width > 0 and height > 0) or not math.isfinite(width * height):
        return None

    def pixels(scale: float) -> int:
        return math.ceil(width * scale) * math.ceil(height * scale)

    scale = min(wanted, math.sqrt(MAX_RENDER_PIXELS / (width * height)))
    while scale > 0 and pixels(scale) > MAX_RENDER_PIXELS:
        scale *= 0.99
    return scale if scale > 0 else None


def render_pdf_pages(data: bytes, page_numbers: list[int], *, scale: float | None = None) -> dict[int, bytes]:
    """Rasterise selected PDF pages to PNG.

    Uses PDFium rather than a poppler shell-out: no external binary to install,
    and no subprocess taking a path to an untrusted file.

    A page whose bitmap would exceed `MAX_RENDER_PIXELS` at `scale` is rendered
    smaller, within the bound (see `_render_scale`). The size is read from the
    page before anything is allocated. Only a page with no renderable size is
    skipped: it is absent from the result, and `apply_ocr` flags it.
    """
    from io import BytesIO

    import pypdfium2 as pdfium

    render_scale = scale if scale is not None else settings.RESUME_OCR_RENDER_SCALE
    images: dict[int, bytes] = {}

    # Opening the document is inside the try, not before it. PDFium raises
    # PdfiumError on bytes it cannot load, and that is not an OcrError — so
    # outside the try it escapes the pipeline's error handling entirely and
    # strands the run in a non-terminal state instead of failing it.
    try:
        document = pdfium.PdfDocument(data)
        try:
            for page_number in page_numbers:
                index = page_number - 1
                if index < 0 or index >= len(document):
                    continue
                page = document[index]
                width, height = page.get_size()
                page_scale = _render_scale(width, height, render_scale)
                if page_scale is None:
                    logger.warning("pdf page %s has no size to render for OCR", page_number)
                    continue
                if page_scale < render_scale:
                    logger.info(
                        "pdf page %s rendered at scale %.2f to stay within the bitmap bound",
                        page_number,
                        page_scale,
                    )
                bitmap = page.render(scale=page_scale)
                image = bitmap.to_pil()

                buffer = BytesIO()
                image.save(buffer, format="PNG")
                images[page_number] = buffer.getvalue()
        finally:
            document.close()
    except Exception as exc:
        raise OcrError("rasterise_failed", "The PDF pages could not be rendered for OCR.") from exc

    return images


def apply_ocr(
    document: ExtractedDocument,
    *,
    source: bytes,
    page_numbers: list[int],
    engine: OcrEngine,
) -> ExtractedDocument:
    """Re-read the named pages with OCR, leaving the others untouched.

    Only the pages that needed it are replaced, so a resume whose first page is
    a scanned header and whose second page is real text keeps the good text.
    """
    if not engine.is_available():
        raise OcrError(
            "ocr_unavailable",
            "This document needs OCR, but no OCR engine is available.",
        )

    images = render_pdf_pages(source, page_numbers)

    pages: list[ExtractedPage] = []
    for page in document.pages:
        image = images.get(page.page_number)
        if page.page_number not in page_numbers:
            pages.append(page)
            continue
        if image is None:
            # Wanted OCR and could not be rendered at any size within the
            # bound: kept as it is, and said so, rather than passed off as a
            # page that was read.
            pages.append(
                replace(
                    page,
                    metadata={**page.metadata, UNREAD_KEY: UNREAD_TOO_LARGE_TO_RENDER},
                )
            )
            continue

        text = engine.image_to_text(image)
        pages.append(
            replace(
                page,
                text=text,
                method=METHOD_OCR,
                metadata={**page.metadata, "ocr_engine": engine.name},
            )
        )

    return replace(document, pages=pages)
