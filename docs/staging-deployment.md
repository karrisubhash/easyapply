# Staging deployment — Render free tier (2026-09-26)

A public HTTPS environment for external user testing. **It is staging, not
production**: it runs entirely on free resources, and §14 lists what that costs.
Nothing here is a claim of production readiness.

> **Status 2026-09-26: provisioned, NOT live — blocked on Entra.** The repo,
> database and web service exist and the image builds and migrates on Render,
> but the service refuses to start without Entra configuration (by design,
> §6), so the public URL serves nothing yet. Microsoft has withdrawn the
> no-card External ID trial; a tenant now needs an Azure account, and the owner
> chose to stop here for now. §15 is the exact list of what remains.

| | |
|---|---|
| Public URL | **https://easyapply-staging.onrender.com** |
| Platform | Render (free web service + free Postgres), region `virginia` (US East) |
| Source | private GitHub repo `karrisubhash/easyapply`, branch `main` |
| Render service | `easyapply-staging` — `srv-dartamojo6nc739brjl0` |
| Render database | `easyapply-staging-db` — `dpg-dart3s3ncjis73f0hb3g-a`, PostgreSQL 18, **expires 2026-10-26** |
| Identity | Microsoft Entra External ID (`AUTH_MODE=entra`) — tenant in §6 |
| Extension | unpacked staging build, id `jjhciekcmhmngciccfgignmmlgjknakk` (§7) |
| Blueprint | [`render.yaml`](../render.yaml) (validated with `render blueprints validate`) |

## 1. Why this platform and this shape

The brief asked for managed services, HTTPS, a database, persistent document
storage, workers and a public URL, as fast as possible; the constraint added
afterwards was **free resources only**. Render was chosen by the owner; its free
tier decides the shape:

| Need | Free-tier fact | What staging does |
|---|---|---|
| Next.js, public HTTPS | Free web services get `*.onrender.com` with managed TLS | One free web service (Docker) |
| FastAPI not publicly reachable | No private services on free | The API runs **in the same container on 127.0.0.1:8000**; only Next's `$PORT` is routed |
| PostgreSQL | One free Postgres per workspace, 1 GB, **deleted after 30 days**, no backups | Free Postgres, `ipAllowList: []` (no external connections at all) |
| Persistent documents | No disks on free; the container filesystem is wiped on every restart and every idle spin-down | Documents stored **in Postgres** (`STORAGE_BACKEND=database`, §4) |
| Workers | No background workers on free | Resume processing and tailoring run in the API process's own queues — the application's default design (`RESUME_QUEUE_BACKEND=thread`, `TAILORING_QUEUE_BACKEND=thread`); leases + reaper recover work interrupted by a restart |
| Release step | No pre-deploy command on free | `alembic upgrade head` runs at container start, before anything serves; failure exits the container |
| Redis | — | **Not provisioned.** No application code reads Redis (checked: no client, no import, no setting). See §14 for where it would matter. |

What was deliberately *not* done: no AKS, no Terraform, no Azure hosting, no
redesign. Product logic — matching, extraction, tailoring, prompts, the
extension workflow, the auth architecture, profile and job models — is unchanged.

## 2. Architecture

```
 tester's browser / Chrome extension
          │  https://easyapply-staging.onrender.com   (Render edge: TLS, HTTP→HTTPS)
          ▼
 ┌──────────── easyapply-staging  (free web service, one container) ────────────┐
 │  Next.js  0.0.0.0:$PORT                                                       │
 │    /api/auth/*        sign-in with Entra (PKCE), session cookie               │
 │    /api/backend/*     proxy: Host check → Origin/extension admission →        │
 │                       identity → 60 s signed assertion to the API             │
 │    /api/health, /api/health/ready                                             │
 │          │ http://127.0.0.1:8000 (loopback — unreachable from outside)        │
 │          ▼                                                                    │
 │  FastAPI  127.0.0.1:8000   AUTH_MODE=entra, ENVIRONMENT=production            │
 │    resume queue (thread) · tailoring queue (thread) · reaper                  │
 └───────────────────────────────────────┬──────────────────────────────────────┘
                                         │ Render private network (internal URL)
                                         ▼
                      easyapply-staging-db  (free Postgres 18, no external access)
                        application tables + stored_objects (documents)
```

Entra External ID (Microsoft-hosted) is reached by the browser for sign-in and
by Next.js server-side for discovery, JWKS and token redemption.

