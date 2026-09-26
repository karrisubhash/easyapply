"""Upload validation.

The rule here is that nothing the client tells us is evidence. The filename, the
extension and the browser-supplied Content-Type are all attacker-controlled, so
they are checked for consistency but never believed: the decision is made by
reading the file's own bytes.

Nothing in this module opens, parses, renders or executes the upload. It looks
at a signature and, for DOCX, at a ZIP central directory. Parsing happens later,
in extraction, and is treated as hostile there too.
"""

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO

from app.core.config import settings

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

SUPPORTED_MEDIA_TYPES = (PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE)

_EXTENSION_FOR_MEDIA_TYPE = {
    PDF_MEDIA_TYPE: "pdf",
    DOCX_MEDIA_TYPE: "docx",
}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"

# The one entry every Word document has. A generic ZIP renamed to .docx will
# not have it, which is exactly the case we are trying to catch.
_DOCX_REQUIRED_ENTRY = "word/document.xml"

# Filenames are only ever displayed, never used to build a path — but a
# filename containing a separator, a null byte or a traversal segment is a
# signal about the client, so it is refused rather than sanitised silently.
_FILENAME_FORBIDDEN = re.compile(r"[\\/\x00]|(^|[\\/])\.\.($|[\\/])")

MAX_FILENAME_LENGTH = 255


class FileValidationError(ValueError):
    """The upload is not something we are willing to process."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedUpload:
    """What the rest of the system is allowed to believe about an upload."""

    filename: str
    media_type: str
    extension: str
    byte_size: int


def validate_upload(
    *,
    filename: str | None,
    declared_media_type: str | None,
    data: bytes,
    max_bytes: int | None = None,
) -> ValidatedUpload:
    """Decide whether these bytes may enter the system, and as what.

    `declared_media_type` is used only to cross-check the sniffed type. When
    they disagree, the bytes win and the upload is refused, because a mismatch
    means either a broken client or a deliberate one.
    """
    limit = max_bytes if max_bytes is not None else settings.RESUME_MAX_UPLOAD_BYTES

    safe_name = _validate_filename(filename)

    if not data:
        raise FileValidationError("empty_file", "The uploaded file is empty.")

    if len(data) > limit:
        raise FileValidationError(
            "file_too_large",
            f"The file is larger than the {limit // (1024 * 1024)} MB limit.",
        )

    sniffed = _sniff_media_type(data)
    if sniffed is None:
        raise FileValidationError(
            "unsupported_file_type",
            "Only PDF and DOCX resumes are supported.",
        )

    extension = _EXTENSION_FOR_MEDIA_TYPE[sniffed]

    # The extension has no authority, but a disagreement is worth refusing:
    # a .pdf whose bytes are a DOCX is not a case with an honest explanation.
    name_extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if name_extension and name_extension != extension:
        raise FileValidationError(
            "extension_mismatch",
            f"The file is a {extension.upper()} but is named .{name_extension}.",
        )

    if declared_media_type:
        declared = declared_media_type.split(";", 1)[0].strip().lower()
        # Browsers send octet-stream when they cannot tell; that is ignorance,
        # not a claim, so it is allowed through to the sniffer's verdict.
        if declared not in ("", "application/octet-stream") and declared != sniffed:
            raise FileValidationError(
                "media_type_mismatch",
                "The declared content type does not match the file contents.",
            )

    return ValidatedUpload(
        filename=safe_name,
        media_type=sniffed,
        extension=extension,
        byte_size=len(data),
    )


def _validate_filename(filename: str | None) -> str:
    if not filename or not filename.strip():
        raise FileValidationError("missing_filename", "The upload has no filename.")

    candidate = filename.strip()

    if len(candidate) > MAX_FILENAME_LENGTH:
        raise FileValidationError("filename_too_long", "The filename is too long.")

    if _FILENAME_FORBIDDEN.search(candidate):
        raise FileValidationError(
            "unsafe_filename",
            "The filename contains path separators or traversal segments.",
        )

    return candidate


def _sniff_media_type(data: bytes) -> str | None:
    """Identify the file from its own bytes, or not at all."""
    if data.startswith(_PDF_MAGIC):
        return PDF_MEDIA_TYPE

    if data.startswith(_ZIP_MAGIC) and _is_docx_container(data):
        return DOCX_MEDIA_TYPE

    return None


def _is_docx_container(data: bytes) -> bool:
    """A ZIP is only a DOCX if it actually contains a Word document part.

    Reading the central directory does not decompress anything, so a zip bomb
    cannot be triggered here. Decompression happens in extraction, against a
    file that has already been accepted.
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            return _DOCX_REQUIRED_ENTRY in archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return False
