/**
 * The application form on the page: what EasyApply could fill from the
 * person's profile, what it will not, and — only when they press the button —
 * filling the fields they ticked.
 *
 *   found ─▶ loading ─▶ review ─▶ filling ─▶ filled
 *              └▶ error    (no confirmed profile, signed out, unreachable)
 *
 * Nothing about the form leaves the extension, and nothing from the profile
 * reaches the page until the person approves it field by field. The profile is
 * read only when they ask ("Prepare answers"), from the current confirmed
 * version; the popup keeps the proposals it made from it and nothing else, and
 * sends the page only the approved values.
 *
 * Filling is not applying. The panel says so before and after, never clicks
 * the page's submit button, and never reports the application as sent.
 *
 * The proposals are ONE account's profile. The profile is read with that
 * account's tag (the proxy refuses the read once the browser is signed in to
 * someone else), and just before filling the panel asks the web app again who
 * is signed in: only the same account's values ever reach the page. Any other
 * answer throws the proposals away.
 */

import { SESSION_REQUIRED_CODE } from "@easyapply/shared";

import type { ApiFailure } from "../api/client";
import { webLinks } from "../api/links";
import type { SessionState } from "../api/session";
import type { FieldDescriptor, FillReport, FillSkipReason, FormDetection } from "../forms/types";
import { proposeAll, type Proposal } from "../profile/mapping";
import { describeFailure } from "./describe";
import { element, linkElement, noticeElement, type Child } from "./dom";
import type { PopupPort } from "./port";

export interface ApplicationPanelOptions {
  container: HTMLElement;
  port: PopupPort;
  webUrl: string;
  extensionId: string;
  tabId: number;
  /** What the web app called the signed-in account; null when signed out or unknown. Display only. */
  account: string | null;
  /** True when the popup could not find out whether anyone is signed in (the web app did not answer). */
  sessionUnknown?: boolean;
  /** That account's tag, sent with the profile read; null when the web app gave none. */
  sessionTag: string | null;
  detection: Extract<FormDetection, { status: "detected" }>;
  /** The tailored resume ready for this job, when the job panel has one. */
  tailoredResume: () => number | null;
  /** Told when the browser turns out to be signed in to a different account now. */
  onSessionChanged?: () => void;
  /** Told when the session turns out to have ended. */
  onSessionExpired?: () => void;
}

type ApplicationState =
  | { kind: "found" }
  | { kind: "loading" }
  /** `during`: reading the profile, or the account check just before filling. */
  | { kind: "error"; failure: ApiFailure; during: "prepare" | "fill" }
  | { kind: "review"; versionNumber: number; proposals: Proposal[]; picked: Set<string> }
  | { kind: "filling"; versionNumber: number; proposals: Proposal[]; picked: Set<string> }
  | { kind: "filled"; versionNumber: number; proposals: Proposal[]; report: FillReport | null };

const SKIP_REASONS: Record<FillSkipReason, string> = {
  gone: "is no longer on the page",
  changed: "changed since you reviewed it, so it was left alone",
  not_settable: "can't be filled from here",
  has_value: "already had something in it, so it was left as it was",
  sensitive: "reads as a question EasyApply never answers for you",
  too_long: "allows fewer characters than the value has; nothing was cut",
};

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

export class ApplicationPanel {
  private state: ApplicationState = { kind: "found" };
  private readonly pending = new Set<Promise<unknown>>();
  /** Set once the panel is gone: nothing more is drawn, and nothing is filled. */
  private disposed = false;

  constructor(private readonly options: ApplicationPanelOptions) {}

  start(): void {
    this.render();
  }

  /** The popup is showing something else: forget the proposals, and fill nothing. */
  dispose(): void {
    this.disposed = true;
    this.state = { kind: "found" };
  }

  async settled(): Promise<void> {
    while (this.pending.size > 0) await Promise.allSettled([...this.pending]);
  }

  private track<T>(work: Promise<T>): Promise<T> {
    this.pending.add(work);
    void work.finally(() => this.pending.delete(work)).catch(() => undefined);
    return work;
  }

  private get doc(): Document {
    return this.options.container.ownerDocument;
  }

  private set(state: ApplicationState): void {
    if (this.disposed) return;
    this.state = state;
    this.render();
  }

