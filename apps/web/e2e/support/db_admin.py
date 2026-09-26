"""Database and fixture chores for the browser suite.

Run from `services/api` so the application package imports, and kept in Python
rather than Node for one reason: the fixture resume is built by
`tests/resume_fixtures.make_resume_pdf`, the same function the pytest suite
uses. A second PDF builder written in JavaScript would be a second definition of
"a document the extractor can read", free to drift from the one that is actually
tested.

    python db_admin.py recreate                 drop and create the e2e database
    python db_admin.py truncate                 empty every table, keep the schema
    python db_admin.py make-resume <path>       write the fixture PDF
    python db_admin.py seed-grounding-failure --email … --job-id …

The last one is the interesting one. See its docstring.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
API_ROOT = HERE.parents[3] / "services" / "api"
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def _url() -> str:
    """The e2e database, and a refusal to be pointed anywhere else.

    This module truncates tables. The guard is not politeness: a mistyped
    environment variable would otherwise empty the database someone is using,
    and the failure would look like "my data vanished" rather than like an
    error.
    """
    url = os.environ.get("E2E_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("E2E_DATABASE_URL is not set")
    if not make_url(url).database.endswith("_e2e"):
        raise SystemExit(
            f"refusing to operate on {make_url(url).database!r}: "
            "the browser suite only ever touches a database ending in _e2e"
        )
    return url


def recreate() -> None:
    url = make_url(_url())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


def truncate() -> None:
    """Empty every table, leaving the schema and its triggers in place.

    Between tests rather than between runs: each test builds the state it needs
    from the UI, and a test that inherited another's jobs would pass or fail
    depending on the order it ran in.

    `users` is included. Identity is provisioned just-in-time, so a truncated
    users table simply means the next request through a web server creates its
    user again — which is also what a genuinely new visitor does.
    """
    engine = create_engine(_url())
    with engine.begin() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename <> 'alembic_version'"
                )
            )
        ]
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            # RESTART IDENTITY so ids start from 1 in every test: a URL-tampering
            # assertion needs an id that predictably does NOT exist.
            connection.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
    engine.dispose()


def make_resume(target: str) -> None:
    from tests.resume_fixtures import make_resume_pdf

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_resume_pdf())


def make_golden_resume(target: str) -> None:
    """The evaluation dataset's resume, from the builder the golden test uses.

    Same reasoning as make_resume: one definition of the document, so what the
    browser uploads is a document the structurer is known to read correctly
    rather than a second PDF free to drift from the one under test.
    """
    from evaluation.datasets.resume_profile_v1.resume_source import build_resume_pdf

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_resume_pdf())


def seed_grounding_failure(email: str, job_id: int) -> int:
    """Produce a tailored resume that grounding genuinely refuses.

    Why this exists: the default generator SELECTS clauses verbatim out of the
    profile, so it cannot fabricate a claim — which is the point of it, and
    which means the rejection screen is unreachable through the UI without
    turning the AI generator on. The verification brief forbids that.

    What this does NOT do is fake the verdict. It runs the REAL pipeline — the
    real state machine, the real grounding validator, the real persistence —
    with one substitution: a generator that invents a number, standing in for a
    model that did. Every row it writes is written by the code that would have
    written it in production, and the verdict is computed by the validator
    rather than asserted by this script. The browser then loads the page through
    the real API and renders whatever is actually there.
    """
    from app.db.session import SessionLocal
    from app.models import Job, User
    from app.schemas.tailored_resume import (
        TailoredExperience,
        TailoredLine,
        TailoredResumeDocument,
    )
    from app.services.tailoring import pipeline, service
    from app.services.tailoring.generation import GenerationResult

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=email).one()
        job = db.query(Job).filter_by(id=job_id, user_id=user.id).one()

        run, _ = service.start_tailoring(db, user_id=user.id, job_id=job.id)

        class InventingTailor:
            """A model that added a number nobody recorded."""

            def generate(self, inputs):
                experience = inputs.version.experiences[0]
                quote = (experience.description or "").split(".")[0].strip() + "."
                return GenerationResult(
                    document=TailoredResumeDocument(
                        experience=[
                            TailoredExperience(
                                source_id=experience.id,
                                company=experience.company,
                                job_title=experience.job_title,
                                location=experience.location,
                                start_date=experience.start_date,
                                start_date_precision=experience.start_date_precision,
                                end_date=experience.end_date,
                                end_date_precision=experience.end_date_precision,
                                bullets=[
                                    TailoredLine(
                                        text=f"{quote[:-1]}, saving the company 40% in costs.",
                                        source_quote=quote,
                                        source_field="description",
                                    )
                                ],
                            )
                        ]
                    ),
                    generator="ai",
                    model="e2e-inventing-model",
                    prompt_version="e2e",
                )

        original = pipeline.get_tailor
        pipeline.get_tailor = lambda: InventingTailor()
        try:
            pipeline.process_run(db, run_id=run.id)
        finally:
            pipeline.get_tailor = original

        db.expire_all()
        finished = service.get_run(db, user_id=user.id, run_id=run.id)
        if finished.result is None or finished.result.grounding_status != "failed":
            raise SystemExit(
                "seeding did not produce a grounding failure — the validator "
                f"returned {finished.result and finished.result.grounding_status!r}"
            )
        return run.id
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("recreate")
    sub.add_parser("truncate")

    resume = sub.add_parser("make-resume")
    resume.add_argument("path")

    golden = sub.add_parser("make-golden-resume")
    golden.add_argument("path")

    failure = sub.add_parser("seed-grounding-failure")
    failure.add_argument("--email", required=True)
    failure.add_argument("--job-id", type=int, required=True)

    args = parser.parse_args()

    if args.command == "recreate":
        recreate()
    elif args.command == "truncate":
        truncate()
    elif args.command == "make-resume":
        make_resume(args.path)
    elif args.command == "make-golden-resume":
        make_golden_resume(args.path)
    elif args.command == "seed-grounding-failure":
        print(seed_grounding_failure(args.email, args.job_id))


if __name__ == "__main__":
    main()
