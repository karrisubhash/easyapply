/**
 * A local OpenID Connect provider shaped like Microsoft Entra External ID, for
 * the browser suites. NOT a test double of EasyApply's code: it implements the
 * protocol the web app speaks to Entra, so the web app's real sign-in, callback
 * and token validation run against it unchanged.
 *
 * What it reproduces, because EasyApply depends on each:
 *
 *   - Discovery at  <authority>/.well-known/openid-configuration  on one host
 *     (`127.0.0.1`, like `<tenant-name>.ciamlogin.com`) while the `issuer` it
 *     declares is on ANOTHER host (`localhost`, like `<tenant-id>.ciamlogin.com`)
 *     — the External ID quirk that makes strict discovery libraries fail.
 *   - The authorization-code flow with PKCE (S256 only), `state`, `nonce`,
 *     `prompt=select_account` (an account picker page), single-use codes that
 *     expire in 60 seconds, exact redirect-URI matching, client_secret_post and
 *     client_secret_basic, `error=access_denied` when the person cancels.
 *   - ID tokens signed RS256 with a `kid` published at the JWKS endpoint, v2.0
 *     claims: `iss`, `aud` (the client id), `sub` (PAIRWISE: different per
 *     client id), `oid` (the same user object id for every client), `tid`,
 *     `email`, `name`, `preferred_username`, `nonce`, `iat`, `nbf`, `exp`, `ver`.
 *   - `end_session_endpoint`, redirecting to a registered post-logout URI.
 *
 * Each test user may carry `claims` overrides (a wrong `aud`, no `oid`, another
 * `tid`, an expired `exp`) so a browser test can prove the web app refuses a
 * token Entra would never send.
 *
 *   node apps/web/e2e/support/mock-entra.mjs      (configured by MOCK_ENTRA_* env, see `fromEnv`)
 */

import { createHash, generateKeyPairSync, randomBytes, sign } from "node:crypto";
import http from "node:http";
import { pathToFileURL } from "node:url";

const b64url = (buffer) => Buffer.from(buffer).toString("base64url");

