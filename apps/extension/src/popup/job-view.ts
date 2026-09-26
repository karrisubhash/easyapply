/**
 * The popup's view of one saved job: requirements, the match, the tailored
 * resume. A pure function of the state machine (src/workflow/job-flow.ts) —
 * it renders whatever state the backend's answers put the flow in, and wires
 * the few buttons back to the controller (./job-panel.ts).
 *
 * What it shows is the backend's: requirement text and its classification,
 * verdicts, counts, coverage, explanations, evidence, run stages, grounding.
 * Failure wording comes from the shared tables (@easyapply/shared
 * presentation.ts) keyed by the run's error CODE; a run's `error_message` is
 * never shown, because it can quote the job description or the prompt — and
 * the prompt contains the person's career history.
 */

import {
  JOB_FAILURE_HELP,
  JOB_STAGE_LABELS,
  jobFailureTitle,
  TAILORING_FAILURE_HELP,
  TAILORING_STAGE_LABELS,
  tailoringFailureTitle,
  type MatchDetail,
  type MatchRequirementResult,
  type TailoringRunDetail,
} from "@easyapply/shared";

import { webLinks } from "../api/links";
import { requirementGroups, REQUIREMENT_KIND_LABELS, type RequirementRow } from "../jobs/requirements";
import { axisLine, matchGroups, percent, statusLabel, tierLabel } from "../matching/present";
import {
  canMatch,
  canTailor,
  currentVersion,
  type JobFlow,
  type MatchState,
  type RequirementsState,
  type TailoringState,
} from "../workflow/job-flow";
import { describeFailure, describeSessionChanged, formatDate, type DescribeContext } from "./describe";
import { element, linkElement, noticeElement, type Child } from "./dom";

export interface JobViewActions {
  recheckRequirements(): void;
  match(): void;
  tailor(): void;
  recheckTailoring(): void;
}

export interface JobViewContext extends DescribeContext {
  doc: Document;
  actions: JobViewActions;
}

function button(ctx: JobViewContext, id: string, text: string, onClick: () => void, { primary = false, disabled = false } = {}) {
  const node = element(ctx.doc, "button", { type: "button", id, class: primary ? "primary" : "secondary" }, text);
  node.disabled = disabled;
  node.addEventListener("click", () => {
    node.disabled = true;
    onClick();
  });
  return node;
}

