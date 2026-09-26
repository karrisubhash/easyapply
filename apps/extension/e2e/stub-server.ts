/**
 * A stand-in for the EasyApply web app, for the browser suite's default run.
 *
 * It behaves the way the real web app does under AUTH_MODE=dev-session — the
 * mode with a per-browser session, which is the harder case for the extension:
 *
 *   GET  /                    the sign-in form when signed out; otherwise a page
 *                             naming the account (title=<email>) with Sign out.
 *   POST /api/auth/sign-in    same-origin only; sets an HttpOnly, SameSite=Lax
 *                             session cookie.
 *   POST /api/auth/sign-out   same-origin only; clears it.
 *   GET  /api/auth/session    { mode, authenticated, email, sessionTag } from
 *                             the cookie. The tag is per ACCOUNT (derived from
 *                             the email), so signing in again as the same
 *                             person keeps it and another person's differs.
 *   /api/backend/*            the proxy: writes only same-origin, or the
 *                             extension's three workflow writes (Origin
 *                             chrome-extension://<pinned id>, Sec-Fetch-Site
 *                             none) — POST /jobs, /jobs/<id>/matches,
 *                             /jobs/<id>/tailored-resumes; then the session:
 *                             401 session_required without one; then, for a
 *                             request carrying X-EasyApply-Session, 409
 *                             session_changed unless it is the signed-in
 *                             account's tag (no header: not checked); then the API,
 *                             per account, played by the same fake the unit
 *                             tests use (test/support/fake-api.ts) — so its
 *                             answers are canned backend values, never a
 *                             verdict of its own.
 *   GET  /api/backend/tailored-resumes/<id>/artifact
 *                             a small file with a Content-Disposition, for the
 *                             account's completed, grounded run; 404 otherwise.
 *   GET  /jobs/<id>           the job's title, for the account that saved it.
 *
 * The session is a random token held in memory, not a signed one: this stub
 * exists to observe what Chrome SENDS — that the extension's requests carry
 * the web app's cookie, and no identity of its own — not to re-test the web
 * app's token, which apps/web tests directly.
 */

import { randomBytes } from "node:crypto";
import http from "node:http";

import { createFakeApi, fakeSessionTag } from "../test/support/fake-api";

export interface SeenRequest {
  method: string;
  url: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

const COOKIE = "easyapply_session";
const EXTENSION_WRITE = /^\/api\/backend\/(jobs|jobs\/[1-9]\d{0,17}\/(matches|tailored-resumes))$/;

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

const SIGN_IN_PAGE = `<!doctype html>
<title>EasyApply</title>
<span>Not signed in</span>
<h1>Sign in</h1>
<form id="f"><label for="e">Email</label><input id="e" type="email"><button type="submit">Sign in</button></form>
<script>
  document.getElementById("f").addEventListener("submit", async (event) => {
    event.preventDefault();
    const response = await fetch("/api/auth/sign-in", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: document.getElementById("e").value }),
    });
    if (response.ok) location.reload();
  });
</script>`;

function signedInPage(email: string): string {
  return `<!doctype html>
<title>EasyApply</title>
<span title="${escapeHtml(email)}">${escapeHtml(email)}</span>
<button id="out" type="button">Sign out</button>
<script>
  document.getElementById("out").addEventListener("click", async () => {
    await fetch("/api/auth/sign-out", { method: "POST" });
    location.assign("/");
  });
</script>`;
}

export async function startStub(port: number, extensionId: string) {
  const origin = `http://127.0.0.1:${port}`;
  const requests: SeenRequest[] = [];
  const sessions = new Map<string, string>();
  // The API behind the proxy. Its own session is set per request from the cookie.
  const api = createFakeApi({ webUrl: origin, session: null });

  function sessionOf(request: http.IncomingMessage): string | null {
    const cookie = (request.headers.cookie ?? "")
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(`${COOKIE}=`));
    return cookie ? (sessions.get(cookie.slice(COOKIE.length + 1)) ?? null) : null;
  }

