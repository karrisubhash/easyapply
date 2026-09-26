import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.api.deps import DbSession
from app.api.errors import HANDLERS
from app.api.v1 import api_router
from app.core.config import identity_configuration_problems, settings
from app.services.resume.queue import build_queue
from app.services.resume.reaper import ReaperLoop
from app.services.tailoring.queue import build_tailoring_queue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the process-wide processing machinery.

    Each queue holds a thread pool and the reaper holds a timer, so all three
    are per-process and all three have to be shut down — a pool that outlives
    the application keeps handing runs to a worker whose database is going away.

    Two queues, not one: resume processing and tailoring carry different work
    (see app/services/tailoring/queue.py) and a stuck resume must not starve a
    tailoring request.

    Started here rather than at import time so that importing the app (for a
    migration, for a test, for `--help`) does not silently begin processing.

    The identity configuration is checked first, here and not at import, for
    the same reason: importing the app must not depend on the developer's .env
    (the test suite pins its own settings after import), but SERVING it must.
    A broken configuration fails startup and uvicorn exits.
    """
    check_identity_configuration()
    app.state.processing_queue = build_queue()
    app.state.tailoring_queue = build_tailoring_queue()
    app.state.reaper = ReaperLoop(queue=app.state.processing_queue)
    app.state.reaper.start()
    logger.info(
        "processing.started",
        extra={
            "queue_backend": settings.RESUME_QUEUE_BACKEND,
            "tailoring_queue_backend": settings.TAILORING_QUEUE_BACKEND,
            "reaper_enabled": settings.RESUME_REAPER_ENABLED,
        },
    )
    try:
        yield
    finally:
        app.state.reaper.stop()
        app.state.processing_queue.shutdown(wait=True)
        app.state.tailoring_queue.shutdown(wait=True)
        logger.info("processing.stopped")


#: The resume upload route, as the ASGI path the request arrives on.
_RESUME_UPLOAD_PATH = "/api/v1/resumes"

#: Room for what multipart wraps around the file: the boundary lines and the
#: part's own headers, filename included. Generous, because refusing a file at
#: exactly the limit because its filename was long would be a wrong answer;
#: the handler's own count of the file's bytes is still the exact check.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024


class ResumeUploadSizeLimit:
    """Refuse an oversized resume upload before its body is read.

    The handler counts the file's bytes as it reads them, but by the time it
    runs FastAPI has already parsed the whole multipart form — so a 2 GB upload
    was spooled to disk in full just to be told it was too large. This sits in
    front of that: a declared Content-Length over the limit is answered 413
    without receiving a byte of the body, and a body with no Content-Length
    (chunked) is counted as it streams and cut off once it passes the limit.

    Scoped to the one route that takes files. It is pure ASGI rather than a
    `BaseHTTPMiddleware` because the point is to act before the body is read,
    and that wrapper reads it.
    """

    def __init__(self, app) -> None:
        self.app = app

    @staticmethod
    def _limit() -> int:
        return settings.RESUME_MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD_BYTES

    @staticmethod
    async def _refuse(send) -> None:
        limit = settings.RESUME_MAX_UPLOAD_BYTES
        body = json.dumps(
            {
                # The same shape the handler's own 413 has, so a client reads
                # one answer whichever of the two refused it.
                "detail": {
                    "detail": f"The file is larger than the {limit // (1024 * 1024)} MB limit.",
                    "code": "file_too_large",
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path", "").rstrip("/") != _RESUME_UPLOAD_PATH
        ):
            await self.app(scope, receive, send)
            return

        limit = self._limit()
        declared = next(
            (value for name, value in scope.get("headers", []) if name == b"content-length"),
            None,
        )
        if declared is not None:
            try:
                too_large = int(declared) > limit
            except ValueError:
                too_large = False  # malformed: the server's own parser answers it
            if too_large:
                await self._refuse(send)
                return
            await self.app(scope, receive, send)
            return

        # No declared length: count what actually arrives.
        received = 0
        refused = False

        async def counted_receive():
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit and not refused:
                    refused = True
                    await self._refuse(send)
                    # Tell the application the client is gone, so it stops
                    # reading and nothing further is spooled.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            # After our 413 the response is finished; whatever the application
            # tries to send about the body it never got is dropped.
            if not refused:
                await send(message)

        await self.app(scope, counted_receive, guarded_send)


#: Hostnames the API answers for whatever the configuration says: loopback, and
#: the name Starlette's TestClient sends.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1", "testserver"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _hostname(host: str) -> str:
    """"127.0.0.1:8000" -> "127.0.0.1"; "[::1]:8000" -> "[::1]"; lower-cased."""
    host = host.strip().lower()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class BrowserRequestGuard:
    """Refuse the two browser attacks the identity seam is exposed to.

    The development identity is a header the API believes (app/api/dev_identity.py),
    and a request without one is the configured developer. The Next proxy
    already refuses cross-site writes and foreign Hosts, but the API itself
    listens on 127.0.0.1:8000 too, and a page anywhere could go straight to it:

      * DNS rebinding — a hostile domain re-pointed at 127.0.0.1 is SAME-ORIGIN
        with the API, and could send any identity header and read the answer.
        Refused by Host: only loopback (plus TestClient's name and an explicit
        API_ALLOWED_HOSTS list) is answered, on every method.
      * Cross-site request forgery — a form on any site can POST to the API
        (multipart and body-less requests need no preflight) and act as the
        developer. Refused for state-changing methods when the request carries
        browser provenance that is not same-origin: an Origin naming another
        host, or a Sec-Fetch-Site other than same-origin/none. A request with
        neither is not from a web page — the Next proxy, curl, a worker — and
        is let through; nothing a hostile page can send lacks both.

    Pure ASGI, like ResumeUploadSizeLimit, so it answers before the body is
    read.
    """

    def __init__(self, app) -> None:
        self.app = app

    @staticmethod
    def _allowed_host(host: str | None) -> bool:
        if not host:
            return False
        name = _hostname(host)
        extra = {
            _hostname(entry)
            for entry in settings.API_ALLOWED_HOSTS.split(",")
            if entry.strip()
        }
        return name in _LOOPBACK_HOSTS or name in extra

    @staticmethod
    def _same_origin(headers: dict[bytes, bytes], host: str) -> bool:
        site = headers.get(b"sec-fetch-site")
        if site is not None and site.decode("latin-1").lower() not in {"same-origin", "none"}:
            return False
        origin = headers.get(b"origin")
        if origin is None:
            return True
        value = origin.decode("latin-1").strip().lower()
        if value == "null" or "://" not in value:
            return False
        return value.split("://", 1)[1].rstrip("/") == host.strip().lower()

    @staticmethod
    async def _refuse(send, detail: str, code: str) -> None:
        body = json.dumps({"detail": detail, "code": code}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {name.lower(): value for name, value in scope.get("headers", [])}
        host = headers.get(b"host", b"").decode("latin-1")
        if not self._allowed_host(host):
            await self._refuse(send, "This API does not answer for that host.", "host_not_allowed")
            return
        if scope.get("method", "GET").upper() not in _SAFE_METHODS and not self._same_origin(
            headers, host
        ):
            await self._refuse(send, "Cross-site requests are not accepted.", "cross_site_request")
            return
        await self.app(scope, receive, send)


def check_identity_configuration() -> None:
    """Refuse to start with an identity configuration that cannot work.

    Names what is wrong, never a value. A production process still running the
    development identity starts (its /health, migrations and workers do not
    authenticate anybody) but refuses every identified request — see
    app/api/dev_identity.py — and says so here, once, at startup.
    """
    problems = identity_configuration_problems(settings)
    if problems:
        raise RuntimeError("Identity is misconfigured: " + "; ".join(problems) + ".")
    if settings.ENVIRONMENT == "production" and settings.AUTH_MODE != "entra":
        logger.error("identity.production_without_sign_in", extra={"auth_mode": settings.AUTH_MODE})
    logger.info("identity.mode", extra={"auth_mode": settings.AUTH_MODE, "environment": settings.ENVIRONMENT})


app = FastAPI(
    title="EasyApply API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(ResumeUploadSizeLimit)
# Added last, so it runs first: nothing — not even the upload size check — is
# done for a request the API will not answer.
app.add_middleware(BrowserRequestGuard)

for exception_type, handler in HANDLERS:
    app.add_exception_handler(exception_type, handler)

app.include_router(api_router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "easyapply-api"
    }


_API_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _migration_heads() -> frozenset[str]:
    """The revisions this code expects the database to be at, from its own scripts."""
    from alembic.script import ScriptDirectory

    return frozenset(ScriptDirectory(str(_API_ROOT / "alembic")).get_heads())


@app.get("/health/ready")
def readiness_check(request: Request, response: Response, db: DbSession):
    """Whether this process can do its job, as opposed to merely being up.

    `/health` answers while the process runs; this answers only when the
    things a request needs are there too, each reported on its own so an
    outage names its cause:

      database    a round trip succeeds
      schema      the database is at this code's migration head — an API
                  serving a schema it was not written for is not ready, it
                  is about to fail in a way no request can explain
      processing  the in-process queues exist and the reaper is running

    Any of them failing is a 503. Values are fixed words, never an exception
    message: those can carry hostnames and connection details.
    """
    checks = {"database": "ok", "schema": "ok", "processing": "ok"}
    try:
        current = frozenset(
            row[0] for row in db.execute(text("SELECT version_num FROM alembic_version"))
        )
    except ProgrammingError:
        # Reachable, but never migrated: there is no alembic_version table.
        checks["schema"] = "missing"
    except Exception as exc:
        logger.warning("health.database_unavailable", extra={"error": type(exc).__name__})
        checks["database"] = "unavailable"
        checks["schema"] = "unknown"
    else:
        if current != _migration_heads():
            checks["schema"] = "mismatch"

    state = request.app.state
    reaper = getattr(state, "reaper", None)
    if (
        getattr(state, "processing_queue", None) is None
        or getattr(state, "tailoring_queue", None) is None
        or reaper is None
        or not reaper.is_healthy
    ):
        checks["processing"] = "stopped"

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "service": "easyapply-api",
        "checks": checks,
    }
