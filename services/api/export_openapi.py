"""Write the running app's OpenAPI schema to packages/shared/openapi.json.

The generated TypeScript contract is produced from that file, so the file has to
be re-exported whenever a Pydantic schema or a route changes. Without this
script that step was a command someone had to remember and type correctly, and
forgetting it made `npm run api:types` a silent no-op — the types would still
compile, still be wrong, and only fail somewhere else much later.

    npm run api:types               # from the repo root: export, then generate
    python services/api/export_openapi.py   # just the export
"""

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Both are needed, and for different reasons: the path so `app` is importable,
# the working directory because Settings reads a relative `.env`. Without the
# chdir this works from services/api and fails from the repo root — which is
# exactly where npm runs it.
sys.path.insert(0, str(HERE))
os.chdir(HERE)

from app.main import app  # noqa: E402

TARGET = HERE.parents[1] / "packages" / "shared" / "openapi.json"


def main() -> int:
    schema = app.openapi()
    TARGET.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {TARGET} ({len(schema['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
