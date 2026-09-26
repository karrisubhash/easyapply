# Real sign-in with Microsoft Entra External ID — final report (2026-09-26)

Engineering report for the authentication phase. Design detail lives in
[authentication.md](authentication.md); the infrastructure still to do is
[production-infrastructure-2026-09-26.md](production-infrastructure-2026-09-26.md).

## Where this stands, in four words that mean different things

| | Status | What it rests on |
|---|---|---|
| **Technically implemented** | **Yes.** | OIDC authorization code + PKCE sign-in, ID token validation, an EasyApply session, a per-request assertion to the API, identity mapping by Entra object id, account-switch protection in both clients, operator role gating, fail-closed configuration. |
| **Locally verified** | **Yes, against a local provider.** | Every suite green (§16), including full browser runs of the web app and the Chrome extension signing in through a local OpenID provider shaped like External ID (`apps/web/e2e/support/mock-entra.mjs`), and an adversarial code review whose 19 confirmed findings are fixed (17) or documented (2) (§8). |
| **Integration verified** | **No.** | No Entra External ID tenant exists for this project. Nothing here has met real Microsoft endpoints, user flows, consent, optional claims, token lifetimes or AADSTS errors. |
| **Production-ready** | **No.** | Blocked on the real tenant, then on the infrastructure in §18. Passing tests do not change this. |

The extension still never submits an application, and nothing in this phase
touched matching, the matching benchmark, resume extraction or structuring,
tailoring grounding or reconciliation, AI prompts or evaluation, or the
extension's matching, tailoring and autofill rules (verified by diffing against
the pre-phase snapshot: §12).

## 1. Executive summary

EasyApply now has a production authentication mode, `AUTH_MODE=entra`, on both
tiers. The web server signs people in with Microsoft Entra External ID
(authorization code with PKCE, a confidential client), validates the ID token
once, discards every Microsoft token, and issues its own HttpOnly session
cookie. The browser clients — the web UI and the Chrome extension — both
present that cookie and hold no credential. For every request the web server
forwards, it mints a 60-second, single-use signed assertion that the API
validates and maps to an account by the person's immutable Entra object id
(`oid`) within the tenant — never by email. The development modes
(`development`, `dev-session`) are unchanged and remain the test suites'
multi-user modes; every mismatch between the two tiers' modes fails closed.

It works end to end against a local provider that reproduces External ID's
protocol and quirks, proven in two browser runs rather than one continuous one
(§17): in the web app, User A signs in, builds a profile from a resume, saves
a job, matches it and generates a tailored resume, signs out, and User B in the
same browser sees none of it; in the extension, a signed-in user saves a
Greenhouse job, gets requirements, a match, a tailored resume and a reviewed
form fill (never submitted), and a second account in the same browser and the
same extension sees only its own (§14, §15). It has not been run against a
real tenant.

## 2. Authentication architecture

```
browser ──(one HttpOnly session cookie, shared by web UI and extension)──▶ web server (apps/web)
   admission  (Host; same-origin writes; the extension's three workflow writes)   src/server/request-guard.ts
   identity   resolveIdentity(cookies)                                            src/server/auth/identity.ts
   consistency X-EasyApply-Session must be the cookie's account (409)
   operators  /internal only for the Entra operator role (404)
   forward    Authorization: Bearer <60 s HS256 assertion>  ──▶  API (services/api)
                                                               app/api/identity.py → assertion_identity.py
                                                               (entra:<tid>, oid) → user_identities → users.id
```

One seam per tier, as before: every route still depends on
`app.api.identity.CurrentUser` and services still take `user_id: int`
(the five routers changed one import line each). The design choice that
matters — the API trusts a web-minted assertion rather than receiving
Microsoft access tokens — and its trade-offs are in authentication.md §1.2.

## 3. Entra integration architecture

