"""Read a downloaded .docx back, the way a recruiter's tooling would.

Used by the browser suite after a download, and deliberately NOT a zip-and-regex
in Node. A tailored resume is written by `python-docx`, and the question the
suite needs answered is whether the file a person receives opens in a real Word
reader — not whether it happens to be a zip with the right words somewhere in
it. Opening it with the same library that wrote it is the closest cheap
approximation of that, and it fails loudly on an archive that unzips but is not
a document.

Prints JSON on stdout:

    {"paragraphs": [...], "text": "...", "runs": [...]}

`runs` carries bold/size per run so the suite can assert that the candidate's
name is actually formatted as a name rather than merely present in the file.
"""

from __future__ import annotations

import json
import sys

from docx import Document


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: docx_text.py <path-to.docx>", file=sys.stderr)
        return 2

    document = Document(sys.argv[1])

    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    runs = [
        {
            "text": run.text,
            "bold": bool(run.bold),
            "size": run.font.size.pt if run.font.size is not None else None,
        }
        for paragraph in document.paragraphs
        for run in paragraph.runs
        if run.text.strip()
    ]

    # Tables are not used by the renderer today. Reported anyway: a layout that
    # started emitting them without anyone noticing is exactly the kind of
    # change that breaks an applicant tracking system's parse.
    tables = [
        [cell.text for row in table.rows for cell in row.cells]
        for table in document.tables
    ]

    json.dump(
        {
            "paragraphs": [text for text in paragraphs if text.strip()],
            "text": "\n".join(paragraphs),
            "runs": runs,
            "tables": tables,
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