Files: [`Dockerfile`](../Dockerfile) (repo root; two stages — build the web app
on `node:22.17.0`, run on `python:3.13-slim` with node copied in),
[`deploy/staging/start.sh`](../deploy/staging/start.sh) (supervision),
[`services/api/deploy/entrypoint.sh`](../services/api/deploy/entrypoint.sh)
(DATABASE_URL scheme, drop to uid 10001),
[`services/api/deploy/migrate_and_serve.py`](../services/api/deploy/migrate_and_serve.py)
(migrate + serve in one interpreter), [`.dockerignore`](../.dockerignore),
[`deploy/staging/render_create_service.py`](../deploy/staging/render_create_service.py)
(creates the service with generated secrets — how this one was made), and
[`deploy/staging/smoke_flow.py`](../deploy/staging/smoke_flow.py) (the workflow
smoke test through the proxy).

`start.sh`: migrate and start the API → wait until it listens → start Next →
if either process exits, stop the other and exit with its status, so the
platform restarts a container that is half an application instead of calling it
healthy.

## 3. Code changes made for deployment (and only those)

| Change | Why it was necessary | Where |
|---|---|---|
| API `GET /health/ready` (database round trip, schema at migration head, processing queues + reaper alive → 200/503, fixed words only) | The brief requires alive ≠ database available ≠ worker operational; `/health` checked nothing | `services/api/app/main.py`; `ReaperLoop.is_healthy` in `app/services/resume/reaper.py`; tests `tests/test_health_readiness.py` (11) |
| Web `GET /api/health` (liveness, no dependencies) and `GET /api/health/ready` (auth configuration + the API's checks relayed) | Render needs a liveness path that an API outage does not fail; readiness must name the failing part | `apps/web/src/app/api/health/**`; tests `health-routes.test.ts` (8) |
| `DatabaseStorage` + `STORAGE_BACKEND` setting + migration `a7c3e5f19b20` (`stored_objects`) | Free tier has no persistent disk; files written by the existing filesystem backend vanish on every restart/spin-down. A new class behind the existing `StorageBackend` interface, selected by configuration; the default stays `filesystem` | `app/services/storage.py`, `app/models/stored_object.py`, `alembic/versions/a7c3e5f19b20_stored_objects.py`, `app/core/config.py`; tests `test_storage_database.py` (16), `test_migration_stored_objects.py` (4) |
| `test_migration_user_identities.py` upgrades to its own revision instead of `head` | It asserted "head == f1a9d3c5b7e2", which any later migration breaks; the test is about that revision | 3 lines |
| `.gitignore` excludes `services/api/var/`, dumps, Playwright output | `var/` holds real uploaded resumes and was not ignored | `.gitignore` |

Nothing else in application code changed. The web proxy's `API_BASE_URL`
fallback (`http://127.0.0.1:8000`) was left as it is: in this container that is
exactly where the API is, and elsewhere an unset value fails as
`api_unreachable`, never as a silent success.

## 4. Documents (storage)

`STORAGE_BACKEND=database` stores each object as one row of `stored_objects`
(key = the existing content-addressed storage key, `content_type`, `data`,
`size_bytes` checked against the data). Each operation is its own short
transaction, so a `put` is durable before the row that records its key commits —
the same semantics as a file. The anti-path-traversal key rule is the filesystem
backend's, checked on every operation. Downgrading the migration refuses while
any object exists.

Verified: upload → processing → tailored DOCX → download inside the container
under free-tier limits; then the container was **deleted and recreated** (fresh
filesystem) and the DOCX re-downloaded byte-identical (same SHA-256).

Cost of the choice: documents count against the 1 GB free database and are lost
with it (§14). A real object store is the intended production backend.

## 5. Environment variables

Set on the one service (both processes read the same environment). Secret
values are generated at creation (`secrets.token_urlsafe(48)`) and exist only in
Render; they are not in git, this document, or any file.

| Variable | Value / source | Read by |
|---|---|---|
| `AUTH_MODE` | `entra` | web + API |
| `IDENTITY_ASSERTION_SECRET` | secret (generated) | web + API (same value by design) |
| `OPERATOR_API_TOKEN` | secret (generated) | web + API |
| `ENTRA_TENANT_ID`, `ENTRA_ISSUER` | from the tenant (§6) | web + API |
| `ENVIRONMENT` | `production` (development identity refused, operator API closed without token) | API |
| `DATABASE_URL` | Render **internal** connection string (rewritten to `postgresql+psycopg://` and given `connect_timeout=10` by the entrypoint) | API |
| `STORAGE_BACKEND` | `database` | API |
| `RESUME_QUEUE_BACKEND` / `_MAX_WORKERS` | `thread` / `1` | API |
| `TAILORING_QUEUE_BACKEND` / `_MAX_WORKERS` | `thread` / `1` | API |
| `RESUME_AI_ENABLED`, `JOB_AI_ENABLED`, `TAILORING_AI_ENABLED` | `false` — deterministic only; no model key is configured anywhere | API |
| `SESSION_SECRET` | secret (generated) | web |
| `API_BASE_URL` | `http://127.0.0.1:8000` (same container) | web |
| `APP_ORIGIN` | `https://easyapply-staging.onrender.com` | web |
| `PROXY_ALLOWED_HOSTS` | `easyapply-staging.onrender.com` | web |
| `ENTRA_AUTHORITY`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | from the tenant (§6); the secret is server-side only | web |
| `PROXY_ALLOWED_EXTENSION_IDS` | `jjhciekcmhmngciccfgignmmlgjknakk` | web |
| `NEXT_TELEMETRY_DISABLED` | `1` | web |

Render's CLI cannot edit a service's environment after creation, so a change is
made by re-creating the service (it is stateless — everything is in the
database) with [`deploy/staging/render_create_service.py`](../deploy/staging/render_create_service.py),
or in the dashboard (Service → Environment).

## 6. Entra External ID

**No tenant exists.** What was tried, in order:

1. `az login` as the owner's personal Microsoft account succeeded, but the
   account belongs to **no Azure directory** (`az account list --all` empty with
   `--allow-no-subscriptions`), so there is nowhere to create a tenant.
2. `https://aka.ms/ciam-free-trial` (the documented no-subscription trial) now
   redirects to the Azure free-account sign-up page.
3. The Microsoft Entra External ID VS Code extension (`ms-azuretools.ms-entra`
   1.0.10) "Set up a free trial": its log shows
   `PUT https://main.b2cadmin.ext.azure.com/api/tenants/CreateCIAMTrialDirectoryAsync/easyapplystaging.onmicrosoft.com`
   → **HTTP 404 "The resource you are looking for has been removed"**
   (2026-09-26 17:12 UTC).

So an External ID tenant now requires an Azure subscription. The Azure free
account asks for a card and phone number for verification; External ID's core
offer is free for the first 50,000 monthly active users, and nothing else in
Azure is needed. Using another identity provider, or `dev-session` for the
tester, was ruled out: the brief forbids changing the authentication
architecture and forbids dev-session for external testers.

Until Entra is configured the deployment **fails closed**, verified on Render:
the API's startup check (`identity_configuration_problems`,
`services/api/app/core/config.py`) raised *"Identity is misconfigured:
ENTRA_TENANT_ID must be the tenant's GUID; ENTRA_ISSUER must be the tenant's
https token issuer"*, `start.sh` stopped the container, and Render marked the
deploy `update_failed` — nothing was ever served.