function status(ctx: JobViewContext, text: string, id?: string): HTMLElement {
  return element(ctx.doc, "p", { class: "lede", role: "status", id }, text);
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

// --- requirements ------------------------------------------------------------------

function requirementItem(ctx: JobViewContext, row: RequirementRow): HTMLElement {
  const { doc } = ctx;
  return element(
    doc,
    "li",
    { class: "req-row", "data-kind": row.kind },
    element(doc, "span", { class: "tag" }, REQUIREMENT_KIND_LABELS[row.kind]),
    " ",
    element(doc, "span", { class: "req-text" }, row.text),
    row.detail ? element(doc, "span", { class: "req-detail" }, ` · ${row.detail}`) : null,
    row.citation
      ? element(
          doc,
          "q",
          { class: row.unverified ? "citation citation--unverified" : "citation" },
          row.citation,
        )
      : null,
    row.unverified
      ? element(doc, "span", { class: "hint hint--warning" }, " This quote wasn't found in the description as written.")
      : null,
  );
}

function renderRequirements(ctx: JobViewContext, flow: JobFlow, state: RequirementsState): Child[] {
  const { doc } = ctx;
  const jobLink = { href: webLinks.job(ctx.webUrl, flow.key.jobId), text: "Open the job in EasyApply", id: "requirements-open-job" };
  switch (state.kind) {
    case "loading":
      return [status(ctx, "Loading this job's requirements…")];
    case "processing":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: "Job saved. EasyApply is extracting requirements.",
            lines: [`${state.run ? (JOB_STAGE_LABELS[state.run.status] ?? state.run.status) : "Queued"}. Checking again shortly.`],
          },
          "requirements-status",
        ),
      ];
    case "still_processing":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: "EasyApply is still reading this job.",
            lines: ["It hasn't finished yet. Check again in a moment, or follow it in EasyApply."],
            link: jobLink,
          },
          "requirements-status",
        ),
        button(ctx, "requirements-recheck", "Check again", ctx.actions.recheckRequirements),
      ];
    case "failed": {
      const code = state.run.error_code;
      return [
        noticeElement(
          doc,
          {
            tone: "error",
            title: jobFailureTitle(state.run),
            lines: [
              (code && JOB_FAILURE_HELP[code]) ||
                `EasyApply couldn't read requirements from this job${code ? ` (${code})` : ""}.`,
              state.run.can_retry
                ? "You can retry it from the job's page in EasyApply."
                : "The job's page in EasyApply has the details.",
            ],
            link: jobLink,
          },
          "requirements-status",
        ),
      ];
    }
    case "unavailable":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: "EasyApply has no requirements for this job yet.",
            lines: ["It hasn't been read. Open the job in EasyApply to process it."],
            link: jobLink,
          },
          "requirements-status",
        ),
      ];
    case "error":
      return [
        noticeElement(doc, describeFailure(state.failure, "load this job's requirements", ctx), "requirements-status"),
        button(ctx, "requirements-recheck", "Try again", ctx.actions.recheckRequirements),
      ];
    case "ready": {
      const { result } = state;
      const groups = requirementGroups(result.requirements.candidate);
      const latest = result.job.latest_run;
      const newer =
        latest && result.run && latest.id !== result.run.id
          ? latest.status === "failed"
            ? "A later attempt to re-read this job failed; these are from its last completed reading."
            : "EasyApply is re-reading this job; these are from its last completed reading."
          : null;
      const empty = groups.required.length === 0 && groups.preferred.length === 0;
      return [
        element(doc, "p", { class: "lede", role: "status", id: "requirements-status" }, "Requirements are ready. Your match is below."),
        newer ? element(doc, "p", { class: "hint", id: "requirements-newer" }, newer) : null,
        empty
          ? element(doc, "p", { class: "hint", id: "requirements-empty" }, "No structured requirements were found in this job's description.")
          : null,
        groups.required.length > 0
          ? element(
              doc,
              "div",
              { class: "group" },
              element(doc, "h4", {}, `Required (${groups.required.length})`),
              element(doc, "ul", { class: "req-list", id: "requirements-required" }, ...groups.required.map((row) => requirementItem(ctx, row))),
            )
          : null,
        groups.preferred.length > 0
          ? element(
              doc,
              "div",
              { class: "group" },
              element(doc, "h4", {}, `Preferred (${groups.preferred.length})`),
              element(doc, "ul", { class: "req-list", id: "requirements-preferred" }, ...groups.preferred.map((row) => requirementItem(ctx, row))),
            )
          : null,
        groups.responsibilities.length > 0
          ? element(
              doc,
              "details",
              { class: "responsibilities", id: "requirements-responsibilities" },
              element(doc, "summary", {}, `What the role involves (${groups.responsibilities.length}) — not requirements`),
              element(doc, "ul", {}, ...groups.responsibilities.map((text) => element(doc, "li", {}, text))),
            )
          : null,
        element(
          doc,
          "p",
          { class: "fineprint" },
          `Read by EasyApply's ${result.requirements.structurer === "deterministic" ? "rule-based reader" : `${result.requirements.structurer} reader`} ` +
            `on ${formatDate(result.requirements.created_at)}.`,
        ),
      ];
    }
  }
}

// --- the match --------------------------------------------------------------------

function resultItem(ctx: JobViewContext, result: MatchRequirementResult): HTMLElement {
  const { doc } = ctx;
  const evidence = result.evidence ?? [];
  return element(
    doc,
    "li",
    { class: `result result--${result.status}`, "data-status": result.status },
    element(
      doc,
      "p",
      { class: "result__head" },
      element(doc, "span", { class: `verdict verdict--${result.status}` }, statusLabel(result.status)),
      " ",
      element(doc, "span", { class: "result__label" }, result.requirement_label),
    ),
    element(doc, "p", { class: "result__why" }, result.explanation),
    evidence.length > 0
      ? element(
          doc,
          "ul",
          { class: "evidence" },
          ...evidence.map((item) =>
            element(
              doc,
              "li",
              { class: item.qualifies === false ? "evidence__item evidence__item--short" : "evidence__item" },
              element(doc, "span", { class: "evidence__label" }, item.label),
              item.detail ? element(doc, "span", { class: "evidence__detail" }, ` · ${item.detail}`) : null,
              item.quote ? element(doc, "q", { class: "citation" }, item.quote) : null,
              tierLabel(item.tier) ? element(doc, "span", { class: "tag tag--quiet" }, tierLabel(item.tier)) : null,
              item.note ? element(doc, "span", { class: "hint" }, ` ${item.note}`) : null,
            ),
          ),
        )
      : null,
  );
}

