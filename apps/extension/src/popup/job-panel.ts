/**
 * The popup's controller for one saved job: loads what the backend knows about
 * it, re-reads what is still moving, and carries out the two actions the
 * person can take here — match, and tailor a resume.
 *
 * Every answer goes through the state machine (src/workflow/job-flow.ts) with
 * the key it was asked under, so an answer for another account, tab, job or
 * profile version is dropped rather than drawn. Every request goes through the
 * service worker (./port.ts → src/background/service.ts), which makes it through
 * the web app's proxy with the browser's own session and the tag of the account
 * this panel is for — so once the browser is signed in to someone else, the
 * proxy refuses it (409 `session_changed`), the flow discards everything, and
 * the popup is told.
 *
 * Matching and tailoring are explicit actions, as on the web app: a match and a
 * tailored resume are stored records, so creating one is the person's decision.
 * Opening the popup only reads.
 *
 * Before either action, the profile is read again. If another tab has made a
 * different version current in the meantime, the popup shows that version's
 * state instead of acting — a match must never be requested for, or labelled
 * with, a version that is no longer the one on screen.
 */

import type { MatchDetail, TailoringRunDetail } from "@easyapply/shared";

import type { ApiResult } from "../api/client";
import {
  canMatch,
  canTailor,
  currentVersion,
  initialFlow,
  matchForTailoring,
  reduce,
  requirementsSettled,
  tailoringSettled,
  type FlowEvent,
  type FlowKey,
  type JobFlow,
} from "../workflow/job-flow";
import { pollUntil, REQUIREMENTS_POLL, TAILORING_POLL, type PollPolicy, type Sleep } from "../workflow/poll";
import { renderJobPanel } from "./job-view";
import type { PopupPort } from "./port";

export interface JobPanelOptions {
  container: HTMLElement;
  port: PopupPort;
  webUrl: string;
  extensionId: string;
  key: FlowKey;
  /** Told once, when the backend says the session has ended. */
  onSessionExpired?: () => void;
  /** Told once, when the backend says the browser is signed in to a different account now. */
  onSessionChanged?: () => void;
  /** For tests: the polling policies and the wait between checks. */
  polling?: { requirements?: PollPolicy; tailoring?: PollPolicy; wait?: Sleep };
}

class Superseded extends Error {
  constructor() {
    super("superseded");
  }
}

export class JobPanel {
  private flow: JobFlow;
  private readonly pending = new Set<Promise<unknown>>();
  /** Cancels polls for the job as a whole. */
  private readonly lifetime = new AbortController();
  /** Cancels polls that belong to one profile version; replaced when the version changes. */
  private versionScope = new AbortController();
  /** Set once the panel is gone: a late answer is neither drawn nor reported. */
  private disposed = false;

  constructor(private readonly options: JobPanelOptions) {
    this.flow = initialFlow(options.key);
  }

  get state(): JobFlow {
    return this.flow;
  }

  start(): Promise<void> {
    this.render();
    return this.track(this.loadAll());
  }

  /** Resolves once nothing this panel started is still running. For tests. */
  async settled(): Promise<void> {
    while (this.pending.size > 0) await Promise.allSettled([...this.pending]);
  }

  /** The popup is going away, or showing someone else: stop every poll, and read nothing more. */
  dispose(): void {
    this.disposed = true;
    this.lifetime.abort(new Superseded());
    this.versionScope.abort(new Superseded());
  }

  // --- plumbing ----------------------------------------------------------------------

  private track<T>(work: Promise<T>): Promise<T> {
    const guarded = work.catch((error: unknown) => {
      // A cancelled poll is how a superseded view ends; nothing to report.
      if (!(error instanceof Superseded)) throw error;
      return undefined as T;
    });
    this.pending.add(guarded);
    void guarded.finally(() => this.pending.delete(guarded)).catch(() => undefined);
    return guarded;
  }