  /** A failure that is about the session, not the profile: discard everything, and tell the popup. */
  private sessionEnded(failure: ApiFailure, during: "prepare" | "fill"): boolean {
    if (failure.kind === "session_changed") {
      // Whatever was proposed belonged to the account signed in before.
      this.set({ kind: "found" });
      this.options.onSessionChanged?.();
      return true;
    }
    if (failure.kind === "session_required") {
      this.set({ kind: "error", failure, during });
      this.options.onSessionExpired?.();
      return true;
    }
    return false;
  }

  // --- actions -------------------------------------------------------------------------

  private async prepare(): Promise<void> {
    this.set({ kind: "loading" });
    const result = await this.options.port.api({ op: "currentProfileVersion" }, this.options.sessionTag);
    if (this.disposed) return;
    if (!result.ok) {
      if (!this.sessionEnded(result, "prepare")) this.set({ kind: "error", failure: result, during: "prepare" });
      return;
    }
    // The snapshot is used here and not kept: only the proposals made from it are.
    const proposals = proposeAll(this.options.detection.fields, result.data.snapshot);
    this.set({
      kind: "review",
      versionNumber: result.data.version_number,
      proposals,
      picked: new Set(proposals.filter((proposal) => proposal.action === "fill" && proposal.preselected).map((proposal) => proposal.field.key)),
    });
  }

  private async fill(): Promise<void> {
    if (this.state.kind !== "review") return;
    const { versionNumber, proposals, picked } = this.state;
    const instructions = proposals
      .filter((proposal) => proposal.action === "fill" && proposal.value !== null && picked.has(proposal.field.key))
      .map((proposal) => ({ key: proposal.field.key, value: proposal.value as string, label: proposal.field.label }));
    if (instructions.length === 0) return;
    this.set({ kind: "filling", versionNumber, proposals, picked });
    // Still the same account? Filling is local — no request would be refused —
    // so ask the web app, and put these values on the page only if it is.
    const failure = await this.accountCheck();
    if (this.disposed) return;
    if (failure) {
      if (!this.sessionEnded(failure, "fill")) this.set({ kind: "error", failure, during: "fill" });
      return;
    }
    const report = await this.options.port.fillForm(this.options.tabId, instructions);
    this.set({ kind: "filled", versionNumber, proposals, report });
  }

  /** Null when the browser is still signed in to the account the proposals were made for; else why not. */
  private async accountCheck(): Promise<ApiFailure | null> {
    const now = await this.options.port
      .session()
      .catch((): SessionState => ({ kind: "unknown", message: "The extension could not ask who is signed in." }));
    switch (now.kind) {
      case "signed_in":
        if (now.sessionTag !== null && now.sessionTag === this.options.sessionTag) return null;
        return { ok: false, kind: "session_changed", status: null, code: null, message: "A different account is signed in." };
      case "signed_out":
        return { ok: false, kind: "session_required", status: null, code: SESSION_REQUIRED_CODE, message: "Signed out." };
      case "unknown":
        return { ok: false, kind: "network", status: null, code: null, message: now.message };
    }
  }

  // --- rendering -------------------------------------------------------------------------

  private render(): void {
    const { doc } = this;
    const { detection } = this.options;
    this.options.container.replaceChildren(
      element(
        doc,
        "section",
        { class: "panel-section", id: "application", "aria-live": "polite" },
        element(doc, "h3", {}, "Application form"),
        ...this.body(detection.fields),
      ),
    );
  }

