"""The golden resume, built as a real PDF rather than checked in as bytes.

Built from code for the same reason `tests/resume_fixtures.py` is: a reader can
see exactly what the document says, and the parsers under test are the real ones
rather than a stub. Unlike that module, this one lays glyphs out in columns and
attaches real /URI annotations, because the failures this dataset exists to
catch are layout failures — a right-aligned date column read as part of the job
title, a link caption read as a technology, a wrapped line read as a new job.

The content is invented. It deliberately mirrors the shape of the real resume
that exposed the bug, without carrying anyone's real address or phone number
into the repository.
"""

from io import BytesIO

FONT_SIZE = 10.0
LEADING = 14.0
TOP = 760.0
LEFT = 56.0
#: Helvetica averages a little over half the point size per character. Only used
#: to size link rectangles, which need to cover their glyphs and miss their
#: neighbours — not to position text, which is placed explicitly.
CHAR_WIDTH = 0.62


def _escape(text: str) -> bytes:
    """PDF string bytes in WinAnsi, which is what the font declares.

    cp1252 rather than latin-1 because a resume's bullet (U+2022) and en dash
    (U+2013) exist in WinAnsi and not in latin-1 — encoding to latin-1 replaced
    every bullet with "?", which is precisely the character this dataset needs
    the structurer to recognise.
    """
    return (
        text.replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
        .encode("cp1252", "replace")
    )


class _Page:
    def __init__(self) -> None:
        self.segments: list[tuple[float, float, str]] = []
        self.links: list[tuple[tuple[float, float, float, float], str]] = []
        self._y = TOP

    def line(self, *segments: tuple[float, str] | str) -> None:
        """One line of text. A bare string starts at the left margin."""
        for segment in segments:
            if isinstance(segment, str):
                x, text = LEFT, segment
            else:
                x, text = segment
            if text:
                self.segments.append((x, self._y, text))
        self._y -= LEADING

    def link_line(self, *segments: tuple[float, str, str | None]) -> None:
        """A line whose segments carry hyperlinks, as (x, text, url)."""
        for x, text, url in segments:
            self.segments.append((x, self._y, text))
            if url:
                width = len(text) * FONT_SIZE * CHAR_WIDTH
                self.links.append(((x, self._y - 2, x + width, self._y + FONT_SIZE), url))
        self._y -= LEADING

    def blank(self) -> None:
        self._y -= LEADING * 0.5

    def content(self) -> bytes:
        parts = [b"BT", b"/F1 %.1f Tf" % FONT_SIZE]
        for x, y, text in self.segments:
            parts.append(b"1 0 0 1 %.2f %.2f Tm" % (x, y))
            parts.append(b"(" + _escape(text) + b") Tj")
        parts.append(b"ET")
        return b"\n".join(parts)