function renderMatchDetail(ctx: JobViewContext, flow: JobFlow, match: MatchDetail, created: boolean | null): Child[] {
  const { doc } = ctx;
  const groups = matchGroups(match.requirement_results ?? []);
  const version = match.profile_version_number ?? currentVersion(flow)?.version_number;
  return [
    element(
      doc,
      "p",
      { class: "lede", role: "status", id: "match-status" },
      `Matched against profile version ${version ?? "?"}.`,
      created === true ? " EasyApply just ran this match." : created === false ? " EasyApply had already matched this version; this is that result." : "",
    ),
    element(
      doc,
      "dl",
      { class: "axes", id: "match-axes" },
      element(doc, "dt", {}, "Required"),
      element(
        doc,
        "dd",
        { id: "match-required" },
        axisLine({
          total: match.required_total,
          matched: match.required_matched,
          missing: match.required_missing,
          uncertain: match.required_uncertain,
        }),
        match.required_total > 0 ? ` (${percent(match.required_coverage)}% coverage)` : "",
      ),
      element(doc, "dt", {}, "Preferred"),
      element(
        doc,
        "dd",
        { id: "match-preferred" },
        axisLine({
          total: match.preferred_total,
          matched: match.preferred_matched,
          missing: match.preferred_missing,
          uncertain: match.preferred_uncertain,
        }),
        match.preferred_total > 0 ? ` (${percent(match.preferred_coverage)}% coverage)` : "",
      ),
    ),
    match.has_unmet_required
      ? element(
          doc,
          "p",
          { class: "hint", id: "match-unmet" },
          "Some required items weren't found in your profile. That is what your profile shows, not a judgement about you.",
        )
      : null,
    groups.length === 0
      ? element(doc, "p", { class: "hint" }, "This posting has no structured requirements to check a profile against.")
      : element(
          doc,
          "div",
          { id: "match-results" },
          ...groups.map((group) =>
            element(
              doc,
              "div",
              { class: "group", "data-category": group.category },
              element(doc, "h4", {}, group.label),
              group.required.length > 0
                ? element(doc, "ul", { class: "results", "data-axis": "required" }, ...group.required.map((result) => resultItem(ctx, result)))
                : null,
              group.preferred.length > 0
                ? element(
                    doc,
                    "div",
                    {},
                    element(doc, "p", { class: "axis-label" }, "Preferred"),
                    element(doc, "ul", { class: "results", "data-axis": "preferred" }, ...group.preferred.map((result) => resultItem(ctx, result))),
                  )
                : null,
            ),
          ),
        ),
    element(
      doc,
      "p",
      { class: "fineprint", id: "match-provenance" },
      `Coverage of this posting's structured requirements by profile version ${version ?? "?"}, ` +
        `measured ${formatDate(match.as_of_date)} with ${match.algorithm_version}. It is not a prediction about an application.`,
    ),
    element(
      doc,
      "p",
      {},
      linkElement(doc, { href: webLinks.match(ctx.webUrl, flow.key.jobId, match.id), text: "Open the full match in EasyApply", id: "open-match" }),
    ),
  ];
}