  private dispatch(event: FlowEvent): void {
    if (this.disposed) return;
    const before = this.flow;
    this.flow = reduce(this.flow, event);
    if (this.flow === before) return;
    if (this.flow.session === "changed") {
      // Another account is signed in now: nothing this panel was doing is theirs.
      this.dispose();
      this.render();
      this.options.onSessionChanged?.();
      return;
    }
    if (this.flow.versionId !== before.versionId) {
      // Whatever was polling for the old version is no longer on screen.
      this.versionScope.abort(new Superseded());
      this.versionScope = new AbortController();
    }
    this.render();
    if (this.flow.session === "expired" && before.session !== "expired") this.options.onSessionExpired?.();
  }

  private render(): void {
    const { container, webUrl, extensionId } = this.options;
    renderJobPanel(container, this.flow, {
      doc: container.ownerDocument,
      webUrl,
      extensionId,
      actions: {
        recheckRequirements: () => void this.track(this.recheckRequirements()),
        match: () => void this.track(this.requestMatch()),
        tailor: () => void this.track(this.startTailoring()),
        recheckTailoring: () => void this.track(this.recheckTailoring()),
      },
    });
  }

  private get key(): FlowKey {
    return this.options.key;
  }

  /** The tag every request of this panel carries: the account it is for. */
  private get tag(): string | null {
    return this.options.key.sessionTag;
  }

  private get wait(): Sleep | undefined {
    return this.options.polling?.wait;
  }

  // --- loading -------------------------------------------------------------------------

  private async loadAll(): Promise<void> {
    await Promise.all([this.loadRequirements(), this.loadProfile()]);
    await this.loadForVersion();
  }

  private async loadRequirements(): Promise<void> {
    const outcome = await pollUntil(() => this.options.port.api({ op: "requirements", jobId: this.key.jobId }, this.tag), requirementsSettled, {
      policy: this.options.polling?.requirements ?? REQUIREMENTS_POLL,
      signal: this.lifetime.signal,
      wait: this.wait,
      onProgress: (result, checks) => this.dispatch({ type: "requirements", key: this.key, result, checks, final: false }),
    });
    this.dispatch({ type: "requirements", key: this.key, result: outcome.value, checks: outcome.checks, final: !outcome.settled });
  }

  private async loadProfile(): Promise<void> {
    if (this.flow.profile.kind === "idle") this.dispatch({ type: "profile_loading", key: this.key });
    const result = await this.options.port.api({ op: "profileVersions" }, this.tag);
    this.dispatch({ type: "profile", key: this.key, result });
  }

  /** The match and tailored resume for the version now current, if there is one and the job is readable. */
  private async loadForVersion(): Promise<void> {
    const version = currentVersion(this.flow);
    if (!version || this.flow.requirements.kind !== "ready" || this.flow.session !== "active") return;
    await Promise.all([this.loadMatch(version.id), this.loadTailoring(version.id)]);
  }

  private async loadMatch(versionId: number): Promise<void> {
    this.dispatch({ type: "match_loading", key: this.key, versionId });
    const listed = await this.options.port.api({ op: "matches", jobId: this.key.jobId }, this.tag);
    if (!listed.ok) {
      this.dispatch({ type: "match_loaded", key: this.key, versionId, result: listed });
      return;
    }
    // Newest first, as the API lists them; the newest for THIS version, as the web app shows.
    const newest = listed.data.find((match) => match.profile_version_id === versionId && match.job_id === this.key.jobId);
    const result: ApiResult<MatchDetail | null> = newest
      ? await this.options.port.api({ op: "match", matchId: newest.id }, this.tag)
      : { ok: true, status: 200, data: null };
    this.dispatch({ type: "match_loaded", key: this.key, versionId, result });
  }