Required configuration once a tenant exists (from [authentication.md](authentication.md) §10):
redirect URIs (platform **Web**) `https://easyapply-staging.onrender.com/api/auth/callback`
and `https://easyapply-staging.onrender.com/signed-out`; a client secret; Graph
delegated `openid profile email` with admin consent; the `email` optional claim;
app role `EasyApply.Operator`; a sign-up/sign-in user flow (email + password,
collect Display Name) associated with the app.

## 7. Chrome extension (staging build)

```
cd apps/extension
EASYAPPLY_WEB_URL=https://easyapply-staging.onrender.com node scripts/build.mjs --out .artifacts/staging
```

- Output `apps/extension/.artifacts/staging/` (gitignored), zipped without source
  maps as `apps/extension/.artifacts/easyapply-extension-staging.zip`.
- Host permissions: the three job-board patterns + `https://easyapply-staging.onrender.com/*`.
  No localhost anywhere in the bundle; no credential or secret name in it.
- Id `jjhciekcmhmngciccfgignmmlgjknakk` — fixed by the public `key` in
  `src/manifest.json` (derived and checked), and the only id in
  `PROXY_ALLOWED_EXTENSION_IDS`; other extensions stay refused.
- `apps/extension/dist/` is still the localhost development build — never hand that one out.

**Install (tester):** unzip → `chrome://extensions` → enable **Developer mode**
→ **Load unpacked** → select the unzipped `easyapply-extension-staging` folder →
pin EasyApply. Sign in on https://easyapply-staging.onrender.com in the same
Chrome profile first; the extension uses that browser session and holds no
credentials of its own.