  private body(fields: FieldDescriptor[]): Child[] {
    const { doc } = this;
    const state = this.state;
    const intro = element(
      doc,
      "p",
      { class: "lede", id: "application-status" },
      `EasyApply found this page's ${this.options.detection.site} application form: ${plural(fields.length, "field")}.`,
    );
    const neverSubmits = element(
      doc,
      "p",
      { class: "fineprint" },
      "EasyApply fills only the fields you tick, only if they are empty, and never submits the application. You review the form and submit it yourself.",
    );

    if (this.options.account === null && this.options.sessionUnknown) {
      return [
        intro,
        noticeElement(
          doc,
          {
            tone: "info",
            title: "EasyApply couldn't check who is signed in.",
            lines: ["Nothing is read from your profile or put on this page until it can. Open EasyApply again in a moment."],
          },
          "application-session-unknown",
        ),
      ];
    }

    if (this.options.account === null) {
      return [
        intro,
        noticeElement(
          doc,
          {
            tone: "info",
            title: "Sign in to EasyApply to prepare answers from your profile.",
            lines: ["Nothing is read from your profile or put on this page until you do."],
          },
          "application-signin",
        ),
      ];
    }
    if (this.options.sessionTag === null) {
      // Signed in, but the web app gave no account tag: the extension could not
      // tell whose profile it would read, or notice if that changed.
      return [
        intro,
        noticeElement(
          doc,
          {
            tone: "info",
            title: "Couldn't check which account this browser is signed in as.",
            lines: ["So EasyApply isn't reading a profile or putting anything on this page. Prepare the form in EasyApply's web app instead."],
          },
          "application-unverified",
        ),
      ];
    }

    switch (state.kind) {
      case "found":
        return [intro, this.prepareButton("Prepare answers from my profile"), neverSubmits];
      case "loading":
        return [intro, element(doc, "p", { class: "lede", role: "status" }, "Reading your current confirmed profile…")];
      case "error":
        return [
          intro,
          state.failure.kind === "not_found" && state.during === "prepare"
            ? noticeElement(
                doc,
                {
                  tone: "info",
                  title: "You don't have a confirmed, current profile version yet.",
                  lines: ["EasyApply only proposes answers from a profile version you have confirmed."],
                  link: { href: webLinks.profileVersions(this.options.webUrl), text: "Open your profile in EasyApply", id: "application-open-profile" },
                },
                "application-error",
              )
            : noticeElement(
                doc,
                describeFailure(state.failure, state.during === "fill" ? "fill the form" : "read your profile", this.options),
                "application-error",
              ),
          this.prepareButton("Try again"),
        ];
      case "review":
      case "filling":
        return [
          element(
            doc,
            "p",
            { class: "lede", id: "application-status" },
            `Proposed from your profile version ${state.versionNumber}. Tick what you want filled; nothing is filled until you press the button.`,
          ),
          ...this.groups(state.proposals, state.picked, state.kind === "filling"),
          this.fillButton(state.proposals, state.picked, state.kind === "filling"),
          neverSubmits,
        ];
      case "filled":
        return [...this.report(state.proposals, state.report), this.prepareButton("Review the form again")];
    }
  }

  private prepareButton(text: string): HTMLButtonElement {
    const button = element(this.doc, "button", { type: "button", class: "secondary", id: "application-prepare" }, text);
    button.addEventListener("click", () => {
      button.disabled = true;
      void this.track(this.prepare());
    });
    return button;
  }

  private fillButton(proposals: Proposal[], picked: Set<string>, busy: boolean): HTMLButtonElement {
    const count = proposals.filter((proposal) => proposal.action === "fill" && picked.has(proposal.field.key)).length;
    const button = element(
      this.doc,
      "button",
      { type: "button", class: "primary", id: "application-fill" },
      busy ? "Filling…" : count === 0 ? "Tick a field to fill it" : `Fill ${plural(count, "selected field")}`,
    );
    button.disabled = busy || count === 0;
    button.addEventListener("click", () => {
      button.disabled = true;
      void this.track(this.fill());
    });
    return button;
  }

  private groups(proposals: Proposal[], picked: Set<string>, busy: boolean): Child[] {
    const { doc } = this;
    const ready = proposals.filter((proposal) => proposal.action === "fill" && proposal.preselected);
    const check = proposals.filter((proposal) => proposal.action === "fill" && !proposal.preselected);
    const review = proposals.filter((proposal) => proposal.action === "review");
    const attach = proposals.filter((proposal) => proposal.action === "attach");
    const leave = proposals.filter((proposal) => proposal.action === "leave");

    const group = (id: string, title: string, items: Proposal[], row: (proposal: Proposal) => HTMLElement) =>
      items.length === 0
        ? null
        : element(
            doc,
            "div",
            { class: "group", id },
            element(doc, "h4", {}, `${title} (${items.length})`),
            element(doc, "ul", { class: "proposals" }, ...items.map(row)),
          );

    return [
      group("application-ready", "Ready to fill", ready, (proposal) => this.fillRow(proposal, picked, busy)),
      group("application-check", "Check before filling", check, (proposal) => this.fillRow(proposal, picked, busy)),
      group("application-review", "Needs your answer", review, (proposal) => this.infoRow(proposal)),
      group("application-attach", "Attach yourself", attach, (proposal) => this.attachRow(proposal)),
      group("application-leave", "Already filled in", leave, (proposal) => this.infoRow(proposal)),
    ];
  }