  private async loadTailoring(versionId: number): Promise<void> {
    this.dispatch({ type: "tailoring_loading", key: this.key, versionId });
    const listed = await this.options.port.api({ op: "tailoringRuns", jobId: this.key.jobId }, this.tag);
    if (!listed.ok) {
      this.dispatch({ type: "tailoring_loaded", key: this.key, versionId, result: listed, checks: 1, final: false });
      return;
    }
    const newest = listed.data.find((run) => run.profile_version_id === versionId && run.job_id === this.key.jobId);
    if (!newest) {
      this.dispatch({ type: "tailoring_loaded", key: this.key, versionId, result: { ok: true, status: 200, data: null }, checks: 1, final: false });
      return;
    }
    await this.followRun(versionId, newest.id);
  }

  /** Read a run until it stops moving (or the budget runs out), showing each stage on the way. */
  private async followRun(versionId: number, runId: number): Promise<void> {
    const outcome = await pollUntil<ApiResult<TailoringRunDetail | null>>(
      () => this.options.port.api({ op: "tailoringRun", runId }, this.tag),
      tailoringSettled,
      {
        policy: this.options.polling?.tailoring ?? TAILORING_POLL,
        signal: this.versionScope.signal,
        wait: this.wait,
        onProgress: (result, checks) =>
          this.dispatch({ type: "tailoring_loaded", key: this.key, versionId, result, checks, final: false }),
      },
    );
    this.dispatch({
      type: "tailoring_loaded",
      key: this.key,
      versionId,
      result: outcome.value,
      checks: outcome.checks,
      final: !outcome.settled,
    });
  }

  // --- actions ----------------------------------------------------------------------------

  private async recheckRequirements(): Promise<void> {
    this.dispatch({ type: "requirements_recheck", key: this.key });
    await this.loadRequirements();
    await this.loadForVersion();
  }

  /** Re-read the profile; true when the version on screen is still the current one. */
  private async versionStillCurrent(): Promise<number | null> {
    const before = this.flow.versionId;
    await this.loadProfile();
    const now = this.flow.versionId;
    if (now === null) return null;
    if (now !== before) {
      // Another tab changed which version is current. Show that version's
      // match and resume; do not act on the old one.
      await this.loadForVersion();
      return null;
    }
    return now;
  }

  private async requestMatch(): Promise<void> {
    if (!canMatch(this.flow)) return;
    const versionId = await this.versionStillCurrent();
    if (versionId === null || !canMatch(this.flow)) return;
    this.dispatch({ type: "match_requested", key: this.key, versionId });
    const result = await this.options.port.api({ op: "createMatch", jobId: this.key.jobId, profileVersionId: versionId }, this.tag);
    this.dispatch({ type: "match_created", key: this.key, versionId, result });
    if (!result.ok && result.kind === "conflict") {
      // The API refuses for more than one reason under one code; find out
      // which by reading state, not by parsing its sentence.
      await Promise.all([this.loadRequirements(), this.loadProfile()]);
    }
  }

  private async startTailoring(): Promise<void> {
    if (!canTailor(this.flow)) return;
    const versionId = await this.versionStillCurrent();
    if (versionId === null || !canTailor(this.flow)) return;
    const matchResultId = matchForTailoring(this.flow);
    this.dispatch({ type: "tailoring_requested", key: this.key, versionId });
    const result = await this.options.port.api(
      {
        op: "startTailoring",
        jobId: this.key.jobId,
        profileVersionId: versionId,
        matchResultId,
      },
      this.tag,
    );
    this.dispatch({ type: "tailoring_started", key: this.key, versionId, result });
    if (result.ok && !tailoringSettled(result)) await this.followRun(versionId, result.data.id);
    if (!result.ok && result.kind === "conflict") await Promise.all([this.loadRequirements(), this.loadProfile()]);
  }

  private async recheckTailoring(): Promise<void> {
    const state = this.flow.tailoring;
    const versionId = this.flow.versionId;
    if (versionId === null || state.kind !== "still_running") return;
    await this.followRun(versionId, state.run.id);
  }
}
