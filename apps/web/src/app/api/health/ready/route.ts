/**
 * GET /api/health/ready — readiness: can this deployment serve a person now?
 *
 * Distinct from /api/health (the process is up). Each dependency is reported
 * on its own, so an outage names its cause rather than looking like "down":
 *
 *   auth        this server's sign-in configuration is complete
 *               (src/server/auth/config.ts); the reason, which names
 *               settings, goes to the log and never into the response
 *   api         the API answered its own readiness check
 *   database    }
 *   schema      } relayed from the API's /health/ready (services/api/app/main.py)
 *   processing  }
 *
 * Any of them not "ok" is a 503. Every value is a fixed word: nothing from an
 * exception, a URL or a hostname is echoed. `auth_mode` is the mode name only,
 * which GET /api/auth/session already tells anyone who asks.
 *
 * The Host check applies, as on every route that is not the bare liveness probe.
 */

import { readAuthConfig } from "@/server/auth/config";
import { HOST_REFUSAL, isAllowedHost, refuse } from "@/server/request-guard";

export const dynamic = "force-dynamic";

/** Long enough for a cold database connection, short enough for an uptime monitor. */
const API_TIMEOUT_MS = 5000;

const RELAYED_CHECKS = ["database", "schema", "processing"] as const;
const WORD = /^[a-z_]{1,32}$/;

export async function GET(request: Request) {
  if (!isAllowedHost(request.headers.get("host"))) return refuse(...HOST_REFUSAL);

  const checks: Record<string, string> = {};

  const auth = readAuthConfig();
  if (auth.mode === "misconfigured") {
    console.error(`auth.misconfigured: ${auth.reason}`);
    checks.auth = "misconfigured";
  } else {
    checks.auth = "ok";
  }

  try {
    const response = await fetch(new URL("/health/ready", process.env.API_BASE_URL ?? "http://127.0.0.1:8000"), {
      cache: "no-store",
      signal: AbortSignal.timeout(API_TIMEOUT_MS),
    });
    const body: unknown = await response.json().catch(() => null);
    const upstream =
      body && typeof body === "object" && "checks" in body && body.checks && typeof body.checks === "object"
        ? (body.checks as Record<string, unknown>)
        : {};
    checks.api = response.ok ? "ok" : response.status === 503 ? "not_ready" : `http_${response.status}`;
    for (const name of RELAYED_CHECKS) {
      const value = upstream[name];
      checks[name] = typeof value === "string" && WORD.test(value) ? value : "unknown";
    }
  } catch {
    checks.api = "unreachable";
    for (const name of RELAYED_CHECKS) checks[name] = "unknown";
  }

  const ready = Object.values(checks).every((value) => value === "ok");
  return Response.json(
    { status: ready ? "ready" : "not_ready", service: "easyapply-web", auth_mode: auth.mode, checks },
    { status: ready ? 200 : 503, headers: { "Cache-Control": "no-store" } },
  );
}