function renderMatch(ctx: JobViewContext, flow: JobFlow, state: MatchState): Child[] {
  const { doc } = ctx;
  const profile = flow.profile;
  if (flow.requirements.kind !== "ready") {
    return [element(doc, "p", { class: "hint", id: "match-status" }, "Matching needs this job's requirements first.")];
  }
  switch (profile.kind) {
    case "idle":
    case "loading":
      return [status(ctx, "Checking your profile…", "match-status")];
    case "no_profile":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: "You don't have an EasyApply profile yet.",
            lines: ["Upload a resume in EasyApply to build one, confirm it, then match it against this job."],
            link: { href: webLinks.resume(ctx.webUrl), text: "Build your profile in EasyApply", id: "open-profile" },
          },
          "match-status",
        ),
      ];
    case "no_versions":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: "Your profile has no versions yet.",
            lines: ["Add your details in EasyApply and confirm them, then match them against this job."],
            link: { href: webLinks.profile(ctx.webUrl), text: "Open your profile", id: "open-profile" },
          },
          "match-status",
        ),
      ];
    case "drafts_only":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: "Confirm your profile to match it.",
            lines: [
              "Your profile has a draft that isn't confirmed yet. Matching only uses a confirmed version, because a draft can still change.",
            ],
            link: { href: webLinks.profileVersions(ctx.webUrl), text: "Review and confirm it", id: "open-profile" },
          },
          "match-status",
        ),
      ];
    case "no_current":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: "Choose which profile version to use.",
            lines: [
              `You have ${plural(profile.confirmed, "confirmed version")}, but none is set as current. Make one current in EasyApply.`,
            ],
            link: { href: webLinks.profileVersions(ctx.webUrl), text: "Open your profile versions", id: "open-profile" },
          },
          "match-status",
        ),
      ];
    case "error":
      return [noticeElement(doc, describeFailure(profile.failure, "check your profile", ctx), "match-status")];
    case "current":
      break;
  }

  const version = profile.version;
  const using = element(
    doc,
    "p",
    { class: "hint", id: "match-version" },
    `Using profile version ${version.version_number}, your current confirmed version.`,
  );
  switch (state.kind) {
    case "idle":
    case "loading":
      return [using, status(ctx, "Looking for your match…", "match-status")];
    case "none":
      return [
        using,
        element(doc, "p", { class: "lede", id: "match-status" }, `No match for profile version ${version.version_number} yet.`),
        button(ctx, "match-run", "Match my profile", ctx.actions.match, { primary: true, disabled: !canMatch(flow) }),
      ];
    case "matching":
      return [
        using,
        status(ctx, `Matching profile version ${version.version_number} against this job…`, "match-status"),
        button(ctx, "match-run", "Matching…", () => undefined, { primary: true, disabled: true }),
      ];
    case "error":
      return [
        using,
        noticeElement(
          doc,
          describeFailure(state.failure, state.during === "match" ? "match your profile" : "load your match", ctx),
          "match-status",
        ),
        canMatch(flow) ? button(ctx, "match-run", "Match my profile", ctx.actions.match, { primary: true }) : null,
      ];
    case "matched":
      return renderMatchDetail(ctx, flow, state.match, state.created);
  }
}

// --- the tailored resume ------------------------------------------------------------

function generatorLine(run: TailoringRunDetail): string {
  const version = run.profile_version_number ?? "?";
  return run.generator === "ai"
    ? `Written with AI${run.model ? ` (${run.model})` : ""} from profile version ${version}, then checked line by line against it.`
    : `Written by EasyApply's rule-based writer from profile version ${version}, using your own words — AI tailoring isn't enabled for your account.`;
}

function runLinks(ctx: JobViewContext, run: TailoringRunDetail, withDownload: boolean): HTMLElement {
  const { doc } = ctx;
  return element(
    doc,
    "p",
    { class: "links" },
    linkElement(doc, { href: webLinks.tailoredResume(ctx.webUrl, run.id), text: "Open in EasyApply", id: "open-tailored" }),
    withDownload ? " · " : null,
    withDownload
      ? linkElement(doc, { href: webLinks.artifact(ctx.webUrl, run.id), text: "Download (.docx)", id: "download-tailored" })
      : null,
  );
}

