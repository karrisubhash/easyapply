"""Create (or re-create) the easyapply-staging Render web service from the CLI.

Mirrors render.yaml (docs/staging-deployment.md §5). The Render CLI cannot edit a
service's environment after creation, so a configuration change is a delete and
a re-create — the service is stateless; everything lives in the database.

Secrets are generated here and handed to the CLI; nothing secret is printed or
written to disk. The database URL is read from Render (internal connection
string) at run time. Entra values come from a JSON file kept OUTSIDE the repo:

    render services delete easyapply-staging --confirm      # when re-creating
    python deploy/staging/render_create_service.py --entra <outside the repo>/entra.json

RENDER_CLI names the CLI binary (default: `render` on PATH).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys

RENDER = os.environ.get("RENDER_CLI", "render")
ORIGIN_HOST = "easyapply-staging.onrender.com"


def render(*args: str) -> str:
    done = subprocess.run([RENDER, *args], capture_output=True, text=True)
    if done.returncode != 0:
        # The CLI's error names flags and fields; it does not echo --env-var values.
        sys.stderr.write((done.stderr or done.stdout)[-2000:])
        raise SystemExit(done.returncode)
    return done.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entra", help="JSON with ENTRA_TENANT_ID, ENTRA_ISSUER, ENTRA_AUTHORITY, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET")
    args = ap.parse_args()

    info = json.loads(render("postgres", "get", "easyapply-staging-db", "--include-sensitive-connection-info", "-o", "json"))
    info = info.get("data", info)
    database_url = info["connectionInfo"]["internalConnectionString"]

    env = {
        # both processes
        "AUTH_MODE": "entra",
        "IDENTITY_ASSERTION_SECRET": secrets.token_urlsafe(48),
        "OPERATOR_API_TOKEN": secrets.token_urlsafe(48),
        # the API
        "ENVIRONMENT": "production",
        "DATABASE_URL": database_url,
        "STORAGE_BACKEND": "database",
        "RESUME_QUEUE_BACKEND": "thread",
        "RESUME_QUEUE_MAX_WORKERS": "1",
        "TAILORING_QUEUE_BACKEND": "thread",
        "TAILORING_QUEUE_MAX_WORKERS": "1",
        "RESUME_AI_ENABLED": "false",
        "JOB_AI_ENABLED": "false",
        "TAILORING_AI_ENABLED": "false",
        # the web app
        "NEXT_TELEMETRY_DISABLED": "1",
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "API_BASE_URL": "http://127.0.0.1:8000",
        "APP_ORIGIN": f"https://{ORIGIN_HOST}",
        "PROXY_ALLOWED_HOSTS": ORIGIN_HOST,
        "PROXY_ALLOWED_EXTENSION_IDS": "jjhciekcmhmngciccfgignmmlgjknakk",
    }
    if args.entra:
        with open(args.entra, encoding="utf-8") as handle:
            entra = json.load(handle)
        for name in ("ENTRA_TENANT_ID", "ENTRA_ISSUER", "ENTRA_AUTHORITY", "ENTRA_CLIENT_ID", "ENTRA_CLIENT_SECRET"):
            env[name] = entra[name]

    command = [
        "services", "create", "--confirm", "-o", "json",
        "--name", "easyapply-staging",
        "--type", "web_service",
        "--repo", "https://github.com/karrisubhash/easyapply",
        "--branch", "main",
        "--runtime", "docker",
        "--plan", "free",
        "--region", "virginia",
        "--health-check-path", "/api/health",
        "--auto-deploy=false",
    ]
    for name, value in env.items():
        command += ["--env-var", f"{name}={value}"]

    created = json.loads(render(*command))
    created = created.get("data", created)
    service = created.get("service", created)
    details = service.get("serviceDetails", {})
    print(json.dumps({
        "id": service.get("id"),
        "name": service.get("name"),
        "url": details.get("url"),
        "plan": details.get("plan"),
        "region": details.get("region"),
        "dashboard": service.get("dashboardUrl"),
        "env_names": sorted(env),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