function htmlEscape(text) {
  return String(text).replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

/**
 * The object id the provider gives an account it makes up from an email
 * (`allowAnyAccount`): stable, so the same address is the same person every
 * time, and GUID-shaped like a real `oid`. apps/extension/e2e/env.ts computes
 * the same value to seed that person's data; the two must stay in step.
 */
export function oidForEmail(email) {
  const hex = createHash("sha256").update(`mock-entra-oid:${email.trim().toLowerCase()}`).digest("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 64 * 1024) reject(new Error("body too large"));
    });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

/**
 * @param {{
 *   port: number,
 *   tenantId: string,
 *   clients: { clientId: string, clientSecret: string, redirectUris: string[], postLogoutRedirectUris?: string[] }[],
 *   users: { id: string, name: string, email: string | null, oid: string, claims?: Record<string, unknown> }[],
 *   discoveryHost?: string,
 *   issuerHost?: string,
 *   allowAnyAccount?: boolean,
 * }} options
 *
 * `allowAnyAccount` adds an "another account" field to the picker: any email
 * signs in as an account made up from it (object id `oidForEmail`). For suites
 * whose people are created per run; off unless asked for.
 */
// The issuer is compared, never fetched; the discovery host is fetched, so it is the address the server binds.
export async function startMockEntra({
  port,
  tenantId,
  clients,
  users,
  discoveryHost = "127.0.0.1",
  issuerHost = "localhost",
  allowAnyAccount = false,
}) {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const kid = b64url(randomBytes(12));
  const jwk = { ...publicKey.export({ format: "jwk" }), kid, use: "sig", alg: "RS256" };

  const base = `http://${discoveryHost}:${port}/${tenantId}`;
  const authority = `${base}/v2.0`;
  const issuer = `http://${issuerHost}:${port}/${tenantId}/v2.0`;
  const endpoints = {
    authorization_endpoint: `${base}/oauth2/v2.0/authorize`,
    token_endpoint: `${base}/oauth2/v2.0/token`,
    jwks_uri: `${base}/discovery/v2.0/keys`,
    end_session_endpoint: `${base}/oauth2/v2.0/logout`,
  };

  /** code -> grant */
  const codes = new Map();
  /** Everything that happened, for tests to assert on. */
  const events = [];

  function clientOf(clientId) {
    return clients.find((client) => client.clientId === clientId) ?? null;
  }

  function idToken(user, client, nonce) {
    const now = Math.floor(Date.now() / 1000);
    const claims = {
      ver: "2.0",
      iss: issuer,
      // Pairwise, as Entra's `sub` is: the same person has a different `sub` for each client id.
      sub: b64url(createHash("sha256").update(`${client.clientId}:${user.oid}`).digest()).slice(0, 43),
      aud: client.clientId,
      exp: now + 3600,
      iat: now,
      nbf: now,
      nonce,
      oid: user.oid,
      tid: tenantId,
      name: user.name,
      preferred_username: user.email ?? user.name,
      ...(user.email ? { email: user.email } : {}),
      aio: b64url(randomBytes(16)),
      ...(user.claims ?? {}),
    };
    for (const [key, value] of Object.entries(claims)) if (value === undefined) delete claims[key];
    const header = { typ: "JWT", alg: "RS256", kid };
    const signingInput = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(claims))}`;
    const signature = sign("RSA-SHA256", Buffer.from(signingInput), privateKey);
    return `${signingInput}.${b64url(signature)}`;
  }

  function redirectWith(response, target, params) {
    const url = new URL(target);
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
    response.writeHead(302, { Location: url.href });
    response.end();
  }

  function sendJson(response, status, body, headers = {}) {
    response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers });
    response.end(JSON.stringify(body));
  }

  function oauthError(response, status, error, description) {
    events.push({ type: "token_error", error, description });
    sendJson(response, status, { error, error_description: description });
  }

  function pickerPage(params) {
    const hidden = Object.entries(params)
      .map(([key, value]) => `<input type="hidden" name="${htmlEscape(key)}" value="${htmlEscape(value)}">`)
      .join("");
    const accounts = users
      .map(
        (user) =>
          `<button type="submit" name="account" value="${htmlEscape(user.id)}">${htmlEscape(user.name)}${
            user.email ? ` (${htmlEscape(user.email)})` : ""
          }</button>`,
      )
      .join("<br>");
    return `<!doctype html><title>Sign in to your account</title>
<h1>Pick an account</h1>
<p>Local test identity provider (shaped like Microsoft Entra External ID).</p>
<form method="post" action="${htmlEscape(endpoints.authorization_endpoint)}/complete">${hidden}${accounts}${
      allowAnyAccount
        ? `<br><label>Another account <input name="email" type="email" autocomplete="off"></label> <button type="submit" name="account" value="__email__">Continue</button>`
        : ""
    }
<br><button type="submit" name="account" value="__cancel__">Cancel</button></form>`;
  }

  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? "/", `http://${request.headers.host}`);
      const path = url.pathname;

      if (request.method === "GET" && path === "/health") return sendJson(response, 200, { ok: true });

      if (request.method === "GET" && path === `/${tenantId}/v2.0/.well-known/openid-configuration`) {
        events.push({ type: "discovery" });
        return sendJson(response, 200, {
          issuer,
          ...endpoints,
          response_types_supported: ["code", "id_token", "code id_token"],
          response_modes_supported: ["query", "fragment", "form_post"],
          subject_types_supported: ["pairwise"],
          id_token_signing_alg_values_supported: ["RS256"],
          scopes_supported: ["openid", "profile", "email", "offline_access"],
          token_endpoint_auth_methods_supported: ["client_secret_post", "client_secret_basic"],
          code_challenge_methods_supported: ["S256"],
          claims_supported: ["sub", "iss", "aud", "exp", "iat", "nbf", "nonce", "oid", "tid", "email", "name", "preferred_username", "ver"],
        });
      }

      if (request.method === "GET" && path === `/${tenantId}/discovery/v2.0/keys`) {
        events.push({ type: "jwks" });
        return sendJson(response, 200, { keys: [jwk] });
      }

      if (request.method === "GET" && path === `/${tenantId}/oauth2/v2.0/authorize`) {
        const params = Object.fromEntries(url.searchParams);
        const client = clientOf(params.client_id);
        // Entra shows an error page, never redirects, for an unknown client or redirect URI.
        if (!client || !client.redirectUris.includes(params.redirect_uri)) {
          events.push({ type: "authorize_refused", reason: "client_or_redirect" });
          response.writeHead(400, { "Content-Type": "text/html" });
          return response.end("<h1>AADSTS50011: The redirect URI does not match.</h1>");
        }
        const problems = [];
        if (params.response_type !== "code") problems.push("response_type");
        if (!(params.scope ?? "").split(" ").includes("openid")) problems.push("scope");
        if (params.code_challenge_method !== "S256" || !params.code_challenge) problems.push("pkce");
        if (!params.state) problems.push("state");
        if (!params.nonce) problems.push("nonce");
        if (problems.length > 0) {
          events.push({ type: "authorize_refused", reason: problems.join(",") });
          return redirectWith(response, params.redirect_uri, {
            error: "invalid_request",
            error_description: `missing or wrong: ${problems.join(", ")}`,
            ...(params.state ? { state: params.state } : {}),
          });
        }
        events.push({ type: "authorize", prompt: params.prompt ?? null });
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        return response.end(
          pickerPage({
            client_id: params.client_id,
            redirect_uri: params.redirect_uri,
            state: params.state,
            nonce: params.nonce,
            code_challenge: params.code_challenge,
            scope: params.scope,
          }),
        );
      }

      if (request.method === "POST" && path === `/${tenantId}/oauth2/v2.0/authorize/complete`) {
        const form = Object.fromEntries(new URLSearchParams(await readBody(request)));
        const client = clientOf(form.client_id);
        if (!client || !client.redirectUris.includes(form.redirect_uri)) {
          response.writeHead(400, { "Content-Type": "text/html" });
          return response.end("<h1>Bad request</h1>");
        }
        if (form.account === "__cancel__") {
          events.push({ type: "cancelled" });
          return redirectWith(response, form.redirect_uri, {
            error: "access_denied",
            error_description: "AADSTS65004: User declined to consent to access the app.",
            state: form.state,
          });
        }
        const typed = (form.email ?? "").trim().toLowerCase();
        const user =
          allowAnyAccount && form.account === "__email__" && /^[^\s@]+@[^\s@]+$/.test(typed)
            ? { id: typed, name: typed.split("@")[0], email: typed, oid: oidForEmail(typed) }
            : users.find((candidate) => candidate.id === form.account);
        if (!user) {
          response.writeHead(400, { "Content-Type": "text/html" });
          return response.end("<h1>No such account</h1>");
        }
        const code = b64url(randomBytes(32));
        codes.set(code, {
          clientId: client.clientId,
          redirectUri: form.redirect_uri,
          codeChallenge: form.code_challenge,
          nonce: form.nonce,
          user,
          expiresAt: Date.now() + 60_000,
          used: false,
        });
        events.push({ type: "code_issued", user: user.id });
        return redirectWith(response, form.redirect_uri, { code, state: form.state });
      }

      if (request.method === "POST" && path === `/${tenantId}/oauth2/v2.0/token`) {
        const form = Object.fromEntries(new URLSearchParams(await readBody(request)));
        let clientId = form.client_id;
        let clientSecret = form.client_secret;
        const basic = /^Basic (.+)$/.exec(request.headers.authorization ?? "");
        if (basic) {
          const [id, secret] = Buffer.from(basic[1], "base64").toString("utf8").split(":");
          clientId = decodeURIComponent(id ?? "");
          clientSecret = decodeURIComponent(secret ?? "");
        }
        const client = clientOf(clientId);
        if (!client || client.clientSecret !== clientSecret) return oauthError(response, 401, "invalid_client", "AADSTS7000215: Invalid client secret.");
        if (form.grant_type !== "authorization_code") return oauthError(response, 400, "unsupported_grant_type", "only authorization_code");
        const grant = codes.get(form.code ?? "");
        if (!grant) return oauthError(response, 400, "invalid_grant", "AADSTS70008: The provided authorization code is invalid.");
        if (grant.used) return oauthError(response, 400, "invalid_grant", "AADSTS54005: OAuth2 Authorization code was already redeemed.");
        grant.used = true;
        if (grant.expiresAt < Date.now()) return oauthError(response, 400, "invalid_grant", "AADSTS70008: The code has expired.");
        if (grant.clientId !== client.clientId) return oauthError(response, 400, "invalid_grant", "code issued to another client");
        if (grant.redirectUri !== form.redirect_uri) return oauthError(response, 400, "invalid_grant", "AADSTS500112: redirect_uri mismatch.");
        const challenge = b64url(createHash("sha256").update(form.code_verifier ?? "").digest());
        if (!form.code_verifier || challenge !== grant.codeChallenge) {
          return oauthError(response, 400, "invalid_grant", "AADSTS501481: The Code_Verifier does not match the code_challenge.");
        }
        events.push({ type: "token_issued", user: grant.user.id });
        return sendJson(response, 200, {
          token_type: "Bearer",
          scope: "openid profile email",
          expires_in: 3600,
          access_token: b64url(randomBytes(48)),
          id_token: idToken(grant.user, client, grant.nonce),
        });
      }

      if (request.method === "GET" && path === `/${tenantId}/oauth2/v2.0/logout`) {
        const target = url.searchParams.get("post_logout_redirect_uri");
        const allowed = clients.some((client) => (client.postLogoutRedirectUris ?? []).includes(target ?? ""));
        events.push({ type: "logout", redirected: allowed });
        if (target && allowed) {
          response.writeHead(302, { Location: target });
          return response.end();
        }
        response.writeHead(200, { "Content-Type": "text/html" });
        return response.end("<h1>You signed out of your account</h1>");
      }

      response.writeHead(404, { "Content-Type": "text/plain" });
      response.end("not found");
    } catch (error) {
      response.writeHead(500, { "Content-Type": "text/plain" });
      response.end(String(error));
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve());
  });

  return {
    authority,
    issuer,
    tenantId,
    endpoints,
    events,
    /** Mint an ID token directly, for tests that need one without a browser. */
    idToken: (user, clientId, nonce) => idToken(user, clientOf(clientId), nonce),
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

/** Configuration from the environment, for running as its own process. */
export function fromEnv(env = process.env) {
  return {
    port: Number(env.MOCK_ENTRA_PORT ?? 3190),
    tenantId: env.MOCK_ENTRA_TENANT_ID ?? "11111111-2222-4333-8444-555555555555",
    clients: [
      {
        clientId: env.MOCK_ENTRA_CLIENT_ID ?? "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        clientSecret: env.MOCK_ENTRA_CLIENT_SECRET ?? "mock-entra-client-secret",
        redirectUris: (env.MOCK_ENTRA_REDIRECT_URIS ?? "").split(",").map((uri) => uri.trim()).filter(Boolean),
        postLogoutRedirectUris: (env.MOCK_ENTRA_POST_LOGOUT_URIS ?? "").split(",").map((uri) => uri.trim()).filter(Boolean),
      },
    ],
    users: JSON.parse(env.MOCK_ENTRA_USERS ?? "[]"),
    allowAnyAccount: env.MOCK_ENTRA_ANY_ACCOUNT === "1",
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const provider = await startMockEntra(fromEnv());
  console.log(`mock Entra listening: authority ${provider.authority}, issuer ${provider.issuer}`);
}

