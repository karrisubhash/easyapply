# EasyApply — FREE-TIER STAGING image (docs/staging-deployment.md). Not production.
#
# One container, two processes, because the free tier has no private services:
#
#   Next.js   0.0.0.0:$PORT       the only thing the platform routes to
#   FastAPI   127.0.0.1:8000      loopback only — reachable from the web app's
#                                 proxy inside this container and from nowhere else
#
# Resume processing and tailoring run in the API process's own queues, and
# documents are kept in PostgreSQL (STORAGE_BACKEND=database) because the
# container filesystem does not survive a restart. Build context: the repo root.

# --- the web app, built -------------------------------------------------------
FROM node:22.17.0-bookworm-slim AS web
WORKDIR /repo
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY apps/extension/package.json apps/extension/package.json
COPY packages/shared/package.json packages/shared/package.json
RUN npm ci --no-audit --no-fund

COPY packages/shared packages/shared
COPY apps/web apps/web
# Not pruned to production dependencies: `next start` loads next.config.ts and
# needs TypeScript to do it (it tries to `npm install` it otherwise).
RUN npm run build --workspace @easyapply/web

# --- the runtime --------------------------------------------------------------
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

# tesseract: the OCR adapter shells out to it for image-only resume pages
# (it also brings the C++ runtime node needs). util-linux: setpriv, which the
# entrypoint uses to drop root.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app/api
# Constraints: the versions the API test suite last passed with.
COPY services/api/requirements.txt services/api/deploy/constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

COPY services/api/alembic.ini ./
COPY services/api/alembic ./alembic
COPY services/api/app ./app
COPY services/api/deploy/entrypoint.sh services/api/deploy/migrate_and_serve.py ./deploy/
# Compiled now, because at runtime they cannot be (read-only to the unprivileged
# user, and PYTHONDONTWRITEBYTECODE): on a fractional CPU, compiling the
# application from source on every start is most of a cold start.
RUN python -m compileall -q app alembic deploy
COPY --from=web /repo /app/web
COPY deploy/staging/start.sh /app/start.sh

RUN chmod 0755 /app/api/deploy/entrypoint.sh /app/start.sh \
    && groupadd --system --gid 10001 easyapply \
    && useradd --system --uid 10001 --gid easyapply --no-create-home --home-dir /nonexistent easyapply \
    && chown -R easyapply:easyapply /app/web/apps/web/.next

# The entrypoint rewrites DATABASE_URL for psycopg and drops to `easyapply`;
# start.sh migrates, starts the API, waits for it, then starts the web app.
ENTRYPOINT ["/app/api/deploy/entrypoint.sh"]
CMD ["/app/start.sh"]
