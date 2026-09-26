/**
 * The EasyApply web app's proxy and the API behind it, at the network edge.
 *
 * Plays the part of `POST /api/backend/jobs` with the API's own identity rule
 * (services/api/app/services/job/service.py `_find_existing`): the same source
 * and external id — or, without one, the same URL — answers 200 with the job
 * already saved, unchanged; anything else is a new job and 201. Every request
 * is recorded as it arrived, so a test can assert what went on the wire.
 *
 * And the reads and writes after a save, with the API's rules that the
 * extension depends on — but never its judgments:
 *
 *   - Everything is per account. Another person's job, match or run is a 404,
 *     exactly as the API's service layer answers it.
 *   - A job's requirements are the fixture candidate, or whatever sequence of
 *     answers a test scripts (`scriptRequirements`), e.g. processing twice and
 *     then completed.
 *   - Matching answers 409 until the job is structured or when the version is
 *     a draft, 200 with the stored match for the same (job, version), else 201
 *     with a match built from a TEMPLATE the test controls. No verdict is
 *     computed here: the extension must show whatever the backend decided.
 *   - Tailoring opens a run (201), answers 200 with the run in flight for the
 *     same (job, version), and walks each run through a scripted list of
 *     statuses, one step per read.
 *
 * And the web app's session contract (packages/shared WebSession): the session
 * route reports the account's tag, and the proxy answers 401
 * `session_required` without a session FIRST, then 409 `session_changed` for a
 * request whose `X-EasyApply-Session` is not the signed-in account's tag. A
 * request without the header is not checked, as the proxy does for old clients.
 */

import { createHash } from "node:crypto";

import type {
  JobDetail,
  JobIngestionRequest,
  JobRequirementsResult,
  MatchDetail,
  ProfileVersionSummary,
  Snapshot,
  TailoringRunDetail,
  WebAuthMode,
} from "@easyapply/shared";

import {
  jobRun,
  matchDetail,
  profileVersion,
  requirementsFor,
  SNAPSHOT,
  tailoringRun,
  tailoringSummary,
} from "./backend-fixtures";

export interface RecordedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string;
  credentials: RequestCredentials | undefined;
  redirect: RequestRedirect | undefined;
}

export interface CannedResponse {
  status: number;
  body: string;
  contentType?: string;
}

export function jobDetail(payload: JobIngestionRequest, id: number): JobDetail {
  return {
    id,
    source_type: payload.source_type,
    source_url: payload.source_url,
    job_title: payload.job_title,
    company_name: payload.company_name ?? null,
    location_raw: payload.location_raw ?? null,
    employment_type: null,
    workplace_type: null,
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    salary_period: null,
    created_at: "2026-09-26T09:00:00Z",
    latest_run: jobRun(id * 10),
    external_job_id: payload.external_job_id ?? null,
    canonical_url: payload.source_url,
    description_text: payload.description_text ?? null,
    updated_at: "2026-09-26T09:00:01Z",
  };
}

/**
 * Who the fake web app says this browser is. Null: dev-session, signed out.
 *
 * `account` is the account's stable key — whose jobs, matches and profile they
 * are — and defaults to the email (Entra accounts may have none). `sessionTag`
 * is what the session route reports: by default one derived from the account,
 * so signing in again as the same account keeps it, as the web app's does;
 * `null` plays a web app older than tags, which reports none.
 */
export type FakeSession = {
  mode: WebAuthMode;
  email: string | null;
  name?: string | null;
  account?: string;
  sessionTag?: string | null;
} | null;

export const CONFIGURED: FakeSession = { mode: "development", email: "dev@easyapply.local" };

/** The tag the fake web app gives an account: 22 base64url characters, like the web app's. */
export function fakeSessionTag(account: string): string {
  return createHash("sha256").update(`fake-account-tag\n${account}`).digest("base64url").slice(0, 22);
}

/** Whose data a session's requests are for. */
export function accountOf(session: NonNullable<FakeSession>): string {
  return session.account ?? session.email ?? "unnamed-account";
}

/** The tag the session route reports for a session (null: none). */
export function tagOf(session: NonNullable<FakeSession>): string | null {
  return session.sessionTag === undefined ? fakeSessionTag(accountOf(session)) : session.sessionTag;
}

type Owned<T> = T & { owner: string };

/** One person's profile: its versions and the snapshot of the current one. Null: no profile at all. */
export interface FakeProfile {
  versions: ProfileVersionSummary[];
  snapshot: Snapshot;
}

/** A requirements answer for a job, built from the job as it is stored. */
export type RequirementsStep = (job: JobDetail) => JobRequirementsResult;