## 8. Health checks

| Endpoint | Meaning | Used by |
|---|---|---|
| `GET /api/health` | web process alive; depends on nothing | Render health check |
| `GET /api/health/ready` | `auth` config complete, `api` ready, and the API's `database`, `schema`, `processing` — 503 if any is not `ok` | uptime checks, operators |
| API `GET /health`, `/health/ready` | the same split inside the container (loopback only) | `start.sh`, the web readiness route |

## 9. Logs

`render logs` (CLI) or the dashboard's Logs tab: build, deploy and runtime output
of both processes (uvicorn access lines, `health.*`, `identity.*`, `auth.*`,
`proxy.extension` lines). What is logged was reviewed in the Entra pass
(authentication.md §12): no tokens, cookies, secrets, resume text or emails.
Render retains free-tier logs briefly; there is no external log shipping.

## 10. Deploy, restart, rollback

```
# deploy the current main (auto-deploy is off on purpose)
git push origin main
render deploys create srv-dartamojo6nc739brjl0 --wait

# restart without a build
render restart srv-dartamojo6nc739brjl0

# rollback: redeploy an earlier commit
render deploys create srv-dartamojo6nc739brjl0 --commit <sha> --wait
```

Migrations run at start and are additive in this revision; a rollback past a
migration must be a deliberate `alembic downgrade` (which refuses where data
would be lost — f1a9d3c5b7e2, a7c3e5f19b20).

## 11. Teardown

```
render services delete srv-dartamojo6nc739brjl0 --confirm
render postgres delete easyapply-staging-db --confirm      # deletes all staging data
```

Then delete the Entra trial tenant (Entra admin center → Manage tenants) and,
if wanted, the GitHub repo. Nothing else was created.

## 12. Custom domain (later, optional)

Render → service → Settings → Custom Domains (free tier allows them), add the
DNS record, then change `APP_ORIGIN` and `PROXY_ALLOWED_HOSTS`, rebuild the
extension with the new `EASYAPPLY_WEB_URL`, and add the two redirect URIs for the
new origin in the Entra app registration.

## 13. Test results (2026-09-26)

**On Render (real):**

| Check | Result |
|---|---|
| Database created | `easyapply-staging-db`: free, PostgreSQL 18, virginia, `ipAllowList: []`, status available, expires 2026-10-26 |
| Service created from the private repo | `easyapply-staging` → `https://easyapply-staging.onrender.com` (the configured `APP_ORIGIN`) |
| Image build on Render | succeeded (commit `5426685`) |
| Migrations on Render Postgres, over the internal URL | all 16 revisions through `a7c3e5f19b20` applied |
| Startup without Entra configuration | **refused** — `Identity is misconfigured: ENTRA_TENANT_ID …; ENTRA_ISSUER …`; container stopped; deploy `update_failed`; nothing served |
| Public URL | TLS connects to Render's edge; the request is held with no response (no live deploy) |
| Entra sign-in, browser workflow, extension, second user, failure tests **on staging** | **not run** — blocked on the tenant (§6, §15) |

