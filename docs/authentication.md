# Authentication — the web app, the Chrome extension and the API

How EasyApply decides who a request is for, in development and under real
sign-in with **Microsoft Entra External ID**. The short version: **there is one
session per browser, held by the web app's server; both browser clients present
it; neither ever holds a credential; the web app validates the Microsoft
sign-in once and tells the API who each request is for with a short signed
assertion; and the API maps that to an account by the person's immutable Entra
object id — never by email.**

```
browser profile
 ├─ web UI tab                (same origin as the web app)
 └─ extension service worker  (chrome-extension://jjhc…; host permission for the web app)
        │  both present the SAME session cookie — HttpOnly, so neither can read it
        │  both send X-EasyApply-Session: <account tag> with every API request
        ▼
web app server (apps/web)
   0. sign-in     /api/auth/sign-in → Microsoft → /api/auth/callback   (entra only)
                  src/server/auth/sign-in-flow.ts, oidc.ts, transaction.ts
   1. admission   src/server/request-guard.ts
                  Host check on everything; for writes: same-origin (web UI),
                  or the allowed extension's three workflow writes only
   2. identity    src/server/auth/identity.ts  → resolveIdentity(cookies)
                  who the request is for + the credential to attach upstream
   3. consistency the request's account tag must be the cookie's account (409)
   4. operators   /internal/* only for the operator app role (404 otherwise)
   5. forward     /api/backend/*  — upstream headers built fresh, never copied
        │  development / dev-session: X-Dev-User-Email
        │  entra: Authorization: Bearer <60-second HS256 assertion>
        ▼
API (services/api)
   identity       app/api/identity.py  — THE seam; AUTH_MODE picks the resolver
                  development: app/api/dev_identity.py (refused in production)
                  entra:       app/api/assertion_identity.py → user_identities
```

Nothing in matching, tailoring, resume intelligence, AI, or the extension's
matching, tailoring and autofill logic changed. The extension never submits an
application; that boundary is permanent and unaffected.

## 1. Decisions

### 1.1 One session per browser, shared by both clients (unchanged)

The extension's requests carry the browser's session cookie for the web app,
exactly as the web UI's do, and the web app's server decides who that is. The
extension has no `cookies` permission and stores no token. Measured on
2026-09-26 in Playwright's Chromium: a `fetch` from the extension's service
worker, holding host permission for the web app, **sends** the web app's
`HttpOnly; SameSite=Lax` and `Strict` cookies with `credentials: "include"`.
The extension's browser suite asserts the cookie on the wire on every run
(`apps/extension/e2e/capture.spec.ts`). Real sign-in did not change this: it
changes who issues the cookie, not how the clients use it. A second
authentication mechanism for the extension was not introduced, because the
shared session is not impossible — it works.

### 1.2 How the API learns who a request is for: an assertion, not a Microsoft token

| | Chosen: web-minted assertion | Rejected: forward Microsoft access tokens |
|---|---|---|
| What the API receives | `Authorization: Bearer <HS256 JWT>` minted per request by the web server, 60-second life, single-use `jti` | an Entra access token for an EasyApply API scope |
| What the web server must keep | nothing from Microsoft — the ID token is validated once and discarded | access and refresh tokens per session, server-side (a store) or encrypted in the cookie (cookie size), refreshed under concurrency |
| Moving parts | one shared secret between two servers the same operator runs | a second app registration (API), scopes, consent, a token store, refresh races |
| What an intercepted credential is worth | one request, within 60 seconds, to the API only (`aud`), once (`jti`) | up to the access-token lifetime (60–90 minutes), anywhere that audience is accepted |

The trade is honest: the API trusts the web server to have validated the
sign-in. That is already true of every other request today — the web server is
where identity is attached — and it is enforced cryptographically (only the
web server holds `IDENTITY_ASSERTION_SECRET`, which is never the session
secret) rather than by network position. If the API ever needs callers other
than this web server, forwarding real access tokens becomes the right design
and slots into the same seam (`app/api/identity.py`).

### 1.3 The account key is the Entra object id, never the email

`user_identities (issuer, subject)` is unique, where `issuer` is the canonical
key `entra:<tenant GUID>` and `subject` is the lower-cased `oid` claim:

