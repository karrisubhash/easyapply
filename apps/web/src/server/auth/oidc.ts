/**
 * The web app as an OpenID Connect relying party of Microsoft Entra External ID.
 *
 * Authorization code with PKCE (S256), a confidential client (client secret,
 * server-side only), `response_mode=query`, scopes `openid profile email`
 * (`profile` is what makes Entra include `oid`), `prompt=select_account` every
 * time — on a shared browser, signing out of EasyApply must not silently sign
 * the next person back into the previous account.
 *
 * DISCOVERY AND THE ISSUER. External ID serves its metadata from the
 * tenant-NAME host (`https://<name>.ciamlogin.com/<tenant-id>/v2.0/...`) but
 * declares an issuer on the tenant-ID host (`https://<tenant-id>.ciamlogin.com/
 * <tenant-id>/v2.0`). A strict discovery client refuses that (the issuer must
 * equal the URL it was fetched from); a lax one trusts whatever the document
 * says. This does neither: the expected issuer is CONFIGURATION (ENTRA_ISSUER,
 * defaulting to the tenant-id form), the document's `issuer` must equal it
 * exactly, and every ID token's `iss` must equal it too.
 *
 * VALIDATION of the ID token (jose `jwtVerify`, RS256 only, keys from the
 * discovered `jwks_uri`, refetched when an unknown `kid` appears):
 * iss = the pinned issuer; aud = our client id (and a single audience); azp, if
 * present, = our client id; exp, iat, nonce, oid and tid present; 60 seconds of
 * clock tolerance; issued within the last 10 minutes; nonce = the transaction's;
 * tid = our tenant; oid a GUID. Nothing from the token but the claims below is
 * kept, and the token itself is discarded.
 */

import { createRemoteJWKSet, errors as joseErrors, jwtVerify, type JWTPayload } from "jose";

import type { EntraConfig } from "./config";

export interface Discovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  jwks_uri: string;
  end_session_endpoint: string | null;
}

/** What EasyApply keeps from a validated ID token. */
export interface ValidatedSignIn {
  issuer: string;
  tenantId: string;
  objectId: string;
  email: string | null;
  name: string | null;
  roles: string[];
}

/** Why a sign-in step failed — a category for the log and the person, never token text. */
export class SignInError extends Error {
  constructor(
    readonly reason: string,
    /** The provider's own error code (`invalid_grant`) and AADSTS number, when it gave them. */
    readonly providerError: { error?: string; code?: number } = {},
  ) {
    super(reason);
  }
}

const DISCOVERY_TTL_MS = 60 * 60 * 1000;
const NETWORK_TIMEOUT_MS = 10_000;
const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

const discoveryCache = new Map<string, { value: Discovery; expires: number }>();
const keySets = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

/** For tests: forget cached discovery documents and key sets. */
export function resetOidcCaches(): void {
  discoveryCache.clear();
  keySets.clear();
}

function endpoint(value: unknown, authority: URL): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    const loopbackAuthority = authority.protocol === "http:" && LOOPBACK.has(authority.hostname);
    if (url.protocol === "https:" || (loopbackAuthority && url.protocol === "http:" && LOOPBACK.has(url.hostname))) {
      return url.href;
    }
  } catch {
    // not a URL
  }
  return null;
}

async function fetchJson(url: string, init: RequestInit = {}): Promise<{ status: number; body: unknown }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), NETWORK_TIMEOUT_MS);
  try {
    const response = await fetch(url, { ...init, redirect: "error", cache: "no-store", signal: controller.signal });
    const text = await response.text();
    let body: unknown = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = null;
    }
    return { status: response.status, body };
  } finally {
    clearTimeout(timer);
  }
}

export async function discover(config: EntraConfig, now: number = Date.now()): Promise<Discovery> {
  const cached = discoveryCache.get(config.authority);
  if (cached && cached.expires > now) return cached.value;

  let result: { status: number; body: unknown };
  try {
    result = await fetchJson(`${config.authority}/.well-known/openid-configuration`, { headers: { Accept: "application/json" } });
  } catch {
    throw new SignInError("discovery_unreachable");
  }
  if (result.status !== 200 || typeof result.body !== "object" || result.body === null) throw new SignInError("discovery_invalid");
  const document = result.body as Record<string, unknown>;
  if (document.issuer !== config.issuer) throw new SignInError("discovery_issuer_mismatch");

  const authority = new URL(config.authority);
  const value: Discovery = {
    issuer: config.issuer,
    authorization_endpoint: endpoint(document.authorization_endpoint, authority) ?? "",
    token_endpoint: endpoint(document.token_endpoint, authority) ?? "",
    jwks_uri: endpoint(document.jwks_uri, authority) ?? "",
    end_session_endpoint: endpoint(document.end_session_endpoint, authority),
  };
  if (!value.authorization_endpoint || !value.token_endpoint || !value.jwks_uri) throw new SignInError("discovery_invalid");
  discoveryCache.set(config.authority, { value, expires: now + DISCOVERY_TTL_MS });
  return value;
}

export function authorizationUrl(
  discovery: Discovery,
  config: EntraConfig,
  { state, nonce, challenge }: { state: string; nonce: string; challenge: string },
): string {
  const url = new URL(discovery.authorization_endpoint);
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("response_mode", "query");
  url.searchParams.set("redirect_uri", config.redirectUri);
  url.searchParams.set("scope", "openid profile email");
  url.searchParams.set("state", state);
  url.searchParams.set("nonce", nonce);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("prompt", "select_account");
  return url.href;
}