- **Discovery** on the tenant-name host (`ENTRA_AUTHORITY`), with the issuer
  **pinned** by configuration (`ENTRA_ISSUER`, defaulting to
  `https://<tenant-id>.ciamlogin.com/<tenant-id>/v2.0`): External ID declares
  an issuer on a different host from the one discovery is served from, so a
  strict library refuses it and a lax one trusts the document. The document's
  issuer must equal the pinned one; endpoints must be https.
- **Sign-in**: same-origin form POST → a sealed per-transaction cookie
  (state, nonce, PKCE verifier; AES-256-GCM under an HKDF-derived key) → 303 to
  Microsoft with `prompt=select_account`.
- **Callback**: the state's transaction first (login CSRF), provider errors
  mapped to `cancelled/expired/unavailable/failed`, code redeemed with the
  client secret and verifier, ID token validated (RS256 only; issuer, audience,
  single audience, azp, nonce, tenant, `oid` GUID, age ≤ 10 min, roles shape),
  display metadata cleaned, session issued, Microsoft tokens dropped unread.
- **Sign-out**: clears the cookie and sends the browser through the tenant's
  end-session endpoint back to `/signed-out`, so Microsoft's session ends too.
- Libraries: `jose` 6.2.12 (web), `PyJWT` 2.15.0 (API). Full step list:
  authentication.md §3.

## 4. Identity mapping design

- Key: `user_identities (issuer = "entra:<tenant GUID>", subject = lower-cased oid)`,
  unique. The tenant GUID, not the issuer URL, so reconfiguring the issuer (a
  custom domain, a corrected setting) never orphans accounts; the URL is still
  checked on every token and assertion.
- `oid`, not `sub` (pairwise per app) and never email or name (mutable). **No
  email linking** — considered and rejected: whoever controls an address in the
  tenant (including a self-service sign-up) would take over the account that
  address once belonged to. Tested: same email + different `oid` = two
  accounts; same `oid` + new email = the same account; a first Entra sign-in
  never attaches to a development-mode account with that email.
- Provisioning on first sight, in one transaction on the request's own
  session; a concurrent first sight loses the unique constraint and reads the
  winner (eight threads → one account, tested).
- Display metadata (email, name) is cleaned on both tiers and **never refuses a
  request** — a design-review finding: a zero-width joiner, a non-breaking
  space, an emoji, a 256-character name or a lone surrogate are all valid
  inputs, stored cleaned.

## 5. Session design

`__Host-easyapply_session` over https (`easyapply_session` on loopback http) =
`v2.<payload>.<HMAC-SHA256>`; claims `typ, iss, aud (client id), tid, oid,
email, name, op, sid, iat, exp`; exact key set. HttpOnly, Secure, SameSite=Lax,
Path=/, no Domain.

- **Lifetime**: absolute 8 hours by default (`SESSION_MAX_AGE_SECONDS`, 60 s –
  30 days), enforced against the current configuration too; not sliding, not
  refreshed.
- **Expiry**: the web UI keeps the page and says *Your EasyApply session
  expired. Sign in again.* with a sign-in in a new tab (same person → same
  account tag → retry works); the extension says the same.
- **Rotation**: `SESSION_SECRET_PREVIOUS` verifies during a drain; rotating
  without it ends every session (the emergency revocation). The assertion secret
  rotates the same way on the API.
- **Logout**: explicit, ends both EasyApply's and Microsoft's sessions.
- **Entra session vs EasyApply session**: independent; revoking a person or a
  role in Entra takes effect at their next EasyApply sign-in, at most the
  session lifetime later. Details: authentication.md §4.

## 6. Extension authentication behavior

Unchanged mechanism: the extension's requests carry the browser's session
cookie for the web app; it has no `cookies` permission and stores no token —
its only storage is the per-tab record of a save (the job's URL and id, the
outcome, the account's display label and account tag, none of them a
credential). New behaviour:

- Signed in is `authenticated` alone (an Entra account may have no email); the
  label is email, else name, else "your EasyApply account" — *Saving as …*.
- Signed out: *Sign in to EasyApply to save this job.* Expired mid-flow: *Your
  EasyApply session expired. Sign in again.* Session not checkable: *EasyApply
  couldn't check who is signed in.*