def _compose() -> _Page:
    page = _Page()

    # --- header: name, contact row, and a row of linked captions ------------
    page.line("ALEX MORGAN")
    page.line("Bristol, United Kingdom | +44 7700 900123 | alex.morgan@example.com")
    page.link_line(
        (LEFT, "LinkedIn", "https://www.linkedin.com/in/alex-morgan-example/"),
        (LEFT + 52, "|", None),
        (LEFT + 62, "GitHub", "https://github.com/alexmorgan-example"),
        (LEFT + 106, "|", None),
        (LEFT + 116, "Portfolio", "https://alexmorgan.example.com"),
    )
    page.blank()

    # --- summary: one paragraph, wrapped across three lines ------------------
    page.line("Summary")
    page.line("Backend engineer focused on distributed systems and data pipelines, with")
    page.line("production experience building Python services, event-driven architectures")
    page.line("and observability tooling across two regulated industries.")
    page.blank()

    # --- skills: labelled rows, the last one wrapping mid-list ---------------
    page.line("Technical Skills")
    page.line("Languages: Python, Go, SQL, TypeScript")
    page.line("Backend: Django, FastAPI, Celery, gRPC, REST APIs")
    page.line("Data: PostgreSQL, Redis, Kafka, dbt, Query Optimisation")
    page.line("Cloud & Tooling: AWS, Docker, Kubernetes, Terraform, GitHub Actions, Load")
    page.line("Testing")
    page.blank()

    # --- experience: right-aligned dates, an ongoing role, wrapped bullets ---
    page.line("Experience")
    page.line((LEFT, "Senior Backend Engineer"), (420, "Mar 2022 - Present"))
    page.line((LEFT, "Northwind Logistics"), (420, "Bristol, United Kingdom"))
    page.line("• Rebuilt the shipment tracking service in Python and FastAPI, cutting p99")
    page.line("latency by 63% across 4M daily events.")
    page.line("• Introduced Kafka-based event streaming, replacing a nightly batch job and")
    page.line("reducing data staleness from 18 hours to under 2 minutes.")
    page.line("• Mentored 4 engineers through design review and on-call onboarding.")
    page.line((LEFT, "Backend Engineer"), (420, "Jul 2019 - Feb 2022"))
    page.line((LEFT, "Cavendish Health"), (420, "Reading, United Kingdom"))
    page.line("• Built HL7 and FHIR ingestion pipelines processing 250k patient records")
    page.line("daily, replacing a manual nightly import.")
    page.line("• Reduced infrastructure spend by 31% by right-sizing ECS services.")
    page.blank()

    # --- projects: technologies after a pipe, with link captions running on --
    page.line("Projects")
    page.link_line(
        (LEFT, "Ledger Reconciliation Engine | Python, PostgreSQL, Celery", None),
        (330, "GitHub", "https://github.com/alexmorgan-example/ledger-reconciliation"),
        (376, "|", None),
        (386, "Demo", "https://demo.alexmorgan.example.com/ledger"),
    )
    page.line("• Reconciles 1.2M daily transactions against three upstream ledgers with")
    page.line("idempotent replay and a full audit trail.")
    page.line("• Exposes a REST API and a Prometheus metrics endpoint.")
    page.link_line(
        (LEFT, "Observability Starter Kit | Terraform, Grafana, Prometheus", None),
        (334, "GitHub", "https://github.com/alexmorgan-example/observability-starter"),
    )
    page.line("• Opinionated Terraform module deploying a metrics and tracing stack.")
    page.blank()

    # --- certifications ------------------------------------------------------
    page.line("Certifications")
    page.line("AWS Certified Solutions Architect | Amazon Web Services | 2023")
    page.line("Certified Kubernetes Administrator | CNCF | 2022")
    page.blank()

    # --- a section nothing downstream holds, which must still end the one above
    page.line("Achievements")
    page.line("• Speaker at PyCon UK 2023 on event-driven Python.")
    page.blank()

    # --- education: grade on the entry line, institution on the next ---------
    page.line("Education")
    page.line((LEFT, "BSc, Computer Science | First Class Honours"), (420, "2015 - 2018"))
    page.line("University of Bristol, Bristol")

    return page


def build_resume_pdf() -> bytes:
    """The golden resume as PDF bytes, byte-for-byte the same on every run."""
    page = _compose()
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )

    stream = page.content()
    content_id = add(
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
    )

    annotation_ids: list[int] = []
    for (x0, y0, x1, y1), url in page.links:
        annotation_ids.append(
            add(
                b"<< /Type /Annot /Subtype /Link /Border [0 0 0] "
                b"/Rect [%.2f %.2f %.2f %.2f] /A << /Type /Action /S /URI /URI (%s) >> >>"
                % (x0, y0, x1, y1, _escape(url))
            )
        )

    pages_id = len(objects) + 2
    annots = b" ".join(b"%d 0 R" % item for item in annotation_ids)
    page_id = add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R /Annots [%s] >>"
        % (pages_id, font_id, content_id, annots)
    )
    actual_pages_id = add(b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % page_id)
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % actual_pages_id)

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number)
        out.write(body)
        out.write(b"\nendobj\n")

    xref_offset = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\n" % (len(objects) + 1, catalog_id))
    out.write(b"startxref\n%d\n%%%%EOF\n" % xref_offset)
    return out.getvalue()


if __name__ == "__main__":
    from pathlib import Path

    target = Path(__file__).parent / "resume.pdf"
    target.write_bytes(build_resume_pdf())
    print(f"wrote {target} ({target.stat().st_size} bytes)")