function notFound(what = "Not Found"): Response {
  return Response.json({ detail: what, code: "not_found" }, { status: 404 });
}

function conflict(detail: string): Response {
  return Response.json({ detail, code: "conflict" }, { status: 409 });
}

function strip<T extends { owner: string }>(value: T): Omit<T, "owner"> {
  const copy: Partial<T> = { ...value };
  delete copy.owner;
  return copy as Omit<T, "owner">;
}

export function createFakeApi({
  webUrl = "http://localhost:3000",
  session: initialSession = CONFIGURED,
}: { webUrl?: string; session?: FakeSession } = {}) {
  const requests: RecordedRequest[] = [];
  const jobs: Owned<JobDetail>[] = [];
  const profiles = new Map<string, FakeProfile | null>();
  const matches: Owned<MatchDetail>[] = [];
  const runs: Owned<TailoringRunDetail & { script: string[] }>[] = [];
  const requirementScripts = new Map<number, RequirementsStep[]>();
  // Canned answers per path, so a test about the save is not answered by the
  // session question that happens to go first.
  const queued = new Map<string, (CannedResponse | "network-error")[]>();
  let session = initialSession;
  let matchTemplate: (id: number, jobId: number, versionId: number) => MatchDetail = matchDetail;
  let tailoringScript: string[] = ["queued", "generating", "validating", "rendering", "completed"];
  let finalRun: (run: TailoringRunDetail) => TailoringRunDetail = (run) => run;

  function identity(payload: { source_type: string; source_url: string; external_job_id?: string | null }): string {
    return payload.external_job_id
      ? `${payload.source_type}|id|${payload.external_job_id}`
      : `${payload.source_type}|url|${payload.source_url}`;
  }

  /** Everyone starts with a confirmed, current version 1 — unless a test says otherwise. */
  function profileOf(owner: string): FakeProfile | null {
    if (!profiles.has(owner)) {
      profiles.set(owner, { versions: [{ ...versionFor(owner, 1) }], snapshot: SNAPSHOT });
    }
    return profiles.get(owner) ?? null;
  }

  /** Version ids are global in the API; make them differ per person too. */
  function versionFor(owner: string, number: number): ProfileVersionSummary {
    const base = [...profiles.keys()].indexOf(owner);
    const offset = (base < 0 ? profiles.size : base) * 100;
    return {
      id: offset + number,
      version_number: number,
      status: "confirmed",
      is_current: true,
      created_by_type: "user",
      created_at: "2026-09-26T08:00:00Z",
      confirmed_at: "2026-09-26T08:00:00Z",
    };
  }

  function requirementsOf(job: JobDetail): JobRequirementsResult {
    const script = requirementScripts.get(job.id);
    const step = script && script.length > 1 ? script.shift() : script?.[0];
    return step ? step(job) : requirementsFor(job);
  }

  /** What the next read of a job's requirements would say, without consuming a scripted step. */
  function peekRequirements(job: JobDetail): JobRequirementsResult {
    const step = requirementScripts.get(job.id)?.[0];
    return step ? step(job) : requirementsFor(job);
  }

  function runDetail(run: Owned<TailoringRunDetail & { script: string[] }>): TailoringRunDetail {
    const detail = strip(run) as TailoringRunDetail & { script?: string[] };
    delete detail.script;
    return detail;
  }

  function advance(run: Owned<TailoringRunDetail & { script: string[] }>): void {
    const next = run.script.shift();
    if (!next) return;
    const stepped = tailoringRun(run.id, run.job_id, run.profile_version_id, {
      status: next,
      match_result_id: run.match_result_id,
      attempt: run.attempt,
    });
    Object.assign(run, next === "completed" || next === "failed" ? finalRun(stepped) : stepped);
  }

  async function backend(method: string, path: string, body: string, owner: string): Promise<Response> {
    const parts = path.split("/").filter(Boolean);

    if (method === "POST" && path === "/jobs") {
      // Per person, as the API scopes identity: one person's save is never
      // another's "already saved".
      const payload = JSON.parse(body) as JobIngestionRequest;
      const existing = jobs.find((job) => job.owner === owner && identity(job) === identity(payload));
      if (existing) return Response.json(strip(existing), { status: 200 });
      const created = jobDetail(payload, jobs.length + 1);
      jobs.push({ ...created, owner });
      return Response.json(created, { status: 201 });
    }

    const ownJob = (id: string | undefined) => jobs.find((job) => job.owner === owner && job.id === Number(id));

    // /jobs/:id/requirements
    if (method === "GET" && parts[0] === "jobs" && parts[2] === "requirements" && parts.length === 3) {
      const job = ownJob(parts[1]);
      return job ? Response.json(requirementsOf(job)) : notFound("That job does not exist.");
    }

    // /profile/versions[/current]
    if (method === "GET" && path === "/profile/versions") {
      const profile = profileOf(owner);
      return profile ? Response.json(profile.versions) : notFound("No profile exists for this user.");
    }
    if (method === "GET" && path === "/profile/versions/current") {
      const profile = profileOf(owner);
      const current = profile?.versions.find((version) => version.is_current);
      if (!profile) return notFound("No profile exists for this user.");
      return current ? Response.json(profileVersion(current, profile.snapshot)) : notFound("This profile has no current version.");
    }

    // /jobs/:id/matches
    if (parts[0] === "jobs" && parts[2] === "matches" && parts.length === 3) {
      const job = ownJob(parts[1]);
      if (!job) return notFound("That job does not exist.");
      if (method === "GET") {
        return Response.json(
          matches
            .filter((match) => match.owner === owner && match.job_id === job.id)
            .reverse()
            .map((match) => {
              // A listing is MatchSummary: no per-requirement results.
              const { requirement_results: _results, has_unmet_required: _unmet, ...summary } = strip(match);
              return summary;
            }),
        );
      }
      if (method === "POST") {
        const requested = JSON.parse(body || "{}") as { profile_version_id?: number | null };
        if (peekRequirements(job).requirements === null) {
          return conflict("This job has not been structured yet, so there are no requirements to match against.");
        }
        const profile = profileOf(owner);
        const version =
          requested.profile_version_id == null
            ? profile?.versions.find((candidate) => candidate.is_current)
            : profile?.versions.find((candidate) => candidate.id === requested.profile_version_id);
        if (!version) return requested.profile_version_id == null ? conflict("This profile has no confirmed version yet.") : notFound("That profile version does not exist.");
        if (version.status !== "confirmed") return conflict("Only a confirmed profile version can be matched.");
        const existing = matches.find(
          (match) => match.owner === owner && match.job_id === job.id && match.profile_version_id === version.id,
        );
        if (existing) return Response.json(strip(existing), { status: 200 });
        const created = matchTemplate(1000 + matches.length + 1, job.id, version.id);
        created.profile_version_number = version.version_number;
        matches.push({ ...created, owner });
        return Response.json(created, { status: 201 });
      }
    }

    // /matches/:id
    if (method === "GET" && parts[0] === "matches" && parts.length === 2) {
      const match = matches.find((candidate) => candidate.owner === owner && candidate.id === Number(parts[1]));
      return match ? Response.json(strip(match)) : notFound("That match does not exist.");
    }

    // /jobs/:id/tailored-resumes
    if (parts[0] === "jobs" && parts[2] === "tailored-resumes" && parts.length === 3) {
      const job = ownJob(parts[1]);
      if (!job) return notFound("That job does not exist.");
      if (method === "GET") {
        return Response.json(
          runs
            .filter((run) => run.owner === owner && run.job_id === job.id)
            .reverse()
            .map((run) => tailoringSummary(runDetail(run))),
        );
      }
      if (method === "POST") {
        const requested = JSON.parse(body || "{}") as { profile_version_id?: number | null; match_result_id?: number | null };
        if (peekRequirements(job).requirements === null) {
          return conflict("This job has not been structured yet, so there is nothing to tailor a resume against.");
        }
        const profile = profileOf(owner);
        const version = profile?.versions.find((candidate) => candidate.id === requested.profile_version_id);
        if (!version) return notFound("That profile version does not exist.");
        if (version.status !== "confirmed") return conflict("Only a confirmed profile version can be tailored.");
        if (requested.match_result_id != null) {
          const match = matches.find((candidate) => candidate.owner === owner && candidate.id === requested.match_result_id);
          if (!match) return notFound("That match does not exist.");
        }
        const inFlight = runs.find(
          (run) => run.owner === owner && run.job_id === job.id && run.profile_version_id === version.id && !run.is_terminal,
        );
        if (inFlight) return Response.json(runDetail(inFlight), { status: 200 });
        const attempt = runs.filter((run) => run.owner === owner && run.job_id === job.id && run.profile_version_id === version.id).length + 1;
        const [first = "queued", ...rest] = tailoringScript;
        const run = {
          ...tailoringRun(5000 + runs.length + 1, job.id, version.id, {
            status: first,
            attempt,
            match_result_id: requested.match_result_id ?? null,
          }),
          script: [...rest],
          owner,
        };
        runs.push(run);
        return Response.json(runDetail(run), { status: 201 });
      }
    }

    // /tailored-resumes/:id
    if (method === "GET" && parts[0] === "tailored-resumes" && parts.length === 2) {
      const run = runs.find((candidate) => candidate.owner === owner && candidate.id === Number(parts[1]));
      if (!run) return notFound("That tailored resume does not exist.");
      advance(run);
      return Response.json(runDetail(run));
    }

    return notFound();
  }

  const fetch = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const headers: Record<string, string> = {};
    new Headers(init.headers).forEach((value, name) => {
      headers[name] = value;
    });
    const body = typeof init.body === "string" ? init.body : "";
    const method = init.method ?? "GET";
    requests.push({ url, method, headers, body, credentials: init.credentials, redirect: init.redirect });

    const path = new URL(url).pathname;
    const next = queued.get(path)?.shift();
    if (next === "network-error") throw new TypeError("Failed to fetch");
    if (next) {
      return new Response(next.body, {
        status: next.status,
        headers: { "Content-Type": next.contentType ?? "application/json" },
      });
    }

    // The browser's session reaches the web app only when the request asks the
    // browser to send it, as `credentials: "include"` does.
    const presented = init.credentials === "include" || init.credentials === undefined ? session : null;
    const signedIn = session?.mode === "development" ? session : presented;

    if (!url.startsWith(`${webUrl}/`)) return notFound();

    if (path === "/api/auth/session" && method === "GET") {
      if (!signedIn) return Response.json({ mode: "dev-session", authenticated: false, email: null });
      const tag = tagOf(signedIn);
      return Response.json({
        mode: signedIn.mode,
        authenticated: true,
        email: signedIn.email,
        ...(signedIn.name === undefined ? {} : { name: signedIn.name }),
        ...(tag === null ? {} : { sessionTag: tag }),
      });
    }

    if (!path.startsWith("/api/backend/")) return notFound();

    // The proxy: no session first, then a session for someone else.
    if (!signedIn) {
      return Response.json({ detail: "You are not signed in to EasyApply.", code: "session_required" }, { status: 401 });
    }
    const claimed = headers["x-easyapply-session"];
    const current = tagOf(signedIn);
    if (claimed !== undefined && current !== null && claimed !== current) {
      return Response.json(
        { detail: "This browser is signed in to EasyApply as a different account now.", code: "session_changed" },
        { status: 409 },
      );
    }

    return backend(method, path.slice("/api/backend".length), body, accountOf(signedIn));
  };

  return {
    fetch,
    requests,
    jobs,
    matches,
    runs,
    /** The next request to this path gets this answer instead of the normal one. */
    respondNextWith(response: CannedResponse | "network-error", path = "/api/backend/jobs") {
      queued.set(path, [...(queued.get(path) ?? []), response]);
    },
    /** Change who the browser is signed in as (null: signed out). */
    setSession(next: FakeSession) {
      session = next;
    },
    /** Set a person's profile. Null: no profile at all. */
    setProfile(owner: string, profile: FakeProfile | null) {
      profiles.set(owner, profile);
    },
    /** A version summary for this person, numbered `number` (confirmed and current unless overridden). */
    version(owner: string, number: number, overrides: Partial<ProfileVersionSummary> = {}): ProfileVersionSummary {
      if (!profiles.has(owner)) profiles.set(owner, null);
      return { ...versionFor(owner, number), ...overrides };
    },
    /** The requirements answers for a job, in order; the last one repeats. */
    scriptRequirements(jobId: number, steps: RequirementsStep[]) {
      requirementScripts.set(jobId, [...steps]);
    },
    /** How the next new match is built. */
    setMatchTemplate(template: (id: number, jobId: number, versionId: number) => MatchDetail) {
      matchTemplate = template;
    },
    /** The statuses a new tailoring run walks through, one per read; and how its final state looks. */
    setTailoringScript(statuses: string[], final: (run: TailoringRunDetail) => TailoringRunDetail = (run) => run) {
      tailoringScript = statuses;
      finalRun = final;
    },
    /** The requests that were job saves, as opposed to everything else. */
    get saves() {
      return requests.filter((request) => request.method === "POST" && new URL(request.url).pathname === "/api/backend/jobs");
    },
    /** Every request to the proxy, reads and writes. */
    get backend() {
      return requests.filter((request) => new URL(request.url).pathname.startsWith("/api/backend/"));
    },
    /** The tag the session route reports right now (null: signed out, or no tag). */
    get currentTag(): string | null {
      return session ? tagOf(session) : null;
    },
  };
}

export type FakeApi = ReturnType<typeof createFakeApi>;
