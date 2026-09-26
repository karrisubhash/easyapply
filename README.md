# EasyApply

Monorepo for EasyApply.

## Layout

```
easyapply/
├── apps/
│   ├── web/                  # Next.js (later)
│   └── extension/            # Chrome extension (MV3): capture, match, tailor, prepare the form (Greenhouse, Lever)
├── services/
│   └── api/                  # FastAPI
├── packages/
│   └── shared/               # Shared types/contracts (later)
├── infrastructure/
│   └── terraform/            # Azure infrastructure (later)
├── docs/
├── tests/
├── .gitignore
├── README.md
└── docker-compose.yml
```

## Running it

You need **PostgreSQL** and **Node 20+**. Everything else is in the repo.

### First time only

```powershell
# 1. A database for the app to live in.
psql -U postgres -c "CREATE DATABASE easyapply"

# 2. Point the API at it, in services\api\.env:
#      DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5432/easyapply

# 3. Python dependencies and the schema.
cd services\api
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head

# 4. Node dependencies, from the repo root.
cd ..\..
npm install
```

The schema is built by migrations, never by `create_all`: the triggers and
partial indexes exist only in the migrations, so a database built any other way
would pass tests that production fails.

### Every time — two terminals

```powershell
# Terminal 1 — the API
cd services\api
.venv\Scripts\activate
uvicorn app.main:app --reload

# Terminal 2 — the web app, from the repo root
npm run dev
```

| | |
|---|---|
| Web app | http://localhost:3000 |
| API health | http://127.0.0.1:8000/health |
| OpenAPI docs | http://127.0.0.1:8000/docs |

The browser never calls the API directly — the web app proxies through
`/api/backend/*`, which is where the identity credential is attached
server-side. Start the API first, or the first page load answers 502 with "The
EasyApply API is unreachable", which is the designed behaviour rather than a
crash.

The web app decides who a request is for in one place,
`apps/web/src/server/auth/identity.ts`, and tells the API through one seam,
`services/api/app/api/identity.py`, which provisions the user on first sight.
`AUTH_MODE` picks how — in `apps/web/.env.local` for the web app and in
`services/api/.env` for the API, and the two must agree:

- `development` (the default, both tiers): everyone is `DEV_USER_EMAIL`. Change
  it to act as someone else — which is also how to check by hand that one user
  cannot reach another's data.
- `dev-session` (web; the API stays `development`): a per-browser session from a
  development sign-in form (any address, no password — not authentication).
  Needs `SESSION_SECRET`, at least 32 characters. The test suites' multi-user mode.
- `entra` (both tiers): real sign-in with **Microsoft Entra External ID** —
  "Sign in with Microsoft", authorization code with PKCE, the ID token validated
  by the web server, an HttpOnly EasyApply session, and a 60-second signed
  assertion to the API, which keys accounts by the Entra object id, never by
  email. The production mode; see `apps/web/.env.example` and
  `services/api/.env.example`. Verified locally against a provider shaped like
  External ID; **no real tenant has been configured or tested yet.**

Both the web UI and the Chrome extension present the same browser session and
never hold a credential. Design, session lifetime and logout, and the tenant
setup steps: [docs/authentication.md](docs/authentication.md).

### Security notes

That header is the whole of identity as far as the API is concerned: whoever
can reach the API can claim to be anyone, and in `development` mode whoever can
reach the web app is `DEV_USER_EMAIL`. Everything below exists so that stays
true only on your own machine. A `dev-session` session narrows who is attached;
it replaces none of this.

- **`npm run dev` listens on `127.0.0.1` only** (`next dev -H 127.0.0.1`), not
  on every interface, so nobody else on your network can load the app and have
  your identity attached for them. `http://localhost:3000` still works. A dev
  server started before this change keeps its old binding until restarted.
- **The proxy answers loopback hosts only.** `/api/backend/*` and `/api/auth/*` refuse (403
  `host_not_allowed`) any `Host` other than `localhost`, `127.0.0.1` or
  `[::1]`, on any port, which is what stops DNS rebinding. Reaching the web app
  under another name needs `PROXY_ALLOWED_HOSTS` (comma-separated hostnames,
  any port; empty by default) in `apps/web/.env.local`.
- **The proxy refuses cross-site writes.** Any request other than GET or HEAD must
  come from the app's own origin (`Sec-Fetch-Site: same-origin`, or an `Origin` naming the same
  host), else 403 `cross_site_request`. Browsers send these themselves; a
  hand-written `curl -X POST` must add an `Origin` naming the host it calls —
  `-H "Origin: http://127.0.0.1:3000"` for `http://127.0.0.1:3000/...`,
  `http://localhost:3000` for `http://localhost:3000/...`.
