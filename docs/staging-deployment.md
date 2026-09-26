# Staging deployment — Render free tier (2026-09-26)

A public HTTPS environment for external user testing. **It is staging, not
production**: it runs entirely on free resources, and §14 lists what that costs.
Nothing here is a claim of production readiness.

> **Status 2026-09-26 (evening, UTC): LIVE.** Real Microsoft Entra sign-in,
> the web app and the staging extension were exercised end to end against the
> public URL from a browser on the internet (§13) — including two accounts
> isolated from each other and the failure cases. Identity is the owner's
> **Microsoft Entra ID (workforce) directory**, not External ID: Microsoft
> withdrew the no-card External ID trial and the owner has no card (§6). What a
> real tester still needs is in §15.

| | |
|---|---|
| Public URL | **https://easyapply-staging.onrender.com** |
| Platform | Render (free web service + free Postgres), region `virginia` (US East) |
| Source | private GitHub repo `karrisubhash/easyapply`, branch `main` |
| Render service | `easyapply-staging` — `srv-das0srfpn0mc73eclbc0` (re-created with Entra settings; the first, `srv-dartamojo6nc739brjl0`, was deleted) |
| Render database | `easyapply-staging-db` — `dpg-dart3s3ncjis73f0hb3g-a`, PostgreSQL 18, **expires 2026-10-26** |
| Identity | Microsoft Entra ID, tenant `3c8c5c0f-14d5-4f04-bec5-18052a32d8c5` ("Default Directory"), app `EasyApply Staging` `d9d86fc1-bd56-4923-8eb7-6579b35c2931` (`AUTH_MODE=entra`) — §6 |
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

