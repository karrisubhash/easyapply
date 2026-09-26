/**
 * The client half of session consistency: every request to the proxy carries
 * the account tag the page was rendered for, and a session failure is routed
 * to the right reaction.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { SESSION_TAG_HEADER } from "@easyapply/shared";
import { http, HttpResponse } from "msw";

import { makeQueryClient } from "@/app/providers";
import { apiFetch, ApiError, request } from "@/lib/api/client";
import { uploadResume } from "@/lib/api/resume";
import { downloadArtifact } from "@/lib/api/tailoring";
import { setRenderedSessionTag, urlWithNotice } from "@/lib/session";
import { server } from "@/test/server";

afterEach(() => setRenderedSessionTag(null));

describe("the account tag on every proxy request", () => {
  function recordTags() {
    const seen: (string | null)[] = [];
    server.use(
      http.all("/api/backend/*", ({ request: received }) => {
        seen.push(received.headers.get(SESSION_TAG_HEADER));
        const path = new URL(received.url).pathname;
        if (path.endsWith("/artifact")) return new HttpResponse(new Blob(["PK"]), { headers: { "content-type": "application/octet-stream" } });
        return HttpResponse.json({ id: 1 }, { status: path.endsWith("/resumes") ? 201 : 200 });
      }),
    );
    return seen;
  }

  it("is sent by JSON calls, the resume upload and the artifact download alike", async () => {
    const seen = recordTags();
    setRenderedSessionTag("TAG-OF-ACCOUNT-A");
    await request("profile");
    await uploadResume(new File(["%PDF"], "resume.pdf", { type: "application/pdf" }));
    await downloadArtifact(7);
    await apiFetch("jobs");
    expect(seen).toEqual(["TAG-OF-ACCOUNT-A", "TAG-OF-ACCOUNT-A", "TAG-OF-ACCOUNT-A", "TAG-OF-ACCOUNT-A"]);
  });

  it("is not sent when the page was rendered for nobody", async () => {
    const seen = recordTags();
    await request("profile");
    expect(seen).toEqual([null]);
  });

  it("cannot be bypassed: nothing under src/ fetches the proxy except apiFetch", () => {
    const root = join(__dirname, "..");
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) {
          if (name !== "node_modules" && name !== "test") walk(path);
        } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
          const text = readFileSync(path, "utf8");
          const file = relative(root, path).replace(/\\/g, "/");
          if (file !== "lib/api/client.ts" && /fetch\(\s*[`'"]\/api\/backend/.test(text)) offenders.push(file);
        }
      }
    };
    walk(root);
    // The one place allowed to: apiFetch builds the URL from a template.
    expect(offenders).toEqual([]);
    expect(readFileSync(join(root, "lib/api/client.ts"), "utf8")).toContain("return fetch(`/api/backend/${path}`");
  });
});

describe("what a session failure does", () => {
  it("routes 409 session_changed and 401 session_required, from queries and mutations, to the handler", async () => {
    const seen: [string, string][] = [];
    const client = makeQueryClient((error, source) => seen.push([error.code, source]));
    await client.fetchQuery({ queryKey: ["a"], queryFn: () => Promise.reject(new ApiError(409, "session_changed", "x")), retry: false }).catch(() => undefined);
    await client.fetchQuery({ queryKey: ["b"], queryFn: () => Promise.reject(new ApiError(401, "session_required", "x")), retry: false }).catch(() => undefined);
    await client
      .getMutationCache()
      .build(client, { mutationFn: () => Promise.reject(new ApiError(401, "session_required", "x")) })
      .execute(undefined)
      .catch(() => undefined);
    // Not session failures: an ordinary 409, the API's own 401, a 404.
    await client.fetchQuery({ queryKey: ["c"], queryFn: () => Promise.reject(new ApiError(409, "conflict", "x")), retry: false }).catch(() => undefined);
    await client.fetchQuery({ queryKey: ["d"], queryFn: () => Promise.reject(new ApiError(401, "unauthenticated", "x")), retry: false }).catch(() => undefined);
    await client.fetchQuery({ queryKey: ["e"], queryFn: () => Promise.reject(new ApiError(404, "not_found", "x")), retry: false }).catch(() => undefined);
    expect(seen).toEqual([
      ["session_changed", "query"],
      ["session_required", "query"],
      ["session_required", "mutation"],
    ]);
  });

  it("the test client has no handler, so nothing navigates under test", async () => {
    const client = makeQueryClient();
    await expect(
      client.fetchQuery({ queryKey: ["a"], queryFn: () => Promise.reject(new ApiError(409, "session_changed", "x")), retry: false }),
    ).rejects.toBeInstanceOf(ApiError);
  });

  it("carries the reason across the reload in the URL", () => {
    const location = new URL("http://localhost:3000/jobs/4?tab=a#top") as unknown as Location;
    expect(urlWithNotice("changed", location)).toBe("/jobs/4?tab=a&session=changed#top");
  });
});