  const server = http.createServer((request, response) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => (body += chunk));
    request.on("end", () => {
      void handle(request, response, body);
    });
  });

  async function handle(request: http.IncomingMessage, response: http.ServerResponse, body: string): Promise<void> {
    requests.push({ method: request.method ?? "", url: request.url ?? "", headers: request.headers, body });
    const send = (status: number, payload: unknown, headers: Record<string, string> = {}) => {
      const html = typeof payload === "string";
      response.writeHead(status, { "Content-Type": html ? "text/html" : "application/json", ...headers });
      response.end(html ? payload : JSON.stringify(payload));
    };
    const method = request.method ?? "GET";
    const path = (request.url ?? "/").split("?")[0] ?? "/";
    const email = sessionOf(request);
    const sameOrigin = request.headers.origin === origin;

    if (method === "GET" && path === "/") return send(200, email ? signedInPage(email) : SIGN_IN_PAGE);

    if (method === "POST" && path === "/api/auth/sign-in") {
      if (!sameOrigin) return send(403, { detail: "Cross-site requests are not accepted.", code: "cross_site_request" });
      const claimed = String(JSON.parse(body || "{}").email ?? "").trim().toLowerCase();
      if (!claimed.includes("@")) return send(422, { detail: "Enter an email address.", code: "invalid_email" });
      const token = randomBytes(16).toString("hex");
      sessions.set(token, claimed);
      return send(
        200,
        { mode: "dev-session", authenticated: true, email: claimed, sessionTag: fakeSessionTag(claimed) },
        { "Set-Cookie": `${COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax` },
      );
    }

    if (method === "POST" && path === "/api/auth/sign-out") {
      if (!sameOrigin) return send(403, { detail: "Cross-site requests are not accepted.", code: "cross_site_request" });
      return send(
        200,
        { mode: "dev-session", authenticated: false, email: null },
        { "Set-Cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax` },
      );
    }

    if (method === "GET" && path === "/api/auth/session") {
      return send(
        200,
        email === null
          ? { mode: "dev-session", authenticated: false, email: null }
          : { mode: "dev-session", authenticated: true, email, sessionTag: fakeSessionTag(email) },
      );
    }

    const page = /^\/jobs\/(\d+)$/.exec(path);
    if (method === "GET" && page) {
      const job = api.jobs.find((candidate) => candidate.id === Number(page[1]) && candidate.owner === email);
      if (!job) return send(404, "<h1>That job does not exist</h1>");
      return send(200, `<!doctype html><title>${escapeHtml(job.job_title)}</title><h1>${escapeHtml(job.job_title)}</h1>`);
    }

    if (!path.startsWith("/api/backend/")) return send(404, { detail: "Not Found", code: "not_found" });

    // --- the proxy: admission, then the session, then the API ---
    if (method !== "GET") {
      const fromExtension =
        request.headers.origin === `chrome-extension://${extensionId}` && request.headers["sec-fetch-site"] === "none";
      if (!(sameOrigin || (fromExtension && method === "POST" && EXTENSION_WRITE.test(path)))) {
        return send(403, { detail: "Cross-site requests are not accepted.", code: "cross_site_request" });
      }
    }
    if (!email) return send(401, { detail: "You are not signed in to EasyApply.", code: "session_required" });
    const claimedTag = request.headers["x-easyapply-session"];
    if (claimedTag !== undefined && claimedTag !== fakeSessionTag(email)) {
      return send(409, { detail: "This browser is signed in to EasyApply as a different account now.", code: "session_changed" });
    }

    const artifact = /^\/api\/backend\/tailored-resumes\/(\d+)\/artifact$/.exec(path);
    if (method === "GET" && artifact) {
      const run = api.runs.find((candidate) => candidate.owner === email && candidate.id === Number(artifact[1]));
      if (!run || !run.has_artifact || run.grounding_status !== "passed") {
        return send(404, { detail: "That tailored resume does not exist.", code: "not_found" });
      }
      response.writeHead(200, {
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": `attachment; filename="${run.artifact?.filename ?? "resume.docx"}"`,
        "Cache-Control": "private, no-store",
      });
      response.end(Buffer.from("PK\u0003\u0004 stub docx"));
      return;
    }

    // The proxy builds the upstream request itself: the session's person, nothing the client claimed.
    api.setSession({ mode: "dev-session", email });
    const answer = await api.fetch(`${origin}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: method === "GET" ? undefined : body,
      credentials: "include",
    });
    response.writeHead(answer.status, { "Content-Type": answer.headers.get("content-type") ?? "application/json" });
    response.end(await answer.text());
  }

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve());
  });

  return {
    requests,
    /** The jobs the API holds, each with the account that saved it. */
    get jobs() {
      return api.jobs;
    },
    api,
    /** The saves, as opposed to page loads and session questions. */
    get saves() {
      return requests.filter((request) => request.method === "POST" && request.url === "/api/backend/jobs");
    },
    /** Requests the extension made to anything under /api/backend. */
    get backend() {
      return requests.filter((request) => request.url.startsWith("/api/backend/"));
    },
    /** The account tag the session route reports for this person. */
    tagOf: (email: string) => fakeSessionTag(email),
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

export type Stub = Awaited<ReturnType<typeof startStub>>;
