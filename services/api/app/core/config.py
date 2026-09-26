import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str

    # --- deployment ---------------------------------------------------------
    # What kind of place this process is running in. It decides two things,
    # both about seams that trust too much: whether the development identity
    # may run at all (it trusts a header — anywhere real people are, it would
    # let any caller be anyone), and whether an operator API with no token
    # configured is open or closed.
    #
    #   development  a developer's machine. The default, so a clean checkout
    #                behaves as it always has: the dev identity runs, and an
    #                unset OPERATOR_API_TOKEN leaves /internal/* open (logged).
    #   test         the automated suites. The dev identity runs; an unset
    #                operator token CLOSES /internal/* (403), as anywhere that
    #                is not a developer's own machine.
    #   production   anything reachable by people other than its developer.
    #                The development identity refuses EVERY request with a 401
    #                (see app/api/dev_identity.py), and an unset operator token
    #                closes /internal/*. Real sign-in is AUTH_MODE=entra; a
    #                production deployment without it is deliberately unusable
    #                rather than silently open.
    #
    # A closed set: a typo ("prod") is a validation error at startup, never a
    # silent fall-back to development. Case and surrounding whitespace are
    # forgiven, nothing else is.
    #
    # The default is development, not production, so that nothing changes for
    # a developer. The cost of that choice is that a deployment MUST set
    # ENVIRONMENT=production explicitly; forgetting to is the one way to ship
    # the development identity.
    ENVIRONMENT: Literal["development", "test", "production"] = "development"

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def _normalise_environment(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    # Development identity only. See app/api/dev_identity.py.
    DEV_USER_EMAIL: str = "dev@easyapply.local"

    # --- identity -----------------------------------------------------------
    # How a request's person is established. The seam is app/api/identity.py.
    #
    #   development  X-Dev-User-Email (app/api/dev_identity.py). The default.
    #                Believes a header, so it refuses EVERY request under
    #                ENVIRONMENT=production — deliberately unusable there.
    #   entra        the EasyApply web app's signed, 60-second assertion of a
    #                Microsoft Entra External ID sign-in it has validated
    #                (app/api/assertion_identity.py). X-Dev-User-Email is
    #                refused. Requires, and `identity_configuration_problems`
    #                checks at startup:
    #                  IDENTITY_ASSERTION_SECRET   >= 32 chars, shared with the
    #                                              web app (never the session
    #                                              secret); _PREVIOUS optional,
    #                                              accepted during a rotation
    #                  ENTRA_TENANT_ID             the tenant GUID (`tid`)
    #                  ENTRA_ISSUER                the tenant's token issuer
    #
    # A closed set: a typo is a validation error at startup, never a fall-back.
    AUTH_MODE: Literal["development", "entra"] = "development"
    IDENTITY_ASSERTION_SECRET: str = ""
    IDENTITY_ASSERTION_SECRET_PREVIOUS: str = ""
    ENTRA_TENANT_ID: str = ""
    ENTRA_ISSUER: str = ""
    # The Entra app role an operator's assertion must carry for /internal/*, on
    # top of OPERATOR_API_TOKEN (app/api/operator_identity.py).
    ENTRA_OPERATOR_ROLE: str = "EasyApply.Operator"

    @field_validator("AUTH_MODE", mode="before")
    @classmethod
    def _normalise_auth_mode(cls, value: object) -> object:
        value = value.strip().lower() if isinstance(value, str) else value
        if value == "dev-session":
            # A valid WEB value, and the likeliest mistake: one shell exporting
            # AUTH_MODE for both tiers. Say which is which.
            raise ValueError(
                '"dev-session" is a mode of the web app (apps/web), not of the API. '
                'The API\'s modes are "development" (for dev-session too) and "entra".'
            )
        return value

    # Hostnames, comma-separated (any port), the API answers for besides
    # loopback — see app/main.py BrowserRequestGuard. Empty by default: a
    # developer's API is reached as 127.0.0.1 or localhost, and refusing every
    # other Host is what stops DNS rebinding. A deployment behind a proxy or a
    # gateway MUST list the hostname requests arrive with.
    API_ALLOWED_HOSTS: str = ""

    # --- resume ingestion ---------------------------------------------------
    # Every limit is configuration, so tightening one is a deployment change
    # rather than a code change, and tests can shrink them without monkeypatching.

    # Where uploaded resumes and generated documents are kept (app/services/storage.py):
    #   filesystem  files under RESUME_STORAGE_ROOT. The default.
    #   database    rows of `stored_objects` in DATABASE_URL's database, for a
    #               deployment with no persistent disk and no object store (the
    #               free-tier staging environment, docs/staging-deployment.md).
    # A closed set: a typo is a validation error at startup, never a fall-back to
    # a disk that may not survive the next restart.
    STORAGE_BACKEND: Literal["filesystem", "database"] = "filesystem"

    @field_validator("STORAGE_BACKEND", mode="before")
    @classmethod
    def _normalise_storage_backend(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    # Where the local storage backend writes. Ignored by any other backend.
    RESUME_STORAGE_ROOT: str = "./var/resume-storage"

    # Hard ceiling on an upload. Enforced while streaming, before the whole
    # body is ever held in memory.
    RESUME_MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024

    # A PDF page yielding fewer characters than this is treated as image-only
    # and routed to OCR. Scanned pages typically yield 0-20 stray characters.
    RESUME_OCR_MIN_CHARS_PER_PAGE: int = 80

    # A document whose pages are ALL below the threshold needs OCR outright.
    RESUME_OCR_ENABLED: bool = True

    # Resolution for rasterising PDF pages before OCR. Higher is slower.
    RESUME_OCR_RENDER_SCALE: float = 2.0

    # --- asynchronous processing --------------------------------------------
    # How enqueued work reaches a worker:
    #   thread   — an in-process pool. The default: it makes processing
    #              asynchronous without adding infrastructure.
    #   external — enqueue is a no-op; a separate `python -m app.worker`
    #              process claims runs from the database. What a hosted queue
    #              will look like from the application's side.
    #   inline   — run on the calling thread. Diagnostics and tests only.
    RESUME_QUEUE_BACKEND: str = "thread"
    RESUME_QUEUE_MAX_WORKERS: int = 2

    # A lease is how a worker claims a run. It must outlast the slowest stage,
    # because letting it expire under a live worker invites a second one in.
    RESUME_LEASE_SECONDS: int = 300
    # Renewal cadence. Comfortably inside the lease so one missed beat is
    # survivable; 0 disables the heartbeat thread.
    RESUME_LEASE_HEARTBEAT_SECONDS: int = 60

    # Attempts per document, counting the first. Bounds automatic retries only;
    # a person asking for another attempt is not rate-limited by it.
    RESUME_MAX_ATTEMPTS: int = 3

    # --- retry backoff -------------------------------------------------------
    # A transient failure usually means something nearby is having a bad minute.
    # Retrying into that minute is how a rate limit becomes a rate-limit loop,
    # so each attempt waits longer than the last:
    #
    #   delay = min(BASE * MULTIPLIER ** (attempt - 2), MAX)
    #
    # `attempt` counts from 1 and attempt 1 is not a retry, so attempt 2 — the
    # first one — waits exactly one BASE. See app/services/resume/backoff.py.
    #
    # then jitter spreads it. With the defaults below, attempt 2 waits ~30s and
    # attempt 3 ~60s before jitter — long enough for a provider to recover, short
    # enough that a user watching the page does not conclude it is broken.
    RESUME_RETRY_BACKOFF_BASE_SECONDS: float = 30.0
    RESUME_RETRY_BACKOFF_MULTIPLIER: float = 2.0
    RESUME_RETRY_BACKOFF_MAX_SECONDS: float = 900.0

    # How much of the delay is randomised, as a fraction of it. The scheduled
    # delay always lands in [delay * (1 - JITTER), delay], so the ceiling is
    # exactly the formula above whatever this is set to.
    #
    #   0.0  no jitter — every run retrying after one outage stampedes together
    #   0.5  half fixed, half random (the default)
    #   1.0  full jitter — maximum spread, minimum predictability
    #
    # It exists because the failures worth retrying are usually shared: one
    # provider outage fails every in-flight resume at once, and without jitter
    # they would all come back at the same instant and fail together again.
    RESUME_RETRY_JITTER: float = 0.5

    # The reaper recovers runs whose worker went away.
    RESUME_REAPER_ENABLED: bool = True
    RESUME_REAPER_INTERVAL_SECONDS: int = 60
    # How long a never-claimed run may sit before it is assumed the enqueue was
    # lost — a process restart between committing the run and starting the work.
    RESUME_REAPER_UNCLAIMED_GRACE_SECONDS: int = 300

    # The standalone worker's polling loop.
    RESUME_WORKER_POLL_SECONDS: float = 5.0
    RESUME_WORKER_BATCH: int = 5

    # --- AI structuring ------------------------------------------------------
    # Disabled by default so a clean checkout, CI and the test suite never reach
    # for the network or a key; the deterministic structurer runs instead.
    RESUME_AI_ENABLED: bool = False
    # Which provider adapter satisfies StructuredOutputProvider. The application
    # depends on the protocol; this names the implementation.
    RESUME_AI_PROVIDER: str = "anthropic"
    RESUME_AI_MODEL: str = "claude-opus-5"
    RESUME_AI_MAX_TOKENS: int = 16000
    # Wall clock for one structuring call, and how many times the SDK may retry
    # its own transport-level failures before the adapter sees one.
    RESUME_AI_TIMEOUT_SECONDS: float = 120.0
    RESUME_AI_MAX_RETRIES: int = 2
    # Adaptive thinking. Deliberately no temperature/top_p settings: the Claude
    # models this adapter targets reject sampling parameters outright, so
    # offering the knob would be offering a 400.
    RESUME_AI_THINKING: bool = True

    ANTHROPIC_API_KEY: str | None = None

    # --- AI structuring (jobs) ------------------------------------------------
    # The mirror of the resume block above, kept as its own namespace rather
    # than shared flags: a deployment may want jobs structured by AI while
    # resumes stay deterministic (or vice versa) without one toggle coupling
    # both decisions together.
    JOB_AI_ENABLED: bool = False
    JOB_AI_PROVIDER: str = "anthropic"
    JOB_AI_MODEL: str = "claude-opus-5"
    # A posting is shorter than a resume and the schema is smaller, so the
    # ceiling is lower than RESUME_AI_MAX_TOKENS — configuration, not a shared
    # constant, so the two can be tuned independently as real usage is seen.
    JOB_AI_MAX_TOKENS: int = 8000
    JOB_AI_TIMEOUT_SECONDS: float = 60.0
    JOB_AI_MAX_RETRIES: int = 2
    JOB_AI_THINKING: bool = True

    # --- resume tailoring ----------------------------------------------------
    # Its own namespace, like the two AI blocks above and for the same reason: a
    # deployment may want jobs structured deterministically while resumes are
    # tailored by a model, or the reverse, without one flag deciding both.
    #
    # Disabled by default. With AI off, `DeterministicTailor` runs — it SELECTS
    # clauses out of the profile and never writes a new one, so the default
    # configuration of this application cannot fabricate a claim on a resume.
    # See app/services/tailoring/generation.py.
    TAILORING_AI_ENABLED: bool = False

    # A narrower rollout than the flag above, for the interval between "the
    # layer is built" and "we know how often it works": a comma-separated list
    # of user ids whose tailoring runs use the AI generator while
    # TAILORING_AI_ENABLED stays off for everybody else.
    #
    # It exists because the alternative ways to get a model's output in front of
    # a real profile are both worse. Turning the flag on globally to try it is
    # an experiment run on every user at once. Never trying it outside the
    # evaluation dataset means the first real profile it sees is everyone's.
    #
    # Empty by default, which is the same behaviour as not having it. It is not
    # an authorization boundary and nothing may treat it as one: it decides
    # which generator writes a document, never who may read one.
    TAILORING_AI_USER_IDS: str = ""

    TAILORING_AI_PROVIDER: str = "anthropic"
    TAILORING_AI_MODEL: str = "claude-opus-5"
    # A resume is longer than a job posting's requirements and shorter than a
    # raw resume reading, and it is generated rather than extracted — so the
    # ceiling sits between the other two rather than being shared with either.
    TAILORING_AI_MAX_TOKENS: int = 12000
    TAILORING_AI_TIMEOUT_SECONDS: float = 120.0
    TAILORING_AI_MAX_RETRIES: int = 2
    TAILORING_AI_THINKING: bool = True

    # OpenRouter, as a second provider for the tailoring domain only
    # (TAILORING_AI_PROVIDER=openrouter). There is deliberately no OpenRouter
    # model default: TAILORING_AI_MODEL must be set explicitly alongside it,
    # because the Claude default above is not an OpenRouter model id and a
    # silently substituted one would make an evaluation measure the wrong thing.
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # How a tailoring run reaches a worker. The same three backends the resume
    # domain offers, chosen independently: tailoring is a different shape of
    # work (CPU-light, provider-bound) and may want a different pool size, or
    # may want to run inline in an environment where resume processing does not.
    #   thread   — an in-process pool. The default.
    #   inline   — run on the calling thread. Diagnostics and tests only.
    TAILORING_QUEUE_BACKEND: str = "thread"
    TAILORING_QUEUE_MAX_WORKERS: int = 2

    # How long a non-terminal run may go without finishing before the read model
    # reports it as stalled and offers a retry.
    #
    # Deliberately a READ-SIDE judgement rather than a lease-and-reaper like the
    # resume domain's. The difference is what a lost run costs: a resume the
    # reaper never recovers is an upload the user believes is being read, while a
    # tailoring run that dies is a document the user can simply ask for again —
    # the inputs are still there and nothing was consumed. Paying for a second
    # lease table, heartbeat thread and reaper loop to automate a retry the user
    # can perform in one click is not a trade this milestone makes. It is a
    # trade a later one might; the run table is shaped so that adding the lease
    # columns would not change anything above this line.
    TAILORING_STALE_AFTER_SECONDS: int = 600

    # --- operator API --------------------------------------------------------
    # An internal surface for "show me resume processing that needs attention".
    # It reads across ALL users, which is why it has its own boundary rather
    # than reusing the caller's identity — see app/api/operator_identity.py for
    # what that boundary does and, more importantly, does not enforce.
    #
    # Disabled means 404, not 403: an operator surface that is switched off
    # should not confirm it exists.
    OPERATOR_API_ENABLED: bool = True
    # A shared secret in X-Operator-Token. Unset — the default — leaves the
    # boundary open in ENVIRONMENT=development only (with a warning per
    # request) and CLOSES it everywhere else: every operator request is a 403
    # until a token is configured, here and as OPERATOR_API_TOKEN in
    # apps/web/.env.local so the proxy can attach it. It used to be open
    # everywhere, which meant a deployment that forgot to set it served a
    # cross-user surface to anyone. It is not a
    # substitute for authorization; it is a placeholder shaped like one, and the
    # endpoints expose only ids, counts and codes precisely because of that.
    OPERATOR_API_TOKEN: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def identity_configuration_problems(config: "Settings") -> list[str]:
    """What is wrong with the identity configuration, by setting NAME — never a value.

    Checked at startup (app/main.py) so a misconfigured deployment does not come
    up at all, and again per request by the identity path itself, so nothing
    assembled around the check can route a request past it.
    """
    problems: list[str] = []
    if config.AUTH_MODE == "entra":
        if len(config.IDENTITY_ASSERTION_SECRET) < 32:
            problems.append("IDENTITY_ASSERTION_SECRET must be at least 32 characters")
        if config.IDENTITY_ASSERTION_SECRET_PREVIOUS and len(config.IDENTITY_ASSERTION_SECRET_PREVIOUS) < 32:
            problems.append("IDENTITY_ASSERTION_SECRET_PREVIOUS, when set, must be at least 32 characters")
        if not _GUID.fullmatch(config.ENTRA_TENANT_ID):
            problems.append("ENTRA_TENANT_ID must be the tenant's GUID")
        issuer = urlsplit(config.ENTRA_ISSUER)
        loopback = issuer.hostname in ("localhost", "127.0.0.1")
        if not (issuer.scheme == "https" or (issuer.scheme == "http" and loopback)) or not issuer.hostname:
            problems.append("ENTRA_ISSUER must be the tenant's https token issuer")
        elif config.ENTRA_TENANT_ID and config.ENTRA_TENANT_ID.lower() not in config.ENTRA_ISSUER.lower():
            problems.append("ENTRA_ISSUER must name ENTRA_TENANT_ID")
        if config.ENVIRONMENT == "production" and loopback:
            problems.append("ENTRA_ISSUER cannot be a loopback address in production")
    return problems


settings = Settings()
