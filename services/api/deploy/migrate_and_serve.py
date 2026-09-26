"""Migrate the database, then serve the API — in ONE interpreter.

For a deployment with no separate release step (the free-tier staging
container, deploy/staging/start.sh). Running `alembic upgrade head` and then
uvicorn as two processes imports the application's models twice; on a
fractional CPU that import is most of a cold start, and a cold start is what a
person waits through when an idle free instance wakes up.

The migration runs first and a failure exits non-zero before anything serves,
exactly as a separate release step would. Host and port come from API_HOST
(default 127.0.0.1 — loopback, so only the web app beside it can reach the API)
and API_PORT (default 8000).

    python deploy/migrate_and_serve.py       (from services/api, or /app/api)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(API_ROOT))

    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(API_ROOT / "alembic.ini")), "head")

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", "8000")),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