**The same image, locally, under the free instance's limits** (`docker run
--memory 512m --cpus 0.1`, PostgreSQL 18.6):

| Check | Result |
|---|---|
| Cold start | 155 s → **74 s** after precompiling bytecode and migrating + serving in one interpreter |
| `GET /api/health` / `GET /api/health/ready` (entra-shaped config) | 200 / 200 `ready`, `auth_mode: entra`, all checks `ok` |
| Foreign `Host` | 403 `host_not_allowed` |
| Proxy without a session | 401 `session_required` |
| API port from another container on the same network | refused (web port answers 200) — the API is loopback-only |
| Process user | uid 10001 (not root) |
| Memory | 185 MiB idle, ~232 MiB after a full run (limit 512) |
| Full workflow through the proxy (development identity — local only, because no tenant exists) | upload 201 → processing `completed` 8 s → confirm 200 → job 201 → `completed` 1 s → 7 requirements → match 201 → tailoring `completed` 6 s → DOCX 200, 37,932 bytes, valid zip |
| Documents survive a new container | container deleted and re-created; DOCX re-downloaded, **SHA-256 identical**; `stored_objects` holds the PDF (5,378 B) and the DOCX |

**API image alone** (`services/api/Dockerfile`, production + entra config): migrations to
head with `alembic check` reporting no drift; `/health/ready` 200; startup refused
without `IDENTITY_ASSERTION_SECRET`; with the database stopped `/health` 200 and
`/health/ready` 503 `database: unavailable` (no connection details in the body),
recovering when it returned; `ENVIRONMENT=production` + development identity →
401 on every request and `identity.production_without_sign_in` logged.

**Regression suites** (local, sequential): backend pytest **2783 passed, 13
skipped, 0 failed**; web vitest **649 passed**; extension vitest **546 passed**;
web type-check and lint of the changed files clean; web Playwright e2e **27/27**
(including the Entra flows against the local provider); extension browser suite
**15/15** (stub) and **15/15** (real stack).

Cross-user isolation (404 for another user's jobs, profiles, resumes, matches,
tailored resumes and artifacts) is covered by the backend and browser suites;
it has **not** been exercised on the staging deployment itself.

## 14. Known limitations (free tier)

- **Database deleted 2026-10-26** (30 days after creation), with no backups and
  no point-in-time restore. Everything — accounts, profiles, jobs, documents —
  goes with it. Upgrading the database plan before then keeps it.
- **Entra trial tenant: 30 days**, then it needs an Azure subscription.
- **Sleeps after 15 minutes idle**; the next request waits for a cold start
  (~75 s measured under the same limits locally). Open the web app first to wake
  it before using the extension.
- **512 MB RAM / 0.1 CPU, one instance**: measured ~185 MB idle, ~230 MB during a
  resume + tailoring run. OCR of image-only PDFs is slow at 0.1 CPU. One worker
  thread per queue.
- **Free instance hours are shared** across the workspace (750 h/month) with the
  workspace's other free services.
- **Documents live in the database** (§4).
- **Assertion replay guard is per process** — sound here (one API process);
  scaling out would need a shared store (this is where Redis would matter), and
  so would rate limiting, which does not exist yet.
- **No rate limiting, no WAF, no HSTS header** from the app (Render redirects
  HTTP→HTTPS; cookies are `__Host-`, Secure, HttpOnly).
- **The web proxy buffers request bodies** in memory (only after sign-in is
  verified); the API's own 10 MB upload limit still applies.
- **The extension id is derived from a public key** in the repo: any unpacked
  extension built with that key has the same id. The Web Store listing (later)
  fixes this.
- The operator screen needs the `EasyApply.Operator` app role; nobody holds it.

## 15. What remains before an external tester can use it

In order. Steps 2-6 are CLI work once step 1 is done; nothing in the
application changes.

1. **Owner:** sign up for an Azure free account with the same Microsoft account
   (card + phone for verification; nothing billable is created).
2. `az login`; register `Microsoft.AzureActiveDirectory`; create a resource group
   and an External ID tenant (`Microsoft.AzureActiveDirectory/ciamDirectories`,
   location United States) — or Entra admin center → Manage tenants → Create →
   External. Then `az login --tenant <tenant>.onmicrosoft.com --allow-no-subscriptions`.
3. In the tenant (§6 list): app registration (single tenant, Web redirect URIs
   `https://easyapply-staging.onrender.com/api/auth/callback` and `/signed-out`),
   service principal, client secret with a recorded expiry, Graph `openid profile
   email` + admin consent, `email` optional claim, app role `EasyApply.Operator`;
   user flow (email + password, collect Display Name) associated with the app;
   two test users (A, B).
4. Confirm `ENTRA_ISSUER` against the tenant's discovery document, write the five
   values to a JSON file **outside the repo**, then
   `render services delete easyapply-staging --confirm` and
   `python deploy/staging/render_create_service.py --entra <file>`; wait for the
   deploy to be `live`; `GET /api/health/ready` must answer 200 with every check `ok`.
5. Real test on staging: sign in as A; `deploy/staging/smoke_flow.py` with A's
   session cookie; the browser + staging-extension workflow on a real
   Greenhouse and Lever posting (save → requirements → match → evidence →
   tailor → DOCX → application form → review → fill approved fields → **stop,
   never submit**); B cannot see A's jobs, profiles, resumes, matches, tailored
   resumes or artifacts (404), and the extension shows B's state after an
   account switch; failure cases (signed out, expired session, unsupported site,
   invalid job page, a corrupt resume, the database suspended with
   `render postgres suspend`).
6. Send the tester: the URL, `easyapply-extension-staging.zip` and §7's install
   steps, and a note that the first visit after 15 idle minutes takes about a
   minute. Only their first session, from their own computer, is a test from
   outside the development environment.
