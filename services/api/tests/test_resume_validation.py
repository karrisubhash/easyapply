"""File validation: nothing the client says is evidence."""

import zipfile
from io import BytesIO

import pytest

from app.services.resume.validation import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    FileValidationError,
    validate_upload,
)
from tests.resume_fixtures import make_resume_docx, make_resume_pdf


def expect_rejection(code: str, **kwargs):
    with pytest.raises(FileValidationError) as caught:
        validate_upload(**kwargs)
    assert caught.value.code == code
    return caught.value


def test_a_valid_pdf_is_accepted():
    result = validate_upload(
        filename="resume.pdf", declared_media_type="application/pdf", data=make_resume_pdf()
    )
    assert result.media_type == PDF_MEDIA_TYPE
    assert result.extension == "pdf"
    assert result.byte_size > 0


def test_a_valid_docx_is_accepted():
    result = validate_upload(
        filename="resume.docx", declared_media_type=DOCX_MEDIA_TYPE, data=make_resume_docx()
    )
    assert result.media_type == DOCX_MEDIA_TYPE
    assert result.extension == "docx"


def test_an_unsupported_extension_is_rejected():
    expect_rejection(
        "unsupported_file_type",
        filename="resume.txt",
        declared_media_type="text/plain",
        data=b"just some text, not a document",
    )


def test_an_executable_is_rejected_whatever_it_is_called():
    """A PE binary renamed to .pdf is still a PE binary."""
    expect_rejection(
        "unsupported_file_type",
        filename="resume.pdf",
        declared_media_type="application/pdf",
        data=b"MZ\x90\x00" + b"\x00" * 200,
    )


def test_a_lying_content_type_is_rejected():
    expect_rejection(
        "media_type_mismatch",
        filename="resume.pdf",
        declared_media_type=DOCX_MEDIA_TYPE,
        data=make_resume_pdf(),
    )


def test_a_lying_extension_is_rejected():
    expect_rejection(
        "extension_mismatch",
        filename="resume.docx",
        declared_media_type=None,
        data=make_resume_pdf(),
    )


def test_an_octet_stream_declaration_is_tolerated():
    """Browsers send octet-stream when they cannot tell. That is ignorance,
    not a claim, so the sniffed type decides."""
    result = validate_upload(
        filename="resume.pdf",
        declared_media_type="application/octet-stream",
        data=make_resume_pdf(),
    )
    assert result.media_type == PDF_MEDIA_TYPE


def test_a_malformed_pdf_is_rejected():
    expect_rejection(
        "unsupported_file_type",
        filename="resume.pdf",
        declared_media_type="application/pdf",
        data=b"%PD" + b"\x00" * 50,
    )


def test_a_zip_that_is_not_a_docx_is_rejected():
    """A plain ZIP renamed to .docx has no word/document.xml."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a word document")

    expect_rejection(
        "unsupported_file_type",
        filename="resume.docx",
        declared_media_type=DOCX_MEDIA_TYPE,
        data=buffer.getvalue(),
    )


def test_a_truncated_docx_is_rejected():
    expect_rejection(
        "unsupported_file_type",
        filename="resume.docx",
        declared_media_type=DOCX_MEDIA_TYPE,
        data=make_resume_docx()[:200],
    )


def test_an_oversized_file_is_rejected():
    expect_rejection(
        "file_too_large",
        filename="resume.pdf",
        declared_media_type="application/pdf",
        data=make_resume_pdf(),
        max_bytes=100,
    )


def test_an_empty_file_is_rejected():
    expect_rejection(
        "empty_file", filename="resume.pdf", declared_media_type="application/pdf", data=b""
    )


@pytest.mark.parametrize(
    "filename",
    ["../../etc/passwd.pdf", "..\\..\\windows\\system32\\x.pdf", "a/b.pdf", "x\x00.pdf"],
)
def test_a_traversing_filename_is_rejected(filename):
    """Filenames are display-only, but one containing a separator is a signal."""
    expect_rejection(
        "unsafe_filename",
        filename=filename,
        declared_media_type="application/pdf",
        data=make_resume_pdf(),
    )


def test_a_missing_filename_is_rejected():
    expect_rejection(
        "missing_filename",
        filename=None,
        declared_media_type="application/pdf",
        data=make_resume_pdf(),
    )


def test_an_absurdly_long_filename_is_rejected():
    expect_rejection(
        "filename_too_long",
        filename="a" * 300 + ".pdf",
        declared_media_type="application/pdf",
        data=make_resume_pdf(),
    )