- `oid` is immutable, the same for the person across every app registration in
  the tenant, and never reassigned. `sub` is pairwise (different per app) and
  would change if the app were re-registered; email and name are mutable
  profile data a person (or an administrator) can change.
- The tenant GUID, not the issuer URL: External ID's hostnames do not follow one
  pattern (discovery on the tenant-name host, tokens from the tenant-id host,
  custom domains later), and correcting `ENTRA_ISSUER` must never orphan every
  account. The issuer URL is still checked exactly on every token — it is
  validation, not the key.
- **No email linking.** Two Entra users with the same address are two accounts
  (tested); a person who changes their address keeps their account (tested); a
  first Microsoft sign-in never attaches to an existing development-mode
  account with the same email (tested). Linking by email would let whoever
  controls an address in the tenant — including a self-service sign-up with an
  unverified or recycled address — take over the account that address once
  belonged to. It was considered and rejected; if an existing account must be
  attached to an Entra identity, that is an operator action by user id with the
  person's `oid`, not something the application infers (§11).
- `users.email` became nullable: Entra-provisioned users have none (the address
  lives on `user_identities.email` as display metadata). It remains the
  development identity's key, so `X-Dev-User-Email` can never resolve to an
  Entra-provisioned account.

## 2. Modes

Both tiers have an `AUTH_MODE`; they must agree, and each refuses the other's
mismatch rather than guessing.

| Web `AUTH_MODE` | API `AUTH_MODE` | Who a request is for | Upstream credential | Sign-in |
|---|---|---|---|---|
| `development` (default) | `development` | `DEV_USER_EMAIL`, every request | `X-Dev-User-Email` | none |
| `dev-session` | `development` | the signed v1 session cookie | `X-Dev-User-Email` | development form: any address, no password |
| `entra` | `entra` | the signed v2 session cookie issued at the callback | `Authorization: Bearer <assertion>` | Microsoft Entra External ID |

Fail-closed rules:

- **Web.** An unknown mode, or a mode missing anything it needs, is
  `misconfigured`: every page says so and every proxied request answers 500
  `auth_misconfigured`, with the reason (setting names, never values) in the
  server log. Never a fallback to the development identity.
- **API, entra.** Missing or short `IDENTITY_ASSERTION_SECRET`, a tenant that is
  not a GUID, an issuer that is not https (loopback excepted) or does not name
  the tenant: startup fails (checked in the FastAPI lifespan, so uvicorn exits),
  and the same check runs on every request, which then answers 401.
- **API, development.** Refuses every identified request under
  `ENVIRONMENT=production`. Refuses any request carrying `Authorization` — that
  is a web app in entra mode in front of an API left in development, which
  would otherwise merge every Microsoft account into `DEV_USER_EMAIL`; logged at
  error level (`dev_identity.refused bearer_in_development_mode`).
- **API, entra.** Refuses `X-Dev-User-Email` outright (401), rather than
  ignoring it.
- **API, `dev-session`.** A web-only value; the API refuses to start with it and
  says so.
- **Production without entra.** The API logs `identity.production_without_sign_in`
  at startup and refuses every request (the development identity is refused in
  production).

**`dev-session` is not authentication.** It exists for the test suites and for
several people on one development server.

## 3. Real sign-in, step by step (AUTH_MODE=entra)