- **One exception, off by default: the Chrome extension's workflow.**
  `PROXY_ALLOWED_EXTENSION_IDS` (comma-separated extension ids, in
  `apps/web/.env.local`, empty by default) lets exactly `POST /api/backend/jobs`,
  `POST /api/backend/jobs/<id>/matches` and
  `POST /api/backend/jobs/<id>/tailored-resumes` through from
  `Origin: chrome-extension://<a listed id>` with
  `Sec-Fetch-Site: none` — what Chrome sends from an extension, and never from a
  web page. Every other write stays same-origin only, sign-in and sign-out
  included. The extension sends no identity; its request carries the browser's
  session cookie for the web app, and the proxy attaches that session's
  identity, as for the web UI. See [docs/authentication.md](docs/authentication.md).
- **The API guards itself the same way.** A page could skip the proxy and go
  straight to `127.0.0.1:8000`, so the API also answers loopback hosts only
  (403 `host_not_allowed`; add others with `API_ALLOWED_HOSTS` in
  `services/api/.env`), and refuses a POST/PUT/DELETE that carries a
  cross-site `Origin` or `Sec-Fetch-Site` (403 `cross_site_request`). The Next
  proxy, curl and scripts send neither and are unaffected. A deployment behind
  a gateway must list the hostname requests arrive with.
- **`ENVIRONMENT`** (`services/api/.env`): `development` (default), `test` or
  `production`. Under `production` the development identity answers **401 to
  every request**, so a deployment without `AUTH_MODE=entra` is refused rather
  than open. Every deployment must set it; any other value is a startup error.
- **The operator screens need a token outside development.** With
  `OPERATOR_API_TOKEN` unset, `/internal/*` is open (and logs a warning on every
  request) only under `ENVIRONMENT=development`, the default; under `test` or
  `production` it answers 403 until the token is set, to the same value, in both
  `services/api/.env` and `apps/web/.env.local`. It used to be open everywhere
  when unset. Use an ASCII value.

### Checking it

```powershell
cd services\api; pytest                       # backend
npm run test --workspace @easyapply/web       # frontend
npm run typecheck --workspace @easyapply/web
npm run lint --workspace @easyapply/web
npm run build --workspace @easyapply/web
npm run test:e2e --workspace @easyapply/web   # the browser, against the real stack

npm run typecheck --workspace @easyapply/extension
npm run test --workspace @easyapply/extension           # extension unit + flow tests
npm run test:browser --workspace @easyapply/extension   # the built extension in Chromium (stub web app)
npm run test:browser:real --workspace @easyapply/extension   # ...against the real API, database and web app
```

`pytest` needs PostgreSQL running: it drops and recreates `<your db>_test` from
the migrations at the start of every session, so it never touches your
development data.

`test:e2e` builds its own database, its own storage root and its own production
build, on ports of their own, so it can run beside a development server without
touching anything it is using. See
[docs/web-application-validation.md](docs/web-application-validation.md).

## Backend

```
Python -> FastAPI -> Pydantic -> SQLAlchemy -> PostgreSQL -> Alembic
```

## Frontend

```
Next.js -> TypeScript -> React -> Tailwind CSS -> shadcn/ui
```

### Typed API contract

`packages/shared/src/api-schema.ts` is generated from the API's OpenAPI schema
and must never be edited by hand. After changing a Pydantic schema, re-export
and regenerate:

```powershell
npm run api:types
```

## Resume Intelligence

Upload a PDF or DOCX resume; it is validated, stored through a storage
abstraction, extracted page by page (with an OCR fallback for scans),
interpreted into structured candidate data with per-field provenance, and
turned into a **draft** profile version for review.

See [docs/resume-intelligence.md](docs/resume-intelligence.md) for the domain
model, the processing state machine, the storage/OCR/AI boundaries, the
provenance design, duplicate-upload behaviour, security posture and the
migration paths to Azure Blob and a worker.

AI structuring is off by default (`RESUME_AI_ENABLED=false`), so a clean
checkout and the test suite never need an API key.

## Asynchronous processing

Uploading a resume returns as soon as the file is stored and a processing run
exists. The pipeline runs on a worker, so how long a resume takes to read has no
bearing on how long the request takes to answer.

```powershell
# Default: an in-process pool. Nothing extra to run.
uvicorn app.main:app --reload

# Or with a separate worker process:
$env:RESUME_QUEUE_BACKEND = "external"; uvicorn app.main:app
python -m app.worker
```

A worker claims a run by taking a database lease and renews it while it works.
If it dies, the lease expires, a reaper closes that attempt honestly and opens a
new one — so a killed process costs an attempt, never the document.

See [docs/async-resume-processing.md](docs/async-resume-processing.md) for the
lease design, the reaper, the retry classification, the AI provider boundary and
what is deliberately not in the logs.

### Evaluating the structurer

```powershell
cd services\api
python -m evaluation.runner
```

Scores a structurer against a small versioned dataset of awkward resume shapes.
It reports rather than asserts: see `services/api/evaluation/README.md`.

## Job Intelligence