/** RP-initiated logout: end the Entra session too, then come back to the signed-out page. */
export function logoutUrl(discovery: Discovery, config: EntraConfig): string | null {
  if (!discovery.end_session_endpoint) return null;
  const url = new URL(discovery.end_session_endpoint);
  url.searchParams.set("post_logout_redirect_uri", config.postLogoutRedirectUri);
  return url.href;
}

/** Redeem the code at the token endpoint. Returns the ID token; everything else in the answer is dropped. */
export async function exchangeCode(
  discovery: Discovery,
  config: EntraConfig,
  { code, verifier }: { code: string; verifier: string },
): Promise<string> {
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: config.redirectUri,
    client_id: config.clientId,
    client_secret: config.clientSecret,
    code_verifier: verifier,
  });
  let result: { status: number; body: unknown };
  try {
    result = await fetchJson(discovery.token_endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: body.toString(),
    });
  } catch {
    throw new SignInError("token_endpoint_unreachable");
  }
  const answer = (typeof result.body === "object" && result.body !== null ? result.body : {}) as Record<string, unknown>;
  if (result.status !== 200 || typeof answer.id_token !== "string") {
    const codes = Array.isArray(answer.error_codes) ? answer.error_codes : [];
    throw new SignInError("token_endpoint_refused", {
      error: typeof answer.error === "string" ? answer.error.slice(0, 64) : undefined,
      code: typeof codes[0] === "number" ? codes[0] : undefined,
    });
  }
  return answer.id_token;
}

function keySet(uri: string): ReturnType<typeof createRemoteJWKSet> {
  let set = keySets.get(uri);
  if (!set) {
    set = createRemoteJWKSet(new URL(uri), { timeoutDuration: NETWORK_TIMEOUT_MS, cooldownDuration: 30_000, cacheMaxAge: 10 * 60_000 });
    keySets.set(uri, set);
  }
  return set;
}

function reasonOf(error: unknown): string {
  if (error instanceof joseErrors.JWTExpired) return "id_token_expired";
  if (error instanceof joseErrors.JWTClaimValidationFailed) return `id_token_claim_${error.claim}`;
  if (error instanceof joseErrors.JWSSignatureVerificationFailed) return "id_token_signature";
  if (error instanceof joseErrors.JWKSNoMatchingKey) return "id_token_key";
  if (error instanceof joseErrors.JOSEAlgNotAllowed) return "id_token_algorithm";
  if (error instanceof joseErrors.JWKSTimeout) return "jwks_unreachable";
  // The key set could not be fetched at all (DNS, connection, a non-200): the
  // provider is unavailable, not the token invalid.
  if (error instanceof TypeError) return "jwks_unreachable";
  if (error instanceof joseErrors.JOSEError && /JSON Web Key Set HTTP response/.test(error.message)) return "jwks_unreachable";
  return "id_token_invalid";
}

/** users.email / user_identities.email and .display_name, in code points as the API counts them. */
const EMAIL_MAX = 320;
const NAME_MAX = 255;

/**
 * A display value, cleaned the way the API cleans it — never a reason to fail
 * a sign-in. NFC; C0 and C1 control characters removed (a zero-width joiner, a
 * non-breaking space or an emoji is a valid name and is kept); cut to the
 * column in code points; null if nothing is left.
 */
export function displayText(value: unknown, limit: number): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value
    .normalize("NFC")
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, "")
    // A lone surrogate is not text: it cannot be stored, and would fail every request downstream.
    .replace(/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, "\ufffd")
    .trim();
  const cut = Array.from(cleaned).slice(0, limit).join("").trim();
  return cut || null;
}

export async function validateIdToken(
  idToken: string,
  discovery: Discovery,
  config: EntraConfig,
  expectedNonce: string,
  currentDate?: Date,
): Promise<ValidatedSignIn> {
  let payload: JWTPayload;
  try {
    ({ payload } = await jwtVerify(idToken, keySet(discovery.jwks_uri), {
      issuer: config.issuer,
      audience: config.clientId,
      algorithms: ["RS256"],
      clockTolerance: 60,
      maxTokenAge: "10m",
      requiredClaims: ["exp", "iat", "nonce", "oid", "tid", "sub"],
      currentDate,
    }));
  } catch (error) {
    throw new SignInError(reasonOf(error));
  }
  if (Array.isArray(payload.aud) && payload.aud.length !== 1) throw new SignInError("id_token_claim_aud");
  if (payload.azp !== undefined && payload.azp !== config.clientId) throw new SignInError("id_token_claim_azp");
  if (payload.nonce !== expectedNonce) throw new SignInError("id_token_claim_nonce");
  const tenant = typeof payload.tid === "string" ? payload.tid.toLowerCase() : "";
  if (tenant !== config.tenantId) throw new SignInError("id_token_claim_tid");
  const objectId = typeof payload.oid === "string" ? payload.oid.toLowerCase() : "";
  if (!GUID.test(objectId)) throw new SignInError("id_token_claim_oid");

  const roles = payload.roles;
  if (roles !== undefined && (!Array.isArray(roles) || !roles.every((role) => typeof role === "string"))) {
    throw new SignInError("id_token_claim_roles");
  }
  const displayName = displayText(payload.name, NAME_MAX);
  return {
    issuer: config.issuer,
    tenantId: tenant,
    objectId,
    email: displayText(payload.email, EMAIL_MAX),
    // Entra fills an uncollected display name with "unknown"; that is not a name.
    name: displayName && displayName.toLowerCase() !== "unknown" ? displayName : null,
    roles: (roles as string[] | undefined) ?? [],
  };
}
