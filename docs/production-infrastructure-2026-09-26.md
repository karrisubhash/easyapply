# Production infrastructure — what remains after authentication (2026-09-26)

Real sign-in (Microsoft Entra External ID) is built and verified locally against
a provider shaped like External ID ([authentication.md](authentication.md), final
report [entra-authentication-2026-09-26.md](entra-authentication-2026-09-26.md)).
Nothing has been deployed, and no Azure resource exists. This is the list of
infrastructure work between here and a production deployment, with what the
code as it stands requires of each item. It is a plan, not a design: every
item still needs its own decision.

**Order.** A real Entra tenant first (it is the one piece of the application
itself that has not met its real counterpart), then the items marked
*blocking*, then the rest.

## Tenant and identity

| Item | State | What is needed |
|---|---|---|
| **Entra External ID tenant** — *blocking* | Not created. The code is verified against `apps/web/e2e/support/mock-entra.mjs` only. | Tenant, app registration, user flow, app role, optional `email` claim — [authentication.md](authentication.md) §10. Then run the browser suites' entra flows by hand against it and record the result; only then is the integration verified. |
| **Production secrets** — *blocking* | Development values in local `.env` files; test values in the harnesses. | `SESSION_SECRET`, `IDENTITY_ASSERTION_SECRET`, `ENTRA_CLIENT_SECRET`, `OPERATOR_API_TOKEN`, the database password — each ≥ 32 random characters where the code requires it, generated for production only, never reused across tiers except `IDENTITY_ASSERTION_SECRET` (same value on web and API by design). Rotation procedures are in authentication.md §4; the client secret's expiry needs a calendar reminder or automation. |
| **Key Vault** | None. | Hold the secrets above; web and API read them as environment variables (both read configuration from the environment only — no code change needed), via managed identity and Key Vault references. |
| **Domain** — *blocking* | None. | A public hostname for the web app. It becomes `APP_ORIGIN` (https), goes in `PROXY_ALLOWED_HOSTS`, and gives the two redirect URIs registered in the tenant (`/api/auth/callback`, `/signed-out`). The API needs a hostname in `API_ALLOWED_HOSTS` (it refuses every other Host). An External ID custom URL domain is optional and changes `ENTRA_AUTHORITY`/`ENTRA_ISSUER` only. |

## Compute

| Item | State | What is needed |
|---|---|---|
| **Azure deployment** — *blocking* | Two processes run locally: `next start` (web) and uvicorn (API). | Two services (e.g. App Service or Container Apps): the web app public, the API reachable **only** from the web app (private networking or an internal ingress) — the API trusts the web app's assertions, and although each is signed, bound to the API audience and usable once, nothing but the web app should be able to reach it at all. `ENVIRONMENT=production` and `AUTH_MODE=entra` on both. |
| **Workers** | Resume processing and tailoring run in in-process thread pools inside the API (`RESUME_QUEUE_BACKEND=thread`, `TAILORING_QUEUE_BACKEND=thread`); a reaper recovers expired leases. | Decide whether they stay in the API process (then scale-out means N pools sharing one database, which the lease design allows) or move to a separate worker service. Either way the reaper and lease settings must be reviewed for the chosen instance count. |
| **Redis** | Not used. | Two things want it: the assertion **replay guard** is per API process (an assertion could be replayed once per instance within its 60 seconds — authentication.md §5), and **rate limiting** (below). A shared store closes both. Optional for a single instance. |
| **Blob Storage** — *blocking* | Resumes and generated artifacts are files under `RESUME_STORAGE_ROOT` on the API's local disk. | Instance-local disk does not survive a redeploy or scale-out. Needs a storage backend (Blob) behind the existing storage interface, private containers, no public URLs (downloads keep going through the API's ownership checks). |

## Data