- The account tag is part of its session state, sent on every call through one
  helper (a test parses `src/` and fails on any other route to `/api/backend`),
  and keys in-flight call joining, the per-posting save join, remembered saves
  and the panel's flow key.
- Before filling a form it re-asks the web app who is signed in and fills only
  for the same account.

## 7. Account switching behavior

| Situation | Web UI | Extension |
|---|---|---|
| Another account signs in (other tab / window) | reloads for it: *This browser was signed in to a different EasyApply account in another tab, so this page was reloaded for that account.* | the next action is refused (409), everything of the previous account is cleared: *You're signed in to EasyApply as a different account now.* + Reload |
| Signed out in another tab | reloads to the sign-in: *You signed out of EasyApply in another tab.* | the next action says the session expired and offers sign-in |
| Session expired | keeps the page, notice + sign-in in a new tab | *Your EasyApply session expired. Sign in again.* |
| Stale tab not in focus | re-checked on focus, visibility, back-forward restore, and when another tab renders or signs in/out | re-checked before fill; every call carries the tag |
| Cached state | the query cache is cleared on every account change | flow state, proposals and remembered saves are keyed by the account tag |

The proxy decides: signed out → 401 first, then a tag that is not the cookie's
account → 409, never forwarded.

## 8. Ownership / security audit

Four independent passes, each adversarially verified:

1. **Ownership audit (before coding).** One real medium — the proxy attached
   the operator token to `/internal/*` for any signed-in user — fixed by the
   operator role gate (§7 of authentication.md). Missing cross-user tests
   (profile versions, snapshots, confirm, reprocess, job dedup, foreign match
   ids, job-scoped tailoring lists, identical 404 bodies) are now covered by the
   API's A↔B matrix. The remaining informational items (lookups that rely on
   an ownership check made earlier in the same request, globally sequential
   ids) are recorded for defence in depth; the audit found no route through
   which they give cross-user access.
2. **Design review (three lenses).** Adopted: the operator role gate (high),
   metadata never refusing (high), the tenant-GUID key (medium), a
   development-mode API refusing bearer assertions (high — every Entra user
   would otherwise have merged into `DEV_USER_EMAIL`), the tag on every fetch
   (medium), stale tabs re-checked (medium), the extension keyed by tag
   (medium), pinned `AUTH_MODE` in every harness (medium), the account tag keyed
   to the account not the session (medium), per-state transaction cookies,
   `APP_ORIGIN` host handling, import-time checks moved into the lifespan.