Microsoft Entra ID (`login.microsoftonline.com`) is reached by the browser for
sign-in and by Next.js server-side for discovery, JWKS and token redemption.

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
| `ENTRA_TENANT_ID`, `ENTRA_ISSUER` | `3c8c5c0f-14d5-4f04-bec5-18052a32d8c5`, `https://login.microsoftonline.com/3c8c5c0f-14d5-4f04-bec5-18052a32d8c5/v2.0` (checked against the tenant's discovery document) | web + API |
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
| `ENTRA_AUTHORITY`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | `https://login.microsoftonline.com/3c8c5c0f-14d5-4f04-bec5-18052a32d8c5/v2.0`, `d9d86fc1-bd56-4923-8eb7-6579b35c2931`, secret (created by `az ad app credential reset`, **expires 2027-09-26**; server-side only) | web |
| `PROXY_ALLOWED_EXTENSION_IDS` | `jjhciekcmhmngciccfgignmmlgjknakk` | web |
| `NEXT_TELEMETRY_DISABLED` | `1` | web |

Render's CLI cannot edit a service's environment after creation, so a change is
made by re-creating the service (it is stateless — everything is in the
database) with [`deploy/staging/render_create_service.py`](../deploy/staging/render_create_service.py),
or in the dashboard (Service → Environment).

## 6. Identity (Microsoft Entra)

**What is configured (2026-09-26):**

| | |
|---|---|
| Tenant | `3c8c5c0f-14d5-4f04-bec5-18052a32d8c5` — "Default Directory", `karrisubhash643gmail.onmicrosoft.com`, a **workforce** Microsoft Entra ID tenant (Entra ID Free; no Azure subscription). The owner's account is its Global Administrator. |
| App registration | `EasyApply Staging`, client id `d9d86fc1-bd56-4923-8eb7-6579b35c2931`, single tenant (`AzureADMyOrg`) |
| Redirect URIs (Web) | `https://easyapply-staging.onrender.com/api/auth/callback`, `https://easyapply-staging.onrender.com/signed-out` |
| Permissions | Microsoft Graph delegated `openid`, `profile`, `email`; **admin consent granted** |
| Token configuration | `email` optional claim on the ID token |
| App role | `EasyApply.Operator` (assigned to nobody) |
| Client secret | `render-staging`, expires **2027-09-26** — rotate before (create, re-create the service, delete the old one) |
| Test users | `staging-tester-a@…` and `staging-tester-b@…` (member accounts; passwords kept outside the repo) |

No application code changed for this: the web app's OIDC client takes the
authority and issuer from configuration and validates `iss` (pinned), `aud`,
`tid`, `oid`, `nonce` — all present in workforce v2.0 ID tokens. The discovery
document's `issuer` equals `ENTRA_ISSUER` exactly. The account key stays
`entra:<tid>` + `oid`.

**How it came to be a workforce tenant, not External ID** (the code was built
and locally verified for External ID):

1. The owner's personal Microsoft account first had **no Azure directory at all**.
2. `https://aka.ms/ciam-free-trial` now redirects to the Azure free-account page.
3. The External ID VS Code extension's "Set up a free trial" called
   `PUT https://main.b2cadmin.ext.azure.com/api/tenants/CreateCIAMTrialDirectoryAsync/easyapplystaging.onmicrosoft.com`
   → **HTTP 404 "The resource you are looking for has been removed"** (17:12 UTC).
4. The Azure free account needs a card; the owner has none. The owner signed up
   for **Azure for Students**; that created the Default Directory, but **no
   subscription appeared** (`az account list --all` empty), so an External ID
   tenant (which must link to a subscription) still could not be created.
5. A workforce tenant needs no subscription, and it is real Microsoft sign-in.

Differences that matter for testers: **no self-service sign-up** — every tester
is an account in this directory (a member created for them, or a guest invited
by email, §15); the sign-in page is Microsoft's standard one, not External ID's
branded flow. If a subscription appears later, an External ID tenant can replace
this by configuration alone (authentication.md §10), at the cost of new accounts.

Before Entra was configured the deployment **failed closed**, verified on Render:
the API's startup check raised *"Identity is misconfigured: ENTRA_TENANT_ID must
be the tenant's GUID; ENTRA_ISSUER must be the tenant's https token issuer"*,
`start.sh` stopped the container, and Render marked the deploy `update_failed`.

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
render deploys create srv-das0srfpn0mc73eclbc0 --wait

# restart without a build
render restart srv-das0srfpn0mc73eclbc0

# rollback: redeploy an earlier commit
render deploys create srv-das0srfpn0mc73eclbc0 --commit <sha> --wait
```

Migrations run at start and are additive in this revision; a rollback past a
migration must be a deliberate `alembic downgrade` (which refuses where data
would be lost — f1a9d3c5b7e2, a7c3e5f19b20).

## 11. Teardown

```
render services delete srv-das0srfpn0mc73eclbc0 --confirm
render postgres delete easyapply-staging-db --confirm      # deletes all staging data
az ad app delete --id d9d86fc1-bd56-4923-8eb7-6579b35c2931
az ad user delete --id staging-tester-a@karrisubhash643gmail.onmicrosoft.com
az ad user delete --id staging-tester-b@karrisubhash643gmail.onmicrosoft.com
```

Keep the Default Directory itself — it is the owner's account directory, not
something this deployment created. Delete the GitHub repo only if wanted.

## 12. Custom domain (later, optional)

Render → service → Settings → Custom Domains (free tier allows them), add the
DNS record, then change `APP_ORIGIN` and `PROXY_ALLOWED_HOSTS`, rebuild the
extension with the new `EASYAPPLY_WEB_URL`, and add the two redirect URIs for the
new origin in the Entra app registration.

## 13. Test results (2026-09-26)

**Live, against https://easyapply-staging.onrender.com** (headless Chromium with
the staging extension loaded, from the development machine over the public
internet; real Microsoft sign-in; `deploy/staging/live_check.mjs`):

| Area | Check | Result |
|---|---|---|
| Platform | Database | free, PG 18, virginia, `ipAllowList: []`, expires 2026-10-26; all 16 migrations applied over the internal URL |
| | Deploy | image built on Render; `live` 150 s after creation; real cold start ~14 s (restart logs: migrate 18:51:59 → API 18:52:05 → Next ready 18:52:13 UTC) |
| | Health | `/api/health` 200; `/api/health/ready` 200, `auth_mode: entra`, all checks `ok` |
| | Edge | `http://` → 301 `https://`; API `/docs`, `/openapi.json` → 404 (the API is not public); proxy without a session → 401 |
| Sign-in | A and B | real `login.microsoftonline.com` sign-in (email, password, "Stay signed in? No"); no MFA prompt; session `mode=entra` |
| | Cookie | `__Host-easyapply_session`: Secure, HttpOnly, SameSite=Lax, host-only |
| | Sign-out | Entra end-session → `/signed-out`; session `authenticated: false` |
| Web | Resume | upload (golden resume) → processed → draft version 1 → **confirmed** |
| Extension — Greenhouse `anthropic/jobs/5191226008` | Capture | content script ran; review showed "Saving as Staging Tester A"; saved; full description captured (6,948 chars) |
| | Requirements | **0 structured requirements** — the rule-based reader found only 5 responsibilities (§14; identical with the local code, not a deployment defect) |
| | Match / tailor | match created (0 requirement results, as above); tailored resume → **DOCX 37,955 bytes**, valid zip, `Anthropic-Software-Engineer-Research-Data-Platform-v1.docx` |
| | Application form | detected (26 fields) → 6 answers proposed, 7 sensitive questions left to the person → **filled 6 fields** → report "EasyApply hasn't submitted anything"; page stayed on the posting; **nothing submitted** |
| Extension — Greenhouse `twilio/jobs/8015771` | Requirements, match, evidence | 24 required / 10 preferred → match with 34 requirement results → evidence page fully rendered (coverage bars, per-requirement verdicts, cited profile items) |
| Extension — Lever `palantir/ab7e3425…` | Capture, requirements | saved (10,397 chars); 0 structured requirements (same reader limitation) |
| | `/apply` form | 7 answers proposed → **filled 7 fields** → "hasn't submitted anything"; stayed on `/apply`; nothing submitted |
| Isolation (B vs A) | Direct reads | B → **404** for A's job, job requirements, job matches, match, tailored resume, tailored DOCX, resume document, resume result, profile version, Lever job, Twilio job, Twilio match (12/12) |
| | Lists | B's jobs, resumes and tailored-resume lists: 200, empty — none of A's |
| | Extension | B's popup on the same posting: "Saving as Staging Tester B", nothing of A |
| | Account switch, one browser | A signed in → sign out → popup "Sign in to EasyApply to save this job." → B signs in → popup "Saving as Staging Tester B", nothing of A |
| Failures | Signed out | proxy 401 `session_required`; popup asks for sign-in |
| | Tampered session | modified cookie → 401 `session_required` (a truly expired one takes the same path; lifetime 8 h) |
| | Unsupported site | Wikipedia page → popup "Not a supported job page." |
| | Invalid job page | Greenhouse board page → "No single job here."; no save |
| | Resume processing failure | corrupt PDF → "Processing failed while reading the text. The PDF could not be read. It may be damaged. Reference: malformed_pdf. Another attempt would fail the same way." |
| | Tailoring refused | without a confirmed profile → 409 "This profile has no confirmed version yet. Confirm a version before tailoring." |
| | Database unavailable | `render postgres suspend`: `/api/health` 200, `/api/health/ready` **503** `database: unavailable`; signed-in API calls 500 "Internal Server Error" (no internals); dashboard "Your profile could not be loaded."; after `resume`, ready 200 with no restart |
| | API / worker unavailable | not separable here (one container): if either process exits, `start.sh` stops the container and Render restarts it — seen on the first, Entra-less deploy |
| Persistence | Restart | `render restart` → a new container (logs) → A's 3 jobs and 1 tailored run intact; DOCX re-downloaded **byte-identical** (SHA-256 `aa23af27f783ab71…`) |

Not yet a test from *outside* the development environment: the browser ran on
the development machine (reaching Render over the internet, no local service
involved). The first external tester's session is that test.

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

Cross-user isolation is covered by the suites and was also exercised on the
staging deployment itself (table above).

## 14. Known limitations (free tier)

- **Database deleted 2026-10-26** (30 days after creation), with no backups and
  no point-in-time restore. Everything — accounts, profiles, jobs, documents —
  goes with it. Upgrading the database plan before then keeps it.
- **Identity is a workforce Entra ID tenant, not External ID** (§6): no
  self-service sign-up; each tester needs an account made or invited for them.
- **Real postings the job reader does not parse.** On the Anthropic (Greenhouse)
  and Palantir (Lever) postings the deterministic job structurer found **no
  structured requirements** (their headings — "What We Require", "You may be a
  good fit if you" — are not ones it recognises), so their match is empty and
  tailoring has nothing to aim at. The local code gives the identical result; 18
  of 20 other live Greenhouse postings scanned did yield requirements. A product
  finding, deliberately not changed in this deployment task.
- **A database outage** reaches a signed-in user as a generic 500 and "could not
  be loaded" messages — honest, but it does not say "try again shortly".
- **Sleeps after 15 minutes idle**; the next request waits for the instance to
  start again (~14 s of container start measured on Render, plus Render's own
  wake-up). Open the web app first to wake it before using the extension.
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
- **The Entra client secret expires 2027-09-26.**

## 15. Giving it to an external tester

1. **An account for them** in the tenant (§6), one of:
   - *Member account (the tested path):*
     `az ad user create --display-name "<Name>" --user-principal-name <name>@karrisubhash643gmail.onmicrosoft.com --password <temporary> --force-change-password-next-sign-in true`,
     then send the username and temporary password privately; they choose their
     own password at first sign-in.
   - *Guest with their own email (not yet exercised):* a Microsoft Graph
     invitation (`POST /v1.0/invitations`, redirect
     `https://easyapply-staging.onrender.com`); they accept by email and sign in
     with a one-time passcode or their own Microsoft/Google account.
2. **Send them:** the URL; `apps/extension/.artifacts/easyapply-extension-staging.zip`
   with §7's install steps; and three notes — open the web app first (it may take
   a few seconds to wake), sign in there before using the extension, and
   EasyApply never submits an application (they review and submit themselves).
3. **Their first session, from their own computer,** is the one check not yet
   done: a browser outside the development environment.

Before production (not in scope for staging): a database plan with backups, an
object store instead of documents in Postgres, the API as its own private
service, rate limiting, error tracking and log shipping, a Chrome Web Store
listing, and a decision between External ID and this workforce tenant — see
[production-infrastructure-2026-09-26.md](production-infrastructure-2026-09-26.md).
