"""The application's database engine and session factory.

hide_parameters=True, on every engine the application builds. Without it a
DataError or IntegrityError renders its bound parameters into the exception
message — `[parameters: {...}]` — and that message is what a traceback in the
log prints. The parameters of this application's statements are resume page
text, names, email addresses and phone numbers, so an unexpected database
error was a way to copy a person's resume into a log file. With it the message
says "[SQL parameters hidden due to hide_parameters=True]" and still carries
the SQL, the SQLSTATE and the constraint name, which is everything debugging a
database error needs. Nothing reads parameter values off an exception: the
error mapping in app/api/errors.py keys on SQLSTATE and constraint name.
"""

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


def build_engine(url: str, **options: Any) -> Engine:
    """An engine configured the way every application engine must be.

    One function rather than a keyword repeated at each call site, so a second
    engine — a worker, a script — cannot be built without it by forgetting.
    """
    return create_engine(url, hide_parameters=True, **options)


engine = build_engine(settings.DATABASE_URL)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)