3. **Security code review of the implementation (three lenses, each finding
   re-checked by a skeptic against the code).** 19 findings, all confirmed:

   | Sev. | Finding | Outcome |
   |---|---|---|
   | high | Under `entra`, `ENVIRONMENT=development` with no operator token left the operator API open to any direct caller (the old development branch ran before the role check) | **fixed**, tested |
   | medium | The proxy classified `/internal` on the first segment; `jobs/..%2Finternal/…` decodes to a `..` segment that resolves past it (and `..%2F..%2F` outside `/api/v1`) | **fixed**: non-plain segments are a 404, never forwarded; unit + browser tested |
   | medium | A session expiry noticed by a focus check or a polled query reloaded the page and lost unsaved edits | **fixed**: expiry keeps the page |
   | low | Session and transaction cookies unprefixed: a sibling subdomain could plant its own valid session or transaction | **fixed**: `__Host-` over https |
   | low | The session was not bound to the deployment (two deployments sharing a secret in one tenant accept each other's sessions, operator flag included) | **fixed**: `aud` = client id |
   | low | The replay guard evicted unexpired ids when full | **fixed**: full means refuse |
   | low | A lone surrogate in a name or email failed every request (500) | **fixed** on both tiers |
   | low | `?auth_error=__proto__` crashed the page | **fixed** |
   | low | Query hooks retried 401 / `session_changed` | **fixed** |
   | low | Sign-out announced itself to other tabs even when it failed; a flag stuck | **fixed** |
   | low | The "leaving" guard was never reset after an abandoned sign-out navigation | **fixed** |
   | low | Two live sessions of one person with different metadata alternate the stored display values | documented (display only) |
   | info | `return_to` could normalise to `//host` (always on our origin, but against the stated rule) | **fixed** |
   | info | An unreachable JWKS reported `failed` instead of `unavailable` | **fixed** |
   | info | Sign-out with Microsoft down and discovery cached lands on Microsoft's error page | documented |
   | info | The operator surface skipped the development-mode bearer refusal | **fixed** |
   | info | Model docstring said the issuer column holds the issuer URL | **fixed** |
   | info | The extension said "Sign in" when it could not check the session | **fixed** |
   | info | The sign-out URL check refused `http://[::1]` | **fixed** |

4. **The brief's attack list**, as tests (§13).

Ownership rules themselves were not changed: services enforce `user_id` exactly
as before; only who the `user_id` is changed.

## 9. Database changes

Migration `f1a9d3c5b7e2_external_user_identities` (down-revision `d4a7c2e9b815`):

- `users.email` becomes nullable (Entra-provisioned users have none; it stays
  the development identity's unique key).
- New `user_identities`: `id`, `user_id` → `users.id` (ON DELETE RESTRICT),
  `issuer` (512), `subject` (255), `email` (320, null), `display_name` (255,
  null), timestamps; `UNIQUE (issuer, subject)`; `CHECK` non-empty issuer and
  subject; index on `user_id`.
- Downgrade refuses while any user has a null email (it would have to delete
  or invent identities); otherwise round-trips.
- Tested in a scratch database: upgrade keeps existing users, constraints and
  RESTRICT behave, downgrade guard, no model/migration drift.
- **Not applied to the developer's database** (`easyapply`, still at
  `d4a7c2e9b815`). Development mode does not need it; entra mode does:
  `pg_dump`, then `alembic upgrade head`.

## 10. API changes

No domain endpoint, request or response schema changed (the OpenAPI contract
and `packages/shared/src/api-schema.ts` are unchanged). Identity contracts:

| Where | Change |
|---|---|
| API, `AUTH_MODE=entra` | identity is `Authorization: Bearer <assertion>`; `X-Dev-User-Email` is 401; every refusal is 401 `unauthenticated` |
| API, development | a request carrying `Authorization` is 401 (users) / 403 (operators): the mode mismatch |
| API operator routes, entra | token **and** operator role; otherwise 403 |
| Web `GET /api/auth/session` | adds `name` and `sessionTag` (additive); `email` may be null under entra |
| Web `POST /api/auth/sign-in` | entra: form POST → 303 to Microsoft |
| Web `GET /api/auth/callback`, `/signed-out` | new |
| Web `POST /api/auth/sign-out` | entra adds `logoutUrl` |
| Web proxy | `X-EasyApply-Session` request header → 409 `session_changed`; `/internal/*` 404 for non-operators; 404 for non-plain path segments |

## 11. Configuration changes

Web (`apps/web/.env.example`): `AUTH_MODE=entra`, `APP_ORIGIN`,
`ENTRA_TENANT_ID`, `ENTRA_AUTHORITY`, `ENTRA_ISSUER` (defaulted),
`ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`, `SESSION_SECRET` (+ `_PREVIOUS`),
`SESSION_MAX_AGE_SECONDS`, `IDENTITY_ASSERTION_SECRET`, `ENTRA_OPERATOR_ROLE`,
`PROXY_ALLOWED_HOSTS` (must include `APP_ORIGIN`'s host).
API (`services/api/.env.example`, new): `AUTH_MODE=entra`,
`IDENTITY_ASSERTION_SECRET` (+ `_PREVIOUS`), `ENTRA_TENANT_ID`, `ENTRA_ISSUER`,
`ENTRA_OPERATOR_ROLE`, with `ENVIRONMENT=production` and `OPERATOR_API_TOKEN`.

Fail-closed: an unknown mode or anything missing is `misconfigured` on the web
(500, reason naming settings only) and a startup failure on the API (checked in
the lifespan, and per request); production without entra refuses every request;
the API refuses the web-only value `dev-session` by name. Configuration errors
never contain a secret (tested for each).

## 12. Files changed

Against the snapshot taken before this phase: **28 files added, 82 changed,
none removed** (build output, dependencies and `.env` files excluded;
`PROJECT.md` was changed by another session, not by this work).

**API** (`services/api`)
- New: `app/api/identity.py` (the seam: picks the resolver by `AUTH_MODE`),
  `app/api/assertion_identity.py` (assertion validation, replay guard,
  provisioning), `app/models/user_identity.py`,
  `alembic/versions/f1a9d3c5b7e2_external_user_identities.py`, `.env.example`,
  `tests/test_identity_entra.py`, `tests/test_migration_user_identities.py`.
- Changed: `app/api/dev_identity.py` (refuses a bearer; now one mode of the
  seam), `app/api/operator_identity.py` (role gate under entra; no open
  posture), `app/core/config.py` (identity settings and checks), `app/main.py`
  (identity check in the lifespan), `app/models/{user,__init__}.py` (nullable
  email; export), `app/api/v1/{jobs,matches,profile,resume,tailoring}.py` (one
  import line each), `requirements.txt` (PyJWT), `tests/conftest.py` (pins
  development mode), `tests/test_security_errors.py` (import).

**Web** (`apps/web`)
- New: `src/server/auth/{oidc,sign-in-flow,transaction,entra-session,assertion}.ts`,
  `src/app/api/auth/callback/route.ts`, `src/app/signed-out/page.tsx`,
  `src/lib/session.ts`, `src/components/shell/{microsoft-sign-in,sign-in-notice}.tsx`,
  tests `src/server/auth/entra.test.ts`, `src/app/api/auth/entra-routes.test.ts`,
  `src/app/api/backend/[...path]/entra.test.ts`, `src/lib/session.test.ts`,
  `src/components/shell/entra-sign-in.test.tsx`, `src/test/entra.ts` (fake
  tenant), `e2e/specs/entra.spec.ts`, `e2e/support/mock-entra.mjs` (local
  provider).
- Changed: `src/server/auth/{config,identity,responses,session-token}.ts`,
  `src/server/request-guard.ts`, `src/app/api/auth/{sign-in,sign-out,session}/route.ts`,
  `src/app/api/backend/[...path]/route.ts` (tag, operator and path checks),
  `src/app/{layout,providers}.tsx`, `src/app/internal/layout.tsx`,
  `src/components/shell/sign-out-button.tsx`, `src/lib/api/{client,resume,tailoring}.ts`
  (one fetch helper), `src/lib/api/*-queries.ts` and `queries.ts` (no retry of a
  session failure), tests `src/server/auth/auth.test.ts`,
  `src/app/api/auth/auth-routes.test.ts`, `e2e/specs/session.spec.ts`,
  `e2e/support/env.{cjs,d.cts}`, `playwright.config.ts` (three more servers;
  the API's mode pinned), `.env.example`, `package.json` (jose).

**Extension** (`apps/extension`) — session and account-tag handling only:
`src/api/{session,client,jobs,calls}.ts`, `src/shared/messages.ts`,
`src/background/{service,index}.ts`, `src/workflow/job-flow.ts`,
`src/popup/{app,port,describe,job-panel,job-view,application-panel}.ts`; tests
(`test/backend-fetch.test.ts` new, twelve updated, `test/support/fake-api.ts`);
`e2e/{env,support,stub-server,workflow.spec}.ts`, `e2e/real-stack.mjs`
(`--entra`); `README.md`. **Unchanged**: `src/forms/*` (fill and
classification rules), `src/matching/*`, `src/adapters/*`, `src/extraction/*`,
`src/profile/*`, `src/review/*`, `src/jobs/*`, the manifest.

**Shared**: `packages/shared/src/index.ts` (`WebSession` fields, `sessionLabel`,
header and code constants). `package-lock.json` (jose).

**Docs**: `docs/authentication.md` (rewritten), `docs/chrome-extension.md`,
`README.md`, new `docs/production-infrastructure-2026-09-26.md` and this report.

**Protected areas, verified unchanged against the snapshot** (every file not
listed above is byte-identical to it): all of `services/api/app/services/`
(matching, resume extraction and structuring, tailoring grounding and
reconciliation, AI prompts and providers) and `services/api/app/schemas/`,
`services/api/evaluation/` (the matching benchmark and the evaluation sets),
the extension's matching, form-fill and adapter code, and the web app's match,
tailoring, job, profile and resume components.

## 13. Security tests

API (`tests/test_identity_entra.py`, 74): forged / missing / expired /
not-yet-valid / wrong issuer / wrong audience / wrong signature / `none` and
RS256 algorithm confusion / wrong `idp` / wrong tenant / non-GUID subject /
over-long lifetime / malformed / replayed assertions — each the same 401 and
nobody provisioned; `X-Dev-User-Email`, `X-User-ID` and other identity-shaped
headers ignored or refused; secret rotation; configuration matrix and startup
refusal; mode mismatch both ways; operator token/role matrix including the
review's high finding; replay guard capacity; metadata cases; logging carries
categories, never tokens.
Web (vitest): configuration, v2 session forgery/edits/version confusion/
lifetime/deployment binding, sealed transactions, `return_to` open-redirect
matrix, PKCE vector, ID token validation against real RS256 keys (issuer,
audience, azp, nonce, tenant, oid, roles, age, unknown key, HS256 substitution),
login CSRF (an attacker's code and state rejected before redemption), cancelled
and failed sign-ins, no token in any response, header or log, `__Host-`
cookies over https, proxy assertion contents, account tag 409, operator 404,
path-traversal 404, the tag on every client request.
Browser (entra e2e): forged cookie, forged headers at the web app and straight
at the API, forged callback, wrong-audience ID token, cancelled sign-in,
operator gating including encoded traversal.

## 14. Multi-user tests

- API A↔B matrix in both directions over every owned resource (profile,
  versions, snapshots, confirm, resumes, results, reprocess, jobs,
  requirements, matches, tailored resumes, artifacts): foreign ids are 404s
  identical to missing ids, never a download; lists contain only one's own.
- Web e2e: two people in one browser (and a same-email different-`oid` twin);
  **User A's full journey (profile → resume → confirmed version → job → match →
  tailored resume), sign-out, User B sees nothing and every one of A's ids is a
  404**; a stale tab after a switch; sign-out in another tab.
- Extension real stack under entra: two accounts in one browser (each sees
  only their own job, match and resume; the other's ids 404), a claimed
  identity header ignored, an account switch while the popup is open.

## 15. Browser tests

All in real Chromium against real servers (FastAPI + PostgreSQL + `next start`
production builds); real sign-in goes through the local External ID-shaped
provider, nothing else is stubbed.

**Web, `apps/web/e2e/specs/entra.spec.ts` (11, AUTH_MODE=entra on both tiers):**
signed-out gating and the proxy's 401 (plus an accessibility scan of the
sign-in page); sign-in returns to the page it began on with an HttpOnly
SameSite=Lax session, no transaction cookie left, nothing in local or session
storage, `document.cookie` empty; two people and a same-email twin in one
browser; **User A's whole journey and User B seeing none of it**; a stale tab
after an account switch (reloads with the notice, and a request carrying the
old tag is a 409 that writes nothing) and after a sign-out elsewhere; a session
that ends mid-edit keeping the form, then saving once the same person signs in
again in another tab; an expiry never discarding the page; cancelled sign-in,
forged callback, wrong-audience ID token; forged cookie and forged headers at
the web app and straight at the API; operator pages 404 for an ordinary
account (including an encoded traversal) and served for the operator; an
account with no email and no name.

**Extension, `apps/extension/e2e` real stack with `--entra` (15):** the whole
workflow (posting → save → requirements → match → tailored resume → the
application form filled, never submitted) compared against what the API
stored; two accounts in one browser, each seeing only its own job, match and
resume, the other's ids 404; a claimed identity header ignored; a session
ending mid-flow; **another account signing in while the popup is open** — the
next action says so and nothing of the first account is left, including its
form proposals; a malformed cookie; job text treated as data; Lever.

The same extension suite also passes against the development-session stack
and against the recording stub (§16).

## 16. Full regression results

Final runs, sequential, after every fix, on 2026-09-26:

| Suite | Command | Result |
|---|---|---|
| API, everything | `pytest` (services/api) | **2,754 passed**, 13 skipped, 0 failed (3 min) — includes the 78 new identity and migration tests |
| API migrations | `alembic heads`; upgrade/downgrade/constraints/drift tests | single head `f1a9d3c5b7e2`; 4/4 |
| Web types | `tsc --noEmit` | clean |
| Web unit | `vitest run` | **641 passed**, 0 failed (139 in the five new test files) |
| Web lint | `eslint` over `src`, the specs and the new support files | 0 errors (4 warnings, all in files this work did not touch) |
| Web browser | `prepare` + `playwright test` (all specs) | **27 passed**, 0 failed — accessibility 2, entra 11, identity 3, journey 4, product flow 1, resume→profile 2, session 4 |
| Extension types | `tsc --noEmit` | clean |
| Extension unit | `vitest run` | **546 passed**, 0 failed |
| Extension lint | `eslint` over `src`, `test`, `e2e` | 0 errors, 0 warnings |
| Extension build | `npm run build` | built |
| Extension browser, stub | `playwright test` | **15 passed** |
| Extension browser, real stack, dev-session | `node e2e/real-stack.mjs` | **15 passed**, 0 skipped |
| Extension browser, real stack, **entra** | `node e2e/real-stack.mjs --entra` | **15 passed**, 0 skipped |
| Hygiene | line endings and control bytes of all 110 added/changed text files | no mixed endings, every changed file kept its original ending, no control bytes |

The developer's own servers (web :3000, API :8000) ran throughout and were not
touched; the suites use their own ports, databases and build directories and
released all of them. One unrelated, pre-existing jsdom message ("navigation
not implemented", from a match-page test clicking a link) appears in the web
unit run, as it did before this phase.

## 17. Known limitations

- **Verified against a local provider, not a real tenant** (the top line).
- The §28 acceptance flow is proven in two halves rather than one continuous
  run: the web journey (profile from a resume through a tailored resume, then
  User B) under real sign-in, and the extension workflow (save → requirements
  → match → tailor → reviewed fill, then User B) under real sign-in with a
  profile seeded through the API.
- Stateless sessions: no per-user server-side revocation; Entra revocation and
  role removal lag by up to the session lifetime.
- Per-process replay guard (a shared store for multi-instance).
- Existing development data is unreachable under entra (no email linking, by
  decision); joining a specific account is a future operator tool, not built.
- The two documented review findings (§8); no HSTS from the app itself.
- The developer's database has not been migrated.

## 18. Remaining production blockers

1. A real External ID tenant, configured per §19, and the flows of §15 run by
   hand against it — the step that turns "locally verified" into "integration
   verified".
2. Source control and CI (there is no git repository).
3. Durable file storage (Blob) instead of the API's local disk.
4. Managed PostgreSQL with the migration applied as a release step.
5. Secrets in Key Vault; the API reachable only from the web app.
6. Edge: TLS, HSTS, WAF, rate limiting; monitoring and alerting on the
   identity events.
7. A Chrome Web Store identity for the extension, named in
   `PROXY_ALLOWED_EXTENSION_IDS`, and a production build pointed at `APP_ORIGIN`.

All of it, item by item: [production-infrastructure-2026-09-26.md](production-infrastructure-2026-09-26.md).

## 19. Exact manual setup instructions

**Tenant** (Microsoft Entra admin center, the external tenant):

1. Note the **Directory (tenant) ID** and the tenant name (`<name>.ciamlogin.com`).
2. App registrations → New registration: "EasyApply", accounts in this
   organizational directory only.
3. Authentication → Add a platform → **Web** (not SPA). Redirect URIs:
   `https://<your host>/api/auth/callback` and `https://<your host>/signed-out`.
4. Certificates & secrets → New client secret; copy the **value** once; record
   its expiry.
5. API permissions → Microsoft Graph → Delegated: `openid`, `profile`, `email` →
   **Grant admin consent**.
6. Token configuration → Add optional claim → ID → `email`.
7. App roles → Create: display name "EasyApply operator", allowed member types
   Users/Groups, value **`EasyApply.Operator`**. Enterprise applications →
   EasyApply → Users and groups → assign it to operators only.
8. External Identities → User flows → a sign-up-and-sign-in flow with the
   identity providers you want, **collect Display Name**; Applications → add
   EasyApply.
9. Open `https://<name>.ciamlogin.com/<tenant-id>/v2.0/.well-known/openid-configuration`
   and note `issuer` (expected `https://<tenant-id>.ciamlogin.com/<tenant-id>/v2.0`).

**Web** (`apps/web/.env.local`, or the host's settings):

```
AUTH_MODE=entra
APP_ORIGIN=https://<your host>
PROXY_ALLOWED_HOSTS=<your host>
ENTRA_TENANT_ID=<tenant id>
ENTRA_AUTHORITY=https://<name>.ciamlogin.com/<tenant id>/v2.0
ENTRA_ISSUER=<issuer from step 9>
ENTRA_CLIENT_ID=<application (client) id>
ENTRA_CLIENT_SECRET=<secret value>
SESSION_SECRET=<32+ random characters>
IDENTITY_ASSERTION_SECRET=<32+ random characters, not SESSION_SECRET>
OPERATOR_API_TOKEN=<32+ random characters>
API_BASE_URL=<the API's internal URL>
PROXY_ALLOWED_EXTENSION_IDS=<the extension's id>
```

**API** (`services/api/.env`):

```
AUTH_MODE=entra
ENVIRONMENT=production
IDENTITY_ASSERTION_SECRET=<the same value as the web app's>
ENTRA_TENANT_ID=<tenant id>
ENTRA_ISSUER=<issuer from step 9>
OPERATOR_API_TOKEN=<the same value as the web app's>
API_ALLOWED_HOSTS=<the hostname the web app calls the API by>
DATABASE_URL=<…>
```

**Database**: back it up, then `alembic upgrade head` (to `f1a9d3c5b7e2`).
**Extension**: `EASYAPPLY_WEB_URL=https://<your host> npm run build` in
`apps/extension`.
**Generating secrets**: e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

**Verify by hand, in order** (this is the integration verification not yet
done): sign in as a new user (lands where it began; header shows the account);
sign-out ends the Microsoft session too (the next sign-in shows the account
picker); cancel at Microsoft (the cancelled message); a second user in the same
browser sees nothing of the first; an operator reaches `/internal`, a
non-operator gets 404; the extension shows *Saving as …*, saves, matches,
tailors and fills for the signed-in user, and after a switch says *You're
signed in to EasyApply as a different account now.*; the web server log shows
`auth.sign_in.success` and no token, email or object id.

## 20. Recommended next phase

1. **Real-tenant integration** (small, first): create the tenant (§19), run the
   manual verification, and point the existing browser suites' entra flows at
   it with two test users — the local provider was built to the same protocol,
   so the suites should need configuration, not code. Record what differs.
2. **Source control and CI**, so every later change runs the four suites.
3. **Infrastructure** in the order of the infrastructure report's blocking
   items: storage, database, secrets and network isolation, edge, observability.
4. Then the extension's store listing and a production build.

Deployment to Azure was not started, as instructed.