A job posting as a domain: `POST /api/v1/jobs` accepts a normalized scrape (the
Chrome extension in `apps/extension` produces one from a Greenhouse posting; the
web app's job form posts the same payload), validates and
deterministically normalizes it, deduplicates by source identity, and
structures its description into required/preferred skills, experience,
education, certifications, responsibilities, technologies and qualifications —
every item with a citation back into the description.

Processing is synchronous today: no AI reads a job yet, so there is nothing
slow enough to need a queue. See
[docs/job-intelligence.md](docs/job-intelligence.md) for the identity policy,
the raw/normalized/structured split, the processing state machine, and the
security posture around treating a job posting as untrusted content — including
the prompt-injection boundary documented ahead of any prompt existing to audit
it against.

## Matching

Given a confirmed profile version and a job's completed structured
requirements, `POST /api/v1/jobs/{id}/matches` produces an explainable match
result: every requirement judged `matched`, `missing` or `uncertain`, each with
an explanation and — where a verdict rests on something — the profile rows that
justify it. `/jobs/[id]/match` shows it, evidence first.

Deterministic by design. No embeddings, no vector search, no LLM: the engine
exists to be a baseline whose mistakes are explainable, so that anything
semantic added later has something honest to be compared against. A result pins
the profile version, the requirements row, the algorithm version and the as-of
date, which is what makes it reproducible and what makes re-requesting it free.

Evidence is graded — declared and corroborated, demonstrated in paid work,
declared only, merely mentioned — and the bar a requirement sets is read from
its own wording, so "Kubernetes" and "Kubernetes in production" are not the
same question. `matching_v3` is the current rules; every change since v1 was
forced by the real-data benchmark rather than by the rule-level suite.

See [docs/matching.md](docs/matching.md) for the normalization rules and the
equivalences they deliberately refuse, the four experience rules, why a
bachelor's in an unrelated field is `uncertain` rather than `matched`, the
score formula and what it is not allowed to mean.

## Resume Tailoring

`POST /api/v1/jobs/{id}/tailored-resumes` turns a confirmed profile version into
a Word document aimed at one job, and then refuses to hand it over if it cannot
prove every sentence. Generation, grounding validation and rendering run on a
worker; `/jobs/[id]/tailor` starts it and `/tailored-resumes/[id]` reviews it,
line by line, against the profile text each line came from.

The default generator does not write sentences — it **selects** them, copying
clauses verbatim out of the snapshot and ordering them by how well they answer
the posting. So a clean checkout cannot fabricate a claim on a resume, because
it has no mechanism for emitting one. `TAILORING_AI_ENABLED` is off by default
and the AI generator is held to exactly the same grounding check — with one step
between the two: a model's document is narrowed to what the snapshot supports
before the verdict is stored, so one bad line costs the line rather than the
whole resume. That narrowing can only remove a claim or restore a copied field
to what the profile says, and grounding then runs again and still decides.

Grounding validation reads the document and the snapshot and decides
independently: an entry must name a real snapshot row, copied fields must be
copied, a citation must really be in the column it names, and a line may not
state a number or a name its source does not contain. Errors withhold the file;
warnings are shown and do not. A document that fails completes as a run
carrying its verdict and its findings, with **no artifact at all** — visible so
the user can see what was written and why it was refused.

See [docs/resume-tailoring.md](docs/resume-tailoring.md) for the four defences,
why grounding failure is a verdict rather than an error, why there is no
uniqueness constraint on a tailoring run, and why this domain has a read-side
stall check where the resume domain has a lease and a reaper.
[docs/llm-tailoring.md](docs/llm-tailoring.md) covers the model generator: why it
could not pass its own validator, what the narrowing pass may and may not do, the
frozen criteria a model has to meet before the flag can default on, and the
per-user rollout that exists so trying it is not an experiment run on everyone.

## Status

Profile domain, Profile REST API, the Resume Intelligence vertical slice with
reliable asynchronous processing behind it, Job Intelligence — the job domain,
its API, and a web form that posts the same payload the Chrome extension will —
the deterministic matching engine on top of both, and Resume Tailoring end to
end through a downloadable artifact.

The web application is usable end to end: dashboard, profile and versions,
resume upload and review, job capture and requirements, matching with evidence,
tailoring with grounding validation, and download.

The journey is covered by a browser suite that drives the real stack —
`apps/web/e2e/specs/` — and has been validated end to end; what it found and what
was done about it is in
[docs/web-application-validation.md](docs/web-application-validation.md).

The Chrome extension captures a job posting (Greenhouse-hosted boards and
Lever), shows exactly what will be saved, and saves it through the same
ingestion API; then shows the job's requirements, the backend's match of the
current confirmed profile version, starts and links a tailored resume, and on
the application form proposes answers from the profile for the person to
review — filling only approved, empty text fields, never a sensitive question,
and never submitting. Loading it: [apps/extension/README.md](apps/extension/README.md);
design and limitations: [docs/chrome-extension.md](docs/chrome-extension.md).

Not done: extension support beyond Greenhouse and Lever, an evaluation set for
the AI generator, a real Entra External ID tenant (real sign-in is built and
verified against a local provider only), and deployment.
