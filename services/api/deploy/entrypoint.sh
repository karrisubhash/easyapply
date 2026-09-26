#!/bin/sh
# Container entrypoint for the EasyApply API (services/api/Dockerfile).
#
# Deployment glue only — nothing here decides application behaviour:
#
#   1. DATABASE_URL. Hosted Postgres hands out postgres:// or postgresql:// URLs;
#      SQLAlchemy reads those as psycopg2, which this image does not ship. The
#      application's own form is postgresql+psycopg:// (services/api/.env.example),
#      so the scheme is rewritten here rather than in code. A connect timeout is
#      added when the URL has none, so an unreachable database fails a request
#      (and /health/ready) in seconds instead of hanging on the TCP default.
#   2. Storage. The platform mounts the persistent disk owned by root; the
#      storage root on it is handed to the unprivileged user before anything runs.
#   3. Privileges. Everything the container runs, runs as `easyapply`.
set -eu

case "${DATABASE_URL:-}" in
  postgres://*)   DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgres://}" ;;
  postgresql://*) DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgresql://}" ;;
esac
case "${DATABASE_URL:-}" in
  "" | *connect_timeout=*) ;;
  *\?*) DATABASE_URL="${DATABASE_URL}&connect_timeout=10" ;;
  *)    DATABASE_URL="${DATABASE_URL}?connect_timeout=10" ;;
esac
export DATABASE_URL

if [ "$(id -u)" = "0" ]; then
  if [ -n "${RESUME_STORAGE_ROOT:-}" ]; then
    mkdir -p "$RESUME_STORAGE_ROOT"
    chown easyapply:easyapply "$RESUME_STORAGE_ROOT"
    find "$RESUME_STORAGE_ROOT" ! -user easyapply -exec chown easyapply:easyapply {} + 2>/dev/null || true
  fi
  exec setpriv --reuid=10001 --regid=10001 --clear-groups "$@"
fi

exec "$@"