**Start — `POST /api/auth/sign-in`** (a same-origin form POST from "Sign in with
Microsoft"; `src/server/auth/sign-in-flow.ts`):

1. Host allowed; Origin/Sec-Fetch-Site same-origin (a cross-site page cannot
   start a sign-in — login CSRF).
2. If the request's host is not `APP_ORIGIN`'s host (e.g. `localhost` vs
   `127.0.0.1`), 303 to `APP_ORIGIN` first: the transaction cookie must be set on
   the host Microsoft will return to.
3. `return_to` sanitised: visible ASCII only, one leading `/`, no backslash,
   same origin after resolution, normalised; anything that normalises to
   `//…` or to `/api/auth/*` becomes `/`.
4. Discovery (`<ENTRA_AUTHORITY>/.well-known/openid-configuration`, cached one
   hour; the document's `issuer` must equal the configured `ENTRA_ISSUER`
   exactly; endpoints must be https, loopback excepted).
5. `state`, `nonce` and a PKCE `verifier` — 32 random bytes each — are sealed
   (AES-256-GCM, key derived from `SESSION_SECRET` by HKDF for this purpose
   only) into a cookie **named after the state** — one per transaction, so two
   tabs signing in at once do not overwrite each other:
   - over https: `__Host-easyapply_oidc_<16 chars of SHA-256(state)>; Path=/;
     Max-Age=600; HttpOnly; SameSite=Lax; Secure`. The `__Host-` prefix means
     neither plain http nor a sibling subdomain can plant a transaction of its
     own (a planted one, with the attacker's own code and state, would otherwise
     pass the callback's check);
   - over loopback http (development, the test stacks):
     `easyapply_oidc_<…>; Path=/api/auth/callback; Max-Age=600; HttpOnly;
     SameSite=Lax`.
6. 303 to the authorization endpoint: `response_type=code`,
   `response_mode=query`, `scope=openid profile email`, `code_challenge`
   (S256), `state`, `nonce`, `prompt=select_account` (on a shared computer,
   never silently the previous person).

**Return — `GET /api/auth/callback`:**

1. `state` first: its transaction cookie must exist, open under the session
   secret (or the previous one), be under ten minutes old, and hold the same
   state. Otherwise `expired` — and nothing the provider sent is looked at. A
   callback URL for a sign-in started in another browser (an attacker's code
   and state) fails here, before any code is redeemed (tested).
2. `error=access_denied` → `cancelled`; any other provider error → `failed`
   (logged: the error code and the AADSTS number only).
3. The code is redeemed at the token endpoint with the client secret
   (`client_secret_post`), the `code_verifier` and the registered
   `redirect_uri`; 10-second timeout; no redirects followed. Only `id_token` is
   read from the answer; the access and refresh tokens are dropped unread.
4. The ID token is validated (jose `jwtVerify`): RS256 only; key from the
   discovered `jwks_uri` by `kid` (unknown `kid` → refetch, rate-limited);
   `iss` = the pinned issuer; `aud` = our client id and a single audience;
   `azp`, if present, = our client id; `exp`, `iat`, `nonce`, `oid`, `tid`,
   `sub` present; 60 s clock tolerance; issued within 10 minutes; `nonce` = the
   transaction's; `tid` = our tenant; `oid` a GUID; `roles`, if present, a list
   of strings.
5. Display metadata is cleaned, never a reason to fail: `email` and `name` are
   NFC-normalised, C0/C1 control characters removed, a lone surrogate replaced
   by U+FFFD, cut to 320/255 code points, null if empty; the placeholder name
   `unknown` (a user flow that did not collect a display name) is null.
6. The EasyApply session is issued (§4), the transaction cookie cleared, and
   303 to `APP_ORIGIN` + `return_to`. A failed sign-in never touches a session
   the browser already holds.

Every failure lands on `/?auth_error=cancelled|expired|unavailable|failed` and
the page says, respectively: *Sign-in was cancelled. Nothing was changed.* /
*That sign-in took too long or was already used. Sign in again.* / *Microsoft
sign-in could not be reached. Try again in a moment.* / *Signing in with
Microsoft did not work. Try again.*

## 4. Sessions: lifetime, expiry, rotation, logout

```
dev-session  easyapply_session = v1.<b64url {sub, iat, exp}>.<b64url HMAC-SHA256(SESSION_SECRET, "v1." + payload)>
entra        __Host-easyapply_session (https) or easyapply_session (loopback http)
               = v2.<b64url {typ:"entra", iss, aud, tid, oid, email, name, op, sid, iat, exp}>.<b64url HMAC-SHA256(SESSION_SECRET, "v2." + payload)>
```

- **Cookie.** `HttpOnly; SameSite=Lax; Path=/; Max-Age=<lifetime>`; `Secure`
  whenever the app is https (under entra `APP_ORIGIN` must be https unless it
  is loopback). Host-only (no `Domain`). Under entra over https the name carries
  the `__Host-` prefix, which browsers accept only with Secure, `Path=/` and no
  `Domain` — so a page on a sibling subdomain cannot plant its own valid
  session (login CSRF by cookie tossing), and a cookie of the plain name is not
  read at all. Lax so that arriving from a link — an email, the extension's
  "Open in EasyApply", Microsoft's redirect back — is signed in; CSRF is
  admission's job, Lax a second layer.
- **Bound to this deployment.** `aud` is this app registration's client id and
  must match, as `iss` and `tid` must: a session issued by another deployment in
  the same tenant — even one that (wrongly) shares `SESSION_SECRET`, and even
  one carrying its operator flag — is signed out here.
- **Signed, not encrypted.** It holds nothing the person does not know about
  themselves; the signature is what stops a browser from editing it into
  someone else. The version prefix is inside the MAC and `typ` is checked, so a
  v1 development session can never pass as a v2 one even under the same secret.
  Exact key set; every way of being wrong is the same "signed out".
- **No Microsoft token is in it or anywhere else.** `sid` is 16 random bytes
  minted by EasyApply per session. `op` is whether the ID token carried the
  operator app role.
- **Lifetime.** Absolute, from sign-in: `SESSION_MAX_AGE_SECONDS`, default
  **8 hours** under entra (12 under dev-session), 60 s – 30 days. Not sliding
  and not refreshed: after it, the person signs in again (usually one click —
  Microsoft's own session is still there). The lifetime is enforced from the
  **current** configuration too, so shortening it ends longer sessions at once.
- **Expiry in the clients.** The web UI never throws a page away on an expiry —
  it may hold unsaved edits, and it is the same person's: it shows *Your
  EasyApply session expired. Sign in again.* with a link that signs in in a new
  tab; once the same person has signed in (same account tag) the notice goes and
  retrying works. The next full load of any page is the sign-in. The extension
  says *Your EasyApply session expired. Sign in again.*
- **Rotation.** `SESSION_SECRET_PREVIOUS` is accepted for verification (never
  for signing) while a rotation drains; keep it for `SESSION_MAX_AGE_SECONDS`,
  then remove it. Rotating without it ends **every** session immediately — the
  emergency revocation. `IDENTITY_ASSERTION_SECRET` rotates the same way on the
  API (`IDENTITY_ASSERTION_SECRET_PREVIOUS`); assertions live 60 seconds, so a
  minute's overlap is enough (update the API first, then the web app).
- **Logout.** `POST /api/auth/sign-out` (same-origin) clears the cookie and
  returns `logoutUrl`: the tenant's `end_session_endpoint` with
  `post_logout_redirect_uri=${APP_ORIGIN}/signed-out`. The client navigates
  there, so **the Microsoft session ends too**, and Microsoft returns to
  `/signed-out`, which says *You signed out of EasyApply.* Other open tabs are
  told (BroadcastChannel) and stop showing the account. If Microsoft cannot be
  reached, EasyApply's session still ends and the browser goes straight to
  `/signed-out`.
- **Stateless.** Sign-out clears the cookie; it does not revoke a copy of the
  token taken before (valid until its `exp`). There is no server-side session
  store; revocation is the lifetime and secret rotation.

**Entra session vs EasyApply session.** They are independent. Microsoft's
session (its own cookies on the ciamlogin.com domain) decides whether "Sign in
with Microsoft" needs a password; EasyApply's session (`easyapply_session`)
decides whether EasyApply knows you. EasyApply's can expire while Microsoft's
lasts (signing in again is then one click, but always through the account
picker); Microsoft's can end (an administrator revokes sessions, the password
changes) while EasyApply's lasts until its own `exp` — **revoking a person in
Entra takes effect in EasyApply at their next sign-in, at most
`SESSION_MAX_AGE_SECONDS` later.** Removing someone's operator role has the same
lag. Signing out of EasyApply ends both.

## 5. The API side (AUTH_MODE=entra)

`app/api/assertion_identity.py` validates, in order, and refuses with one
generic 401 (`unauthenticated`, *The request did not carry a valid EasyApply
sign-in.*) and a logged reason category:

`dev_header` (X-Dev-User-Email present) · `misconfigured` · `missing` ·
`malformed` (not `Bearer <one token>`, over 4 KB) · `bad_algorithm` (header
`alg` ≠ HS256 — checked before any key is used, so `none` and RS/HS confusion
die here) · `bad_signature` (current then previous secret) · `expired` ·
`not_yet_valid` · `wrong_audience` (≠ `easyapply-api`) · `wrong_issuer`
(≠ `easyapply-web`) · `too_long_lived` (over 300 s) · `wrong_idp` (≠
`ENTRA_ISSUER`) · `wrong_tenant` · `bad_subject` (not a GUID) · `jti` shape ·
`replayed` (each `jti` once per process) · `replay_guard_full` (see below).

Then `(entra:<tid>, oid)` is looked up in `user_identities`; first sight
provisions a user and its mapping in one transaction on the request's own
session; a concurrent first sight loses the unique constraint and reads the
winner (tested with eight threads: one account). Display metadata is cleaned as
on the web side and updated only when it changed. Services still take
`user_id: int` and enforce ownership exactly as before.

**Replay guard scope.** Per process. With several API instances, an assertion
could be replayed once per instance within its 60 seconds — it is already
bound to the API audience and a 60-second window, and interception requires the
web → API hop (TLS in production). A shared store (Redis) would close it;
listed in the production-readiness report. The guard holds at most 200,000
unexpired ids and, when full, **refuses** new assertions (`replay_guard_full`,
logged at error level) rather than forgetting an unexpired one — which would
let exactly that assertion be replayed. Only valid assertions reach it, so only
the web app can fill it (over 2,000 requests a second to one process).

## 6. Account switching and stale state (the account tag)

`WebSession.sessionTag` is SHA-256 over the mode and the account's stable key
(`tid:oid` under entra, the address under dev-session), 22 characters. It names
the **account**, so the same person signing in again keeps it and only a
different account changes it. It is not a secret or a credential.

- Both clients send it back as `X-EasyApply-Session` on every API request. The
  web UI records it before any child renders and sends it through one helper
  (`apiFetch`); a unit test fails if anything else fetches `/api/backend`.
- The proxy checks, in order: signed out → 401 `session_required`; then a tag
  that is not the cookie's account → 409 `session_changed`, **never forwarded**.
  A request without the header is not checked (older clients).
- The web UI, on a 409, or when its check finds another account signed in:
  reload for that account with *This browser was signed in to a different
  EasyApply account in another tab, so this page was reloaded for that
  account.* Signed out in another tab: reload to the sign-in with *You signed
  out of EasyApply in another tab.* Expired (a 401, or the check finds nobody):
  keep the page and say so (§4). The check asks `/api/auth/session` on focus, on
  becoming visible, on a restore from the back-forward cache, and when another
  tab of the app renders, signs in or signs out (BroadcastChannel) — so a stale
  tab stops showing the previous person's data at once, not only at its next
  request. A session failure is never retried. A sign-out is announced to other
  tabs only if it happened.
- The extension: the tag is part of the session it shows, is sent on every call,
  keys its in-flight call joining and its memory of saves (never the display
  label — two accounts can share an address), and a 409 discards the previous
  account's state (see [chrome-extension.md](chrome-extension.md)).

## 7. Operators

`/internal/*` (the resume-processing operations pages, which read across every
user) under entra is for holders of the Entra app role **`EasyApply.Operator`**
(`ENTRA_OPERATOR_ROLE`) only:

- the ID token's `roles` becomes the session's `op`;
- the web app's internal layout answers 404 for anyone else, the proxy answers
  `/api/backend/internal/*` with the same 404 and forwards nothing, and attaches
  `OPERATOR_API_TOKEN` only after that check;
- the API requires the token **and** an assertion carrying the role, and logs
  each granted access with a pseudonymous operator key. Under entra there is no
  "open in development" posture: with `OPERATOR_API_TOKEN` unset the operator
  API refuses everyone, whatever `ENVIRONMENT` says (a review finding, fixed and
  tested).

In the development modes everyone is an operator, as before; a development-mode
API refuses a bearer assertion on the operator surface too (the mode mismatch).

## 8. Routes

| Route | Admission | Answers |
|---|---|---|
| `GET /api/auth/session` | Host | `WebSession` — `{mode, authenticated, email, name, sessionTag}`. `authenticated` alone says signed in; `email` may be null under entra. |
| `POST /api/auth/sign-in` | Host + same-origin | entra: form `return_to` → 303 to Microsoft. dev-session: `{"email"}` → session. development: 404. |
| `GET /api/auth/callback` | Host | entra only (404 otherwise): the callback (§3). |
| `POST /api/auth/sign-out` | Host + same-origin | Clears the cookie; entra adds `logoutUrl`. 404 in development. |
| `/signed-out` | — | where Microsoft returns after sign-out; signed out it shows the sign-in with *You signed out of EasyApply.* |
| `/api/backend/*` | as before | 404 for a path any of whose (percent-decoded) segments is empty, dot-only, or holds anything but letters, digits and `-_.~` — `jobs/..%2Finternal/…` would otherwise resolve past the operator check; 401 `session_required`; 409 `session_changed`; `/internal/*` 404 for non-operators under entra; 500 `auth_misconfigured`. |

## 9. Configuration

Web (`apps/web/.env.example`), entra: `AUTH_MODE=entra`, `APP_ORIGIN`,
`ENTRA_TENANT_ID`, `ENTRA_AUTHORITY`, `ENTRA_ISSUER` (defaulted), `ENTRA_CLIENT_ID`,
`ENTRA_CLIENT_SECRET`, `SESSION_SECRET` (+ `_PREVIOUS`), `SESSION_MAX_AGE_SECONDS`,
`IDENTITY_ASSERTION_SECRET`, `ENTRA_OPERATOR_ROLE`, `PROXY_ALLOWED_HOSTS` (must
include `APP_ORIGIN`'s host), `OPERATOR_API_TOKEN`.

API (`services/api/.env.example`), entra: `AUTH_MODE=entra`,
`IDENTITY_ASSERTION_SECRET` (+ `_PREVIOUS`), `ENTRA_TENANT_ID`, `ENTRA_ISSUER`,
`ENTRA_OPERATOR_ROLE`, `OPERATOR_API_TOKEN`, `ENVIRONMENT=production`,
`API_ALLOWED_HOSTS`.

Secrets are never committed, never `NEXT_PUBLIC_`, never sent to a browser or
the extension, and never logged. Configuration errors name the setting, never
its value (tested for every secret).

## 10. Tenant setup (Microsoft Entra External ID)

Not performed — no tenant is configured for this project yet. What it takes:

1. **External tenant.** Create (or use) an external tenant. Note its **Tenant
   ID** (GUID) and its tenant name (`<name>.ciamlogin.com`).
2. **App registration** (Applications → App registrations → New):
   single tenant (this directory only).
   - **Redirect URIs, platform "Web"**: `${APP_ORIGIN}/api/auth/callback` and
     `${APP_ORIGIN}/signed-out` (the post-logout redirect must be registered).
     Not "Single-page application": this is a confidential server-side client.
   - **Certificates & secrets**: a client secret → `ENTRA_CLIENT_SECRET`. Record
     its **expiry**; rotate before it (create the new one, deploy it, delete the
     old). An expired secret makes every sign-in `failed` with
     `invalid_client` (AADSTS7000222) in the log.
   - **API permissions**: Microsoft Graph delegated `openid`, `profile` and
     `email`; **grant admin consent**. `offline_access` is not needed: EasyApply
     keeps no refresh token.
   - **Token configuration**: add the **`email` optional claim** to the ID
     token. Without it `email` may be absent (EasyApply still works — the
     account shows its name, or "your EasyApply account").
   - **App roles**: create `EasyApply.Operator` (allowed member types: Users/
     Groups; value exactly `EasyApply.Operator`). Assign it under Enterprise
     applications → EasyApply → Users and groups to the operators only.
3. **User flow** (External Identities → User flows): sign-up and sign-in, the
   identity providers you want (email + password / one-time passcode, social),
   and **collect Display Name** (otherwise names arrive as `unknown`). Associate
   the application with the user flow.
4. **Values**: `ENTRA_TENANT_ID` = the tenant GUID; `ENTRA_AUTHORITY` =
   `https://<name>.ciamlogin.com/<tenant-id>/v2.0`; `ENTRA_ISSUER` =
   `https://<tenant-id>.ciamlogin.com/<tenant-id>/v2.0` (the default) — confirm
   it against the tenant's discovery document's `issuer`; `ENTRA_CLIENT_ID` =
   the Application (client) ID.
5. **Custom URL domain** (optional, later): changes the authority and the issuer
   — update both settings on both tiers; accounts are keyed by tenant, so none
   are orphaned.

## 11. Existing development data

Development-mode accounts are keyed by `users.email`. In entra mode
`X-Dev-User-Email` is refused and a first Microsoft sign-in provisions a new
empty account, so the developer's existing development data is not reachable
under entra (it is preserved, and reachable again in development mode). Joining
the two is deliberately not automatic (§1.3). If needed, an operator can insert
one `user_identities` row `(entra:<tid>, <oid>, user_id)` for a specific user
id, after checking that neither side is already mapped — a follow-up tool, not
built.

## 12. Logging and privacy

Logged: categories and outcomes — `auth.sign_in.success {operator}`,
`auth.sign_in.failure {reason, error?, aadsts?}`, `auth.sign_out`,
`auth.misconfigured` (setting names), `proxy.operator_refused {mode}`,
`proxy.extension {extension, method, route, status, ms}`, API
`identity.refused {reason}`, `identity.provisioned {user_id}`,
`identity.concurrent_first_sight`, `identity.metadata_updated`,
`identity.metadata_dropped|truncated {claim}`, `operator.access {operator key}`,
`operator.access_denied {reason}`, `dev_identity.refused {reason}`.

Never logged (tested where marked): tokens, codes or state (web tested),
session cookies, the client secret, authorization headers (API tested), email
addresses, object ids, names, raw claims, the provider's error description.

## 13. Tests

See the final report (`docs/entra-authentication-2026-09-26.md`) for counts
and results. In brief: API — assertion rejection matrix, algorithm confusion,
replay, rotation, configuration matrix, startup refusal, mode mismatch both
ways, provisioning race, email-not-identity cases, metadata cases, the full
A↔B ownership matrix in both directions, operator role, migration upgrade /
downgrade / constraints / drift. Web — configuration, v2 session, transaction,
OIDC against a fake tenant with real RS256 keys, the routes end to end, the
proxy's assertion, tag and operator checks, the client's tag on every request.
Browser — the whole sign-in against a local OpenID provider shaped like
External ID (`apps/web/e2e/support/mock-entra.mjs`).

## 14. Known limitations

- **Verified against a local provider, not a real tenant.** The protocol,
  External ID's discovery/issuer quirk, pairwise `sub`, `oid`, roles and logout
  are reproduced by the local provider; a real tenant's behaviour (user flows,
  consent, optional claims, token lifetimes, AADSTS errors) has not been
  exercised.
- **Stateless sessions** (§4): no server-side revocation short of rotating
  `SESSION_SECRET`; Entra revocation and role removal lag by up to the
  session lifetime.
- **Per-process replay guard** (§5).
- **Cookies are per host, not per port.** Two EasyApply web servers on one
  hostname share one cookie name (development only).
- **The extension depends on Chrome's cookie behaviour** for extension requests
  with host permission — measured and asserted, but Chrome's.
- **No CSRF token.** Writes rely on the browser-enforced Origin and
  Sec-Fetch-Site headers; SameSite=Lax is a second layer.
- **The development identity** still believes `X-Dev-User-Email` outside
  production — by design, and refused under entra and in production.
- **Display metadata can alternate** between two live sessions of one person
  signed in before and after they changed their name or address in Entra: each
  session carries its own snapshot, so `user_identities.email`/`display_name`
  follow whichever made the latest request until the older session expires.
  Display only; never identity.
- **Sign-out while Microsoft is down, with discovery cached** (up to an hour):
  EasyApply's session is already cleared, but the browser is sent to
  Microsoft's end-session URL and sees Microsoft's error page instead of
  `/signed-out`.
- **No HSTS from the app itself.** Cookies are Secure and `__Host-` prefixed over
  https; `Strict-Transport-Security` is expected from the edge (Front Door) —
  [production-infrastructure-2026-09-26.md](production-infrastructure-2026-09-26.md).
