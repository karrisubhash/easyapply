// @vitest-environment node

/**
 * The two health routes answer different questions. /api/health is the
 * platform's liveness probe and must depend on nothing; /api/health/ready says
 * whether a person could be served, names each failing dependency, and never
 * echoes an upstream's words, a hostname or a configuration reason.
 */

import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import { stubEntraEnv } from "@/test/entra";

import { GET as live } from "./route";
import { GET as ready } from "./ready/route";

const API_READY = "http://127.0.0.1:8000/health/ready";
const ALL_OK = { database: "ok", schema: "ok", processing: "ok" };

function request(path: string, host = "localhost:3000") {
  return new Request(`http://${host}${path}`, { headers: { host } });
}

function apiAnswers(status: number, checks: Record<string, unknown>) {
  server.use(http.get(API_READY, () => HttpResponse.json({ status: "x", checks }, { status })));
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("GET /api/health", () => {
  it("answers 200 without consulting anything, whatever the Host", async () => {
    const response = live();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok", service: "easyapply-web" });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
});

describe("GET /api/health/ready", () => {
  it("is ready when sign-in is configured and the API is ready", async () => {
    stubEntraEnv();
    apiAnswers(200, ALL_OK);

    const response = await ready(request("/api/health/ready"));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "ready",
      service: "easyapply-web",
      auth_mode: "entra",
      checks: { auth: "ok", api: "ok", ...ALL_OK },
    });
  });

  it("relays which API dependency failed", async () => {
    apiAnswers(503, { database: "unavailable", schema: "unknown", processing: "ok" });

    const response = await ready(request("/api/health/ready"));

    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.status).toBe("not_ready");
    expect(body.checks).toEqual({ auth: "ok", api: "not_ready", database: "unavailable", schema: "unknown", processing: "ok" });
  });

  it("an unreachable API is named as such, with its checks unknown", async () => {
    server.use(http.get(API_READY, () => HttpResponse.error()));

    const response = await ready(request("/api/health/ready"));

    expect(response.status).toBe(503);
    expect((await response.json()).checks).toEqual({
      auth: "ok",
      api: "unreachable",
      database: "unknown",
      schema: "unknown",
      processing: "unknown",
    });
  });

  it("an API that refuses this server says how, by status only", async () => {
    server.use(
      http.get(API_READY, () =>
        HttpResponse.json({ detail: "This API does not answer for that host.", code: "host_not_allowed" }, { status: 403 }),
      ),
    );

    const response = await ready(request("/api/health/ready"));

    expect(response.status).toBe(503);
    expect((await response.json()).checks.api).toBe("http_403");
  });

  it("never echoes an upstream value that is not a plain word", async () => {
    apiAnswers(503, { database: "connection to db-secret-host failed", schema: 7, processing: "ok" });

    const response = await ready(request("/api/health/ready"));
    const text = await response.text();

    expect(text).not.toContain("db-secret-host");
    expect(JSON.parse(text).checks).toMatchObject({ database: "unknown", schema: "unknown", processing: "ok" });
  });

  it("incomplete sign-in configuration is not ready, and the reason stays in the log", async () => {
    stubEntraEnv({ ENTRA_CLIENT_SECRET: "" });
    apiAnswers(200, ALL_OK);
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await ready(request("/api/health/ready"));
    const text = await response.text();

    expect(response.status).toBe(503);
    expect(JSON.parse(text)).toMatchObject({ auth_mode: "misconfigured", checks: { auth: "misconfigured", api: "ok" } });
    expect(text).not.toContain("ENTRA_CLIENT_SECRET");
    expect(logged).toHaveBeenCalledWith(expect.stringContaining("ENTRA_CLIENT_SECRET"));
    logged.mockRestore();
  });

  it("answers only for a host this server serves", async () => {
    const response = await ready(request("/api/health/ready", "evil.example"));

    expect(response.status).toBe(403);
    expect((await response.json()).code).toBe("host_not_allowed");
  });
});