| Item | State | What is needed |
|---|---|---|
| **PostgreSQL production** — *blocking* | Local PostgreSQL. Alembic head is `f1a9d3c5b7e2` (the new `user_identities` migration); the developer's own database has **not** been upgraded to it. | Azure Database for PostgreSQL, TLS required, private access from the API only, migrations run as a release step (`alembic upgrade head`) before the new API version serves traffic. The new migration refuses to downgrade once Entra users exist (their `users.email` is null) — by design, stated in the migration. |
| **Backups** | None beyond the developer's own dumps. | Point-in-time restore on the database; Blob soft delete/versioning; a tested restore. The two must be restorable to a consistent point (a resume row whose file is missing, or the reverse). |
| **Disaster recovery** | None. | RPO/RTO decided; region choice; a written restore runbook including the tenant settings (app registration, redirect URIs, app role assignments) and the secrets. Accounts are keyed by the Entra tenant id + object id, so a restored database works with the same tenant and only with it. |

## Edge

| Item | State | What is needed |
|---|---|---|
| **Front Door / WAF** | None. | TLS termination, WAF rules, `Strict-Transport-Security` (the app sends none itself; its cookies are Secure and `__Host-` prefixed over https), and `X-Forwarded-Proto: https` towards the web app (it marks cookies Secure from `APP_ORIGIN` anyway). The Host the web app sees must be `APP_ORIGIN`'s host, or sign-in redirects there first (by design) — configure Front Door to preserve the Host header. |
| **Production CORS** | None needed today: the browser never calls the API (the web app proxies), and the extension calls the web app, which answers it through admission rules, not CORS. | Keep it that way: do not add CORS to the API. If a direct API client is ever added, that is a new authentication design (authentication.md §1.2), not a CORS setting. |
| **Rate limiting** | None. | Sign-in start and callback (per IP), the proxy (per session), resume upload and tailoring (per user — tailoring can call a paid model when enabled). At the edge, in the web app, or both; a shared store for multi-instance. |

## Operations

| Item | State | What is needed |
|---|---|---|
| **Logging** | Structured one-line events to stdout (both tiers); no secrets, tokens, emails or object ids (authentication.md §12). The API's formatter prints the event name but not its `extra` fields. | Ship stdout to Log Analytics / Application Insights; make the API formatter emit `extra` (reason categories, user ids) as structured fields; retention policy. Operator access is logged with a pseudonymous key. |
| **Monitoring** | `/health` on the API (does not check the database or the identity configuration). | Liveness vs readiness probes (readiness should check the database); request metrics; worker queue depth and lease expiry; sign-in success/failure rates from `auth.sign_in.*`. |
| **Alerting** | None. | Alerts on: any `auth.misconfigured`, `identity.misconfigured`, `identity.production_without_sign_in`, `dev_identity.refused bearer_in_development_mode` (a mode mismatch between tiers — every request is failing); a spike in `auth.sign_in.failure` (e.g. an expired client secret: `invalid_client`); `identity.refused replayed`; worker failures; 5xx rate. Client secret expiry reminder. |
| **CI/CD** | No git repository; no pipeline. | Source control first. Then: the four suites (API pytest, web vitest + e2e including `specs/entra.spec.ts`, extension vitest + stub browser suite, extension real-stack in both modes) on every change; migrations as a gated release step; infrastructure as code; secrets only from Key Vault. |

## The Chrome extension

| Item | State | What is needed |
|---|---|---|
| **Chrome Web Store identity** | The unpacked extension's id `jjhciekcmhmngciccfgignmmlgjknakk` is pinned by a public `key` in `src/manifest.json`; the private key was never kept. | A Web Store listing gets its own id. `PROXY_ALLOWED_EXTENSION_IDS` must name it (the proxy admits the extension's three workflow writes only for listed ids); keep or drop the pinned development id deliberately. |
| **Extension publishing** | Built with `EASYAPPLY_WEB_URL` fixed at build time (it becomes a host permission). | A production build with `EASYAPPLY_WEB_URL=https://<APP_ORIGIN host>`; a privacy disclosure (it reads job postings and application forms on the supported sites, and profile data from the user's own account); review of host permissions (Greenhouse, Lever, the web app). The extension holds no credentials under any mode — nothing changes there. |

## Not infrastructure, but blocking a real launch

- **Existing development data** stays unreachable under Entra (authentication.md
  §11) — decide whether any of it matters before launch.
- **Session revocation** is lifetime- and secret-rotation-based only; if a
  per-user "sign out everywhere" is required, it needs a server-side session
  store (Redis or the database).