  private fillRow(proposal: Proposal, picked: Set<string>, busy: boolean): HTMLElement {
    const { doc } = this;
    const key = proposal.field.key;
    const box = element(doc, "input", { type: "checkbox", "data-key": key, "aria-label": `Fill ${proposal.field.label}` });
    box.checked = picked.has(key);
    box.disabled = busy;
    box.addEventListener("change", () => {
      if (this.state.kind !== "review") return;
      if (box.checked) this.state.picked.add(key);
      else this.state.picked.delete(key);
      this.render();
    });
    return element(
      doc,
      "li",
      { class: "proposal", "data-key": key, "data-confidence": proposal.confidence },
      element(
        doc,
        "label",
        { class: "proposal__head" },
        box,
        " ",
        element(doc, "span", { class: "proposal__label" }, proposal.field.label),
        " ",
        element(
          doc,
          "span",
          { class: `tag confidence confidence--${proposal.confidence}` },
          proposal.confidence === "exact" ? "Exact profile value" : "Adapted from your profile — check it",
        ),
      ),
      element(doc, "p", { class: "proposal__value" }, proposal.value ?? ""),
      element(doc, "p", { class: "hint" }, proposal.note),
    );
  }

  private infoRow(proposal: Proposal): HTMLElement {
    const { doc } = this;
    return element(
      doc,
      "li",
      {
        class: "proposal proposal--review",
        "data-key": proposal.field.key,
        "data-sensitive": proposal.classification.sensitive ?? undefined,
      },
      element(
        doc,
        "p",
        { class: "proposal__head" },
        element(doc, "span", { class: "proposal__label" }, proposal.field.label),
        proposal.field.required ? element(doc, "span", { class: "req" }, " *") : null,
        " ",
        element(doc, "span", { class: "tag" }, proposal.action === "leave" ? "Left as it is" : "Review required"),
      ),
      element(doc, "p", { class: "hint" }, proposal.note),
      proposal.hint ? element(doc, "p", { class: "hint proposal__hint" }, `Your profile says: ${proposal.hint}`) : null,
    );
  }

  private attachRow(proposal: Proposal): HTMLElement {
    const { doc } = this;
    const runId = proposal.classification.identity === "resume" ? this.options.tailoredResume() : null;
    return element(
      doc,
      "li",
      { class: "proposal proposal--attach", "data-key": proposal.field.key },
      element(
        doc,
        "p",
        { class: "proposal__head" },
        element(doc, "span", { class: "proposal__label" }, proposal.field.label),
        proposal.field.required ? element(doc, "span", { class: "req" }, " *") : null,
      ),
      element(doc, "p", { class: "hint" }, proposal.note),
      runId !== null
        ? element(
            doc,
            "p",
            {},
            linkElement(doc, {
              href: webLinks.artifact(this.options.webUrl, runId),
              text: "Download your tailored resume for this job",
              id: "application-tailored-download",
            }),
          )
        : null,
    );
  }

  private report(proposals: Proposal[], report: FillReport | null): Child[] {
    const { doc } = this;
    if (report === null) {
      return [
        noticeElement(
          doc,
          {
            tone: "error",
            title: "The page didn't answer, so nothing was filled.",
            lines: ["It may have been reloaded or navigated away. Open EasyApply on it again to review the form."],
          },
          "application-report",
        ),
      ];
    }
    const labelOf = (key: string) => proposals.find((proposal) => proposal.field.key === key)?.field.label ?? key;
    const review = proposals.filter((proposal) => proposal.action === "review" && proposal.field.required).length;
    return [
      noticeElement(
        doc,
        {
          tone: report.filled.length > 0 ? "success" : "info",
          title: report.filled.length > 0 ? `Filled ${plural(report.filled.length, "field")} on the page.` : "Nothing was filled.",
          lines: [
            "Review the whole form on the page" +
              (review > 0 ? `, answer the ${plural(review, "required question")} marked for you,` : "") +
              " and submit it yourself when you're ready.",
            "EasyApply hasn't submitted anything, and won't.",
          ],
        },
        "application-report",
      ),
      report.filled.length > 0
        ? element(doc, "ul", { class: "filled", id: "application-filled" }, ...report.filled.map((key) => element(doc, "li", {}, labelOf(key))))
        : null,
      report.skipped.length > 0
        ? element(
            doc,
            "ul",
            { class: "issues", id: "application-skipped" },
            ...report.skipped.map((skip) =>
              element(doc, "li", { class: "issue issue--warning", "data-reason": skip.reason }, `${labelOf(skip.key)} ${SKIP_REASONS[skip.reason]}.`),
            ),
          )
        : null,
    ];
  }
}
