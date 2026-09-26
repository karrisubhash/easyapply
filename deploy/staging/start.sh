#!/bin/bash
# Free-tier staging: both processes in one container (deploy/staging/Dockerfile).
#
#   1+2. Migrate, then serve the API on loopback, in one interpreter
#      (services/api/deploy/migrate_and_serve.py). The free tier has no
#      pre-deploy step, so the release step runs here, before anything serves;
#      a failed upgrade exits the container and the platform keeps the previous
#      deploy. Wait until the API is listening, so the web app never serves a first
#      request into a proxy with nothing behind it.
#   3. Start the web app on $PORT.
#   4. If either process exits, stop the other and exit with its status: a
#      container with half an application must not look alive to the platform.
set -euo pipefail

API_PORT="${API_PORT:-8000}"

cd /app/api
API_HOST=127.0.0.1 API_PORT="$API_PORT" python deploy/migrate_and_serve.py &
api=$!

for _ in $(seq 1 300); do
  # bash's own /dev/tcp rather than a probe process: on a fractional CPU a
  # Python interpreter per second competes with the start it is waiting for.
  # uvicorn opens the port only once application startup has completed.
  if (exec 3<>"/dev/tcp/127.0.0.1/${API_PORT}") 2>/dev/null; then
    break
  fi
  if ! kill -0 "$api" 2>/dev/null; then
    echo "staging.start api_exited_during_startup" >&2
    wait "$api" || exit $?
    exit 1
  fi
  sleep 1
done

cd /app/web/apps/web
node /app/web/node_modules/next/dist/bin/next start --hostname 0.0.0.0 --port "${PORT:-10000}" &
web=$!

stop() {
  kill -TERM "$api" "$web" 2>/dev/null || true
  wait "$api" "$web" 2>/dev/null || true
}
trap 'stop; exit 143' TERM INT

set +e
wait -n "$api" "$web"
status=$?
set -e
echo "staging.start process_exited status=${status}" >&2
stop
exit "$status"