function renderTailoring(ctx: JobViewContext, flow: JobFlow, state: TailoringState): Child[] {
  const { doc } = ctx;
  const version = currentVersion(flow);
  if (flow.requirements.kind !== "ready" || !version) {
    return [
      element(doc, "p", { class: "hint", id: "tailoring-status" }, "Tailoring needs this job's requirements and a confirmed, current profile version."),
    ];
  }
  const start = (text: string) =>
    button(ctx, "tailor-run", text, ctx.actions.tailor, { primary: true, disabled: !canTailor(flow) });
  const fineprint = element(
    doc,
    "p",
    { class: "fineprint" },
    "EasyApply writes it on its own server from your confirmed profile and checks every line against it. Nothing is sent to the job site.",
  );

  switch (state.kind) {
    case "idle":
    case "loading":
      return [status(ctx, "Looking for a tailored resume…", "tailoring-status")];
    case "none":
      return [
        element(doc, "p", { class: "lede", id: "tailoring-status" }, `No tailored resume for profile version ${version.version_number} yet.`),
        start("Tailor my resume"),
        fineprint,
      ];
    case "starting":
      return [status(ctx, "Starting your tailored resume…", "tailoring-status"), button(ctx, "tailor-run", "Starting…", () => undefined, { primary: true, disabled: true })];
    case "running":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: `${TAILORING_STAGE_LABELS[state.run.status] ?? state.run.status}…`,
            lines: [
              state.run.status === "validating"
                ? "Checking every statement against your profile (grounding)."
                : `Tailoring your resume for this job (attempt ${state.run.attempt}).`,
              ...(state.run.is_stalled ? ["This is taking longer than usual."] : []),
            ],
          },
          "tailoring-status",
        ),
      ];
    case "still_running":
      return [
        noticeElement(
          doc,
          {
            tone: "info",
            title: "Still working on your resume.",
            lines: [
              `It is at "${TAILORING_STAGE_LABELS[state.run.status] ?? state.run.status}". Check again in a moment, or follow it in EasyApply.`,
            ],
          },
          "tailoring-status",
        ),
        button(ctx, "tailor-recheck", "Check again", ctx.actions.recheckTailoring),
        runLinks(ctx, state.run, false),
      ];
    case "ready": {
      const warnings = state.run.warning_count ?? 0;
      return [
        noticeElement(
          doc,
          {
            tone: "success",
            title: "Your tailored resume is ready.",
            lines: [
              generatorLine(state.run),
              warnings > 0
                ? `Every statement was traced back to your profile; ${plural(warnings, "warning")} to review in EasyApply.`
                : "Every statement was traced back to your profile.",
              "Review it before you use it — it is yours to accept or change.",
            ],
          },
          "tailoring-status",
        ),
        runLinks(ctx, state.run, true),
      ];
    }
    case "not_approved":
      return [
        noticeElement(
          doc,
          {
            tone: "error",
            title: "Not approved — no file.",
            lines: [
              "Some statements in it couldn't be traced back to your profile, so EasyApply won't offer it for download. " +
                "Its page in EasyApply lists which ones.",
            ],
          },
          "tailoring-status",
        ),
        runLinks(ctx, state.run, false),
        start("Generate again"),
      ];
    case "no_file":
      return [
        noticeElement(
          doc,
          {
            tone: "error",
            title: "This resume's file isn't available.",
            lines: ["It was approved, but EasyApply has no file for it. Generate it again to get a new one."],
          },
          "tailoring-status",
        ),
        runLinks(ctx, state.run, false),
        start("Generate again"),
      ];
    case "failed": {
      const code = state.run.error_code;
      return [
        noticeElement(
          doc,
          {
            tone: "error",
            title: tailoringFailureTitle(state.run),
            lines: [(code && TAILORING_FAILURE_HELP[code]) || `EasyApply couldn't generate this resume${code ? ` (${code})` : ""}.`],
          },
          "tailoring-status",
        ),
        runLinks(ctx, state.run, false),
        canTailor(flow) ? start("Try again") : null,
      ];
    }
    case "error":
      return [
        noticeElement(
          doc,
          describeFailure(state.failure, state.during === "start" ? "start your tailored resume" : "load your tailored resume", ctx),
          "tailoring-status",
        ),
        canTailor(flow) ? start("Tailor my resume") : null,
      ];
  }
}

// --- the panel ------------------------------------------------------------------------

function section(doc: Document, id: string, title: string, children: Child[]): HTMLElement {
  return element(doc, "section", { id, class: "panel-section", "aria-live": "polite" }, element(doc, "h3", {}, title), ...children);
}

export function renderJobPanel(root: HTMLElement, flow: JobFlow, ctx: JobViewContext): void {
  const { doc } = ctx;
  if (flow.session === "changed") {
    // Nothing of the account the panel was for: not its name, not a requirement, not a match.
    root.replaceChildren(noticeElement(doc, describeSessionChanged(), "panel-session-changed"));
    return;
  }
  if (flow.session === "expired") {
    root.replaceChildren(
      noticeElement(doc, describeFailure({ ok: false, kind: "session_required", status: 401, code: null, message: "" }, "continue", ctx), "session-expired"),
    );
    return;
  }
  root.replaceChildren(
    element(
      doc,
      "p",
      { class: "account", id: "panel-account" },
      "Signed in to EasyApply as ",
      element(doc, "strong", {}, flow.key.label),
      ".",
    ),
    section(doc, "requirements", "Requirements", renderRequirements(ctx, flow, flow.requirements)),
    section(doc, "match", "Your match", renderMatch(ctx, flow, flow.match)),
    section(doc, "tailoring", "Tailored resume", renderTailoring(ctx, flow, flow.tailoring)),
  );
}
