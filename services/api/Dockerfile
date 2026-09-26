# EasyApply API — the container a hosted deployment runs (docs/staging-deployment.md).
#
# One image, two uses: the API process (the default command) and the release
# step that migrates the database before it serves (`deploy/entrypoint.sh
# alembic upgrade head`). Resume processing and tailoring run in the API
# process's own queues (RESUME_QUEUE_BACKEND / TAILORING_QUEUE_BACKEND=thread);
# `python -m app.worker` runs from the same image if a deployment ever gives
# the worker its own service and shared storage.

FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tesseract: the OCR adapter shells out to it for image-only resume pages.
# util-linux provides setpriv, which the entrypoint uses to drop root.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The versions the test suite last passed with, as constraints: requirements.txt
# stays the list of direct dependencies, and the image cannot drift from what
# was tested by resolving a newer release at build time.
COPY requirements.txt deploy/constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh

RUN chmod 0755 ./deploy/entrypoint.sh \
    && groupadd --system --gid 10001 easyapply \
    && useradd --system --uid 10001 --gid easyapply --no-create-home --home-dir /nonexistent easyapply

# Starts as root only so the entrypoint can hand the mounted storage disk to the
# unprivileged user; everything the image runs, runs as that user.
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-10000}\""]
