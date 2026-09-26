/**
 * The popup: read the page, show the reading for review, save on request.
 *
 *   loading ─▶ blocked   (unsupported page, not a posting, reload needed, failed)
 *           └▶ review ─▶ sending ─▶ saved | already saved | refused (try again)
 *                                     │
 *                                     ▼
 *              the job in EasyApply: requirements, match, tailored resume
 *              (./job-panel.ts, over the state machine in src/workflow/)
 *
 * Every value from the page reaches the screen through `textContent` or an
 * input's `value`, never as markup. A job posting is text written by a third
 * party for the purpose of being read by software like this one.
 *
 * Nothing is sent until the Save button is pressed, and what is sent is the
 * payload the "Exact request" panel is showing at that moment.
 *
 * The popup is for ONE account: the one the web app named when it opened. Its
 * tag goes with every request, and "already saved" means saved by that tag.
 * If the browser turns out to be signed in to a different account (the proxy's
 * 409 `session_changed`), everything on screen and behind it is thrown away —
 * the review, the job panel, the application form's proposals — and the popup
 * offers to start again as whoever is signed in now.
 */

import { SESSION_CHANGED_CODE, SESSION_REQUIRED_CODE } from "@easyapply/shared";

import { jobPageUrl, jobsEndpoint, type SubmitOutcome } from "../api/jobs";
import type { SessionState } from "../api/session";
import { FIELD_LIMITS } from "../contract";
import { findAdapter, supportedSites } from "../extraction/registry";
import type { ExtractedJob, ExtractionResult, ExtractionWarning } from "../extraction/types";
import {
  buildPayload,
  codePointLength,
  draftFrom,
  type EditableField,
  type ReviewDraft,
  type ReviewedPayload,
} from "../review/payload";
import type { SubmissionRecord } from "../shared/messages";
import {
  describeOutcome,
  describeSession,
  describeSessionChanged,
  describeSessionExpired,
  formatDate,
  type Notice,
} from "./describe";
import { element, noticeElement } from "./dom";
import type { FormDetection } from "../forms/types";
import { ApplicationPanel } from "./application-panel";
import { JobPanel } from "./job-panel";
import type { PopupPort, TargetTab } from "./port";

export interface PopupOptions {
  root: HTMLElement;
  port: PopupPort;
  webUrl: string;
  extensionId: string;
}

const FIELDS: { name: EditableField; label: string; multiline?: boolean; required?: boolean }[] = [
  { name: "job_title", label: "Job title", required: true },
  { name: "company_name", label: "Company" },
  { name: "location_raw", label: "Location" },
  { name: "description_text", label: "Description", multiline: true },
];

/** What the review screen calls the account, or null. Display only. */
function labelOf(session: SessionState): string | null {
  return session.kind === "signed_in" ? session.label : null;
}

/** The tag of the account the review screen names, or null when unknown. */
function tagOf(session: SessionState): string | null {
  return session.kind === "signed_in" ? session.sessionTag : null;
}

/**
 * Whether a remembered save is of the posting now on screen, by the account now
 * signed in — by its tag, never its label: two accounts can share an email. A
 * tag unknown on either side is not the same account. Another person in the
 * same browser has not saved it, whoever did.
 */
function sameSave(record: SubmissionRecord, job: ExtractedJob, session: SessionState): boolean {
  const tag = tagOf(session);
  if (tag === null || typeof record.sessionTag !== "string" || record.sessionTag !== tag) return false;
  return sameJob(record, job);
}

/** Whether a remembered save is of the posting now on screen. */
function sameJob(record: SubmissionRecord, job: ExtractedJob): boolean {
  if (record.externalJobId && job.external_job_id) return record.externalJobId === job.external_job_id;
  return record.sourceUrl === job.source_url;
}

export class Popup {
  private readonly doc: Document;
  private readonly pending = new Set<Promise<unknown>>();
  private tab: TargetTab | null = null;

  // Review state.
  private job: ExtractedJob | null = null;
  private draft: ReviewDraft | null = null;
  private reviewed: ReviewedPayload | null = null;
  private phase: "idle" | "sending" | "saved" = "idle";
  private session: SessionState = { kind: "unknown", message: "" };
  /** The saved job's requirements, match and resume, once there is a saved job. */
  private panel: JobPanel | null = null;
  /** The page's application form, when it has one. */
  private application: ApplicationPanel | null = null;
  /** Whether the screen is the "different account now" notice, until the next load. */
  private changed = false;
  /** Bumped whenever the screen is rebuilt: a late answer for an older screen is not drawn on this one. */
  private view = 0;

  constructor(private readonly options: PopupOptions) {
    this.doc = options.root.ownerDocument;
  }

  /** Resolves once nothing the popup started is still running. For tests. */
  async settled(): Promise<void> {
    while (this.pending.size > 0) await Promise.allSettled([...this.pending]);
    await this.panel?.settled();
    await this.application?.settled();
  }

  /** The job panel, when one is showing. For tests. */
  get jobPanel(): JobPanel | null {
    return this.panel;
  }

  private track<T>(work: Promise<T>): Promise<T> {
    this.pending.add(work);
    void work.finally(() => this.pending.delete(work)).catch(() => undefined);
    return work;
  }

  start(): Promise<void> {
    return this.track(this.load());
  }

  // --- reading the page ----------------------------------------------------

  private async load(): Promise<void> {
    this.changed = false;
    this.showLoading();
    this.tab = await this.options.port.targetTab();
    if (!this.tab) {
      this.showBlocked({ tone: "error", title: "No page to read.", lines: ["Open a job posting, then try again."] });
      return;
    }

    const result = await this.options.port.extract(this.tab.id);
    if (!result) {
      this.showBlocked(this.noContentScript(this.tab), true);
      return;
    }
    await this.showResult(result);
  }

  /**
   * Nothing answered in the tab. On a site no adapter reads, that is expected.
   * On a supported one there are two causes and no way to tell them apart from
   * here: the page has not finished loading (the content script runs once it
   * has), or it was already open when the extension was installed or updated
   * (Chrome only injects into pages loaded afterwards). Both are named.
   */
  private noContentScript(tab: TargetTab): Notice {
    let url: URL | null = null;
    try {
      url = tab.url ? new URL(tab.url) : null;
    } catch {
      url = null;
    }
    const adapter = url ? findAdapter(url) : null;
    if (adapter) {
      return {
        tone: "info",
        title: "Can't read this page yet.",
        lines: [
          `If this ${adapter.siteName} page is still loading, try again in a moment. ` +
            "If it was already open when EasyApply was installed or updated, reload the page first.",
        ],
      };
    }
    return {
      tone: "info",
      title: "Not a supported job page.",
      lines: [`EasyApply can read job postings on ${supportedSites()} job boards (job-boards.greenhouse.io, jobs.lever.co).`],
    };
  }

  private async showResult(result: ExtractionResult): Promise<void> {
    switch (result.status) {
      case "unsupported":
        this.showBlocked({ tone: "info", title: "Not a supported job page.", lines: [result.message] });
        return;
      case "application_page": {
        // Nothing to save here; the form can still be prepared.
        const tabId = this.tab?.id;
        const [session, form] = await Promise.all([
          this.options.port
            .session()
            .catch((): SessionState => ({ kind: "unknown", message: "The extension could not ask." })),
          tabId === undefined ? null : this.options.port.detectForm(tabId).catch(() => null),
        ]);
        this.session = session;
        const body = this.body();
        body.append(
          this.notice(
            {
              tone: "info",
              title: "This is a job's application form.",
              lines: [result.message],
              link: { href: result.postingUrl, text: "Open the posting to save it", id: "open-posting" },
            },
            "blocked",
          ),
          element(this.doc, "div", { id: "application-panel", class: "application-panel" }),
        );
        if (form?.status === "detected" && form.fields.length > 0) this.showApplicationPanel(form);
        return;
      }
      case "not_job_page":
        this.showBlocked({ tone: "info", title: "No single job here.", lines: [result.message] });
        return;
      case "failed":
        this.showBlocked({ tone: "error", title: "Couldn't read this job.", lines: [result.message] }, true);
        return;
      case "extracted": {
        const tabId = this.tab?.id;
        const [record, session, form] = await Promise.all([
          tabId === undefined ? null : this.options.port.lastSubmission(tabId).catch(() => null),
          this.options.port
            .session()
            .catch((): SessionState => ({ kind: "unknown", message: "The extension could not ask." })),
          tabId === undefined ? null : this.options.port.detectForm(tabId).catch(() => null),
        ]);
        this.session = session;
        this.showReview(
          result.site,
          result.job,
          result.warnings,
          record && sameSave(record, result.job, session) ? record : null,
        );
        if (form?.status === "detected" && form.fields.length > 0) this.showApplicationPanel(form);
      }
    }
  }

  // --- screens ---------------------------------------------------------------

  private body(): HTMLElement {
    const { root } = this.options;
    // A rebuilt screen is a new view: whatever the old panels were doing is not on it.
    this.view += 1;
    this.panel?.dispose();
    this.panel = null;
    this.application?.dispose();
    this.application = null;
    root.replaceChildren(
      element(this.doc, "header", { class: "bar" }, element(this.doc, "strong", {}, "EasyApply"), " This job"),
    );
    const body = element(this.doc, "div", { class: "body" });
    root.append(body);
    return body;
  }

  private showLoading(): void {
    this.body().append(element(this.doc, "p", { class: "lede", role: "status" }, "Reading this page…"));
  }

  private notice(notice: Notice, id?: string): HTMLElement {
    return noticeElement(this.doc, notice, id);
  }

  /**
   * Which account the job goes to. Shown beside the Save button, because it is
   * as much a part of what will be saved as any field above it.
   */
  private renderAccount(): void {
    const box = this.options.root.querySelector("#account");
    if (!box) return;
    if (this.session.kind === "signed_in") {
      const configured = this.session.mode === "development";
      box.replaceChildren(
        element(
          this.doc,
          "p",
          { class: "account", id: "account-email" },
          "Saving as ",
          element(this.doc, "strong", {}, this.session.label),
          configured ? " — the account this EasyApply web app is configured as." : ".",
        ),
      );
      return;
    }
    const notice = describeSession(this.session, this.options);
    box.replaceChildren(...(notice ? [this.notice(notice, "account-notice")] : []));
  }

  /**
   * The browser is signed in to a different account than the one this popup
   * was opened for. Everything the popup held for that account goes — the
   * review, the saved job's panel, the form's proposals — and the one thing
   * offered is to start again, which asks the web app who is signed in now.
   */
  private showSessionChanged(): void {
    if (this.changed) return;
    this.changed = true;
    this.job = null;
    this.draft = null;
    this.reviewed = null;
    this.phase = "idle";
    this.session = { kind: "unknown", message: "" };
    const body = this.body();
    const reload = element(this.doc, "button", { type: "button", class: "primary", id: "session-reload" }, "Reload");
    reload.addEventListener("click", () => void this.track(this.load()));
    body.append(this.notice(describeSessionChanged(), "session-changed"), reload);
  }

  private showBlocked(notice: Notice, canRetry = false): void {
    const body = this.body();
    body.append(this.notice(notice, "blocked"));
    if (canRetry) {
      const retry = element(this.doc, "button", { type: "button", class: "secondary" }, "Try again");
      retry.addEventListener("click", () => void this.track(this.load()));
      body.append(retry);
    }
  }

  private showReview(
    site: string,
    job: ExtractedJob,
    warnings: ExtractionWarning[],
    record: SubmissionRecord | null,
  ): void {
    const doc = this.doc;
    this.job = job;
    this.draft = draftFrom(job);
    this.phase = record ? "saved" : "idle";

    const body = this.body();
    body.append(
      element(
        doc,
        "p",
        { class: "lede" },
        `Read from ${site}. Check it and correct anything that's wrong — the exact request below is what EasyApply will store.`,
      ),
    );

    const form = element(doc, "form", { id: "review", novalidate: "" });
    for (const field of FIELDS) {
      const id = `field-${field.name}`;
      const warning = warnings.find((item) => item.field === field.name);
      const control = field.multiline
        ? element(doc, "textarea", { id, name: field.name, rows: "9", spellcheck: "false" })
        : element(doc, "input", { id, name: field.name, type: "text", autocomplete: "off" });
      control.value = this.draft[field.name];
      if (field.required) control.setAttribute("aria-required", "true");
      control.addEventListener("input", () => {
        if (!this.draft) return;
        this.draft[field.name] = control.value;
        this.refresh();
      });

      form.append(
        element(
          doc,
          "div",
          { class: "field" },
          element(doc, "label", { for: id }, field.label, field.required ? element(doc, "span", { class: "req" }, " *") : null),
          control,
          warning ? element(doc, "p", { class: "hint hint--warning", id: `${id}-note` }, warning.message) : null,
          field.name === "description_text" ? element(doc, "p", { class: "hint", id: "description-count" }) : null,
        ),
      );
      if (warning) control.setAttribute("aria-describedby", `${id}-note`);
    }

    form.append(
      element(
        doc,
        "dl",
        { class: "identity" },
        element(doc, "dt", {}, "Source"),
        element(doc, "dd", { id: "identity-source" }, job.source_type),
        element(doc, "dt", {}, `Job id on ${site}`),
        element(doc, "dd", { id: "identity-external-id" }, job.external_job_id ?? "none"),
        element(doc, "dt", {}, "Page"),
        element(doc, "dd", { id: "identity-url", class: "url" }, job.source_url),
      ),
      element(doc, "ul", { class: "issues", id: "issues" }),
      element(
        doc,
        "details",
        { class: "exact", id: "exact" },
        element(doc, "summary", {}, "Exact request"),
        element(doc, "p", { class: "hint" }, `POST ${jobsEndpoint(this.options.webUrl)}`),
        element(doc, "pre", { id: "exact-body" }),
      ),
      element(doc, "div", { id: "outcome" }),
      element(doc, "div", { id: "account" }),
      element(doc, "button", { type: "submit", id: "save", class: "primary" }, "Save to EasyApply"),
      element(
        doc,
        "p",
        { class: "fineprint" },
        "Saving adds this job to your EasyApply jobs. It does not apply for the job or fill in anything on the page.",
      ),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.track(this.save());
    });
    body.append(
      form,
      // Filled once there is a saved job (./job-panel.ts), and once the page's
      // application form has been read (./application-panel.ts). In this order
      // on screen whichever arrives first.
      element(doc, "div", { id: "job-panel", class: "job-panel", hidden: "" }),
      element(doc, "div", { id: "application-panel", class: "application-panel" }),
    );

    this.renderAccount();
    this.refresh();
    if (record) {
      this.showOutcomeNotice(
        {
          tone: "success",
          title: record.outcome === "created" ? "Saved to EasyApply." : "Already in EasyApply.",
          lines: [
            `Saved ${formatDate(record.savedAt)}, from this tab. The fields below are the page as it reads now; ` +
              "open the job to see what EasyApply stored.",
          ],
        },
        record.jobId,
      );
      this.lockForm();
      this.showJobPanel(record.jobId);
    }
  }

  /** Recompute the payload from the draft and redraw what depends on it. */
  private refresh(): void {
    if (!this.job || !this.draft) return;
    this.reviewed = buildPayload(this.job, this.draft);
    const { payload, issues, sendable } = this.reviewed;
    const root = this.options.root;

    const exact = root.querySelector<HTMLPreElement>("#exact-body");
    if (exact) exact.textContent = JSON.stringify(payload, null, 2);

    const count = root.querySelector<HTMLElement>("#description-count");
    if (count) {
      const length = codePointLength(payload.description_text ?? "");
      count.textContent = `${length.toLocaleString("en-US")} of ${FIELD_LIMITS.description_text.toLocaleString("en-US")} characters`;
    }

    const list = root.querySelector<HTMLUListElement>("#issues");
    list?.replaceChildren(
      ...issues.map((issue) =>
        element(this.doc, "li", { class: `issue issue--${issue.severity}`, "data-field": issue.field }, issue.message),
      ),
    );

    for (const field of FIELDS) {
      const control = root.querySelector(`#field-${field.name}`);
      const invalid = issues.some((issue) => issue.field === field.name && issue.severity === "error");
      control?.setAttribute("aria-invalid", invalid ? "true" : "false");
    }

    const save = root.querySelector<HTMLButtonElement>("#save");
    // Signed out, a save can only be refused; the account notice says how to fix it.
    if (save) save.disabled = !sendable || this.phase !== "idle" || this.session.kind === "signed_out";
  }

  // --- saving ------------------------------------------------------------------

  private async save(): Promise<void> {
    if (!this.tab || !this.reviewed?.sendable || this.phase !== "idle" || this.session.kind === "signed_out") return;
    // Fixed at the moment of the click: an edit made while the request is in
    // flight cannot change what is being sent, and is not what was reviewed.
    // So is the account: the save carries the tag of the one named on screen.
    const payload = this.reviewed.payload;
    const sessionAtSave = this.session;
    const view = this.view;

    this.phase = "sending";
    const save = this.options.root.querySelector<HTMLButtonElement>("#save");
    if (save) {
      save.disabled = true;
      save.textContent = "Saving…";
    }
    this.options.root.querySelector("#outcome")?.replaceChildren();

    let outcome: SubmitOutcome;
    try {
      outcome = await this.options.port.submit(this.tab.id, payload, labelOf(sessionAtSave), tagOf(sessionAtSave));
    } catch {
      outcome = { kind: "unreachable", code: "network", message: "The extension could not send the job." };
    }

    // The screen the save was pressed on is gone (the account changed, and
    // perhaps a new screen is up for the next one): nothing to draw on it.
    if (view !== this.view) return;
    if (outcome.kind === "rejected" && outcome.code === SESSION_CHANGED_CODE) {
      // Nothing was saved, for either account.
      this.showSessionChanged();
      return;
    }

    const notice =
      outcome.kind === "rejected" && outcome.code === SESSION_REQUIRED_CODE && sessionAtSave.kind === "signed_in"
        ? describeSessionExpired("save this job", this.options)
        : describeOutcome(outcome, payload, this.options);
    if (outcome.kind === "created" || outcome.kind === "existing") {
      this.phase = "saved";
      this.showOutcomeNotice(notice, outcome.job.id);
      this.lockForm();
      this.showJobPanel(outcome.job.id);
    } else {
      this.phase = "idle";
      if (outcome.kind === "rejected" && outcome.code === SESSION_REQUIRED_CODE) {
        // The session ended between opening the popup and saving. Retrying from
        // here cannot help until the person signs in again.
        this.session = { kind: "signed_out" };
        this.renderAccount();
      }
      this.showOutcomeNotice(notice, null);
      if (save) save.textContent = "Try again";
      this.refresh();
    }
  }

  private showOutcomeNotice(notice: Notice, jobId: number | null): void {
    const box = this.options.root.querySelector("#outcome");
    if (!box) return;
    const link =
      jobId === null
        ? notice.link
        : { href: jobPageUrl(this.options.webUrl, jobId), text: "Open in EasyApply", id: "open-job" };
    box.replaceChildren(this.notice({ ...notice, link }, "outcome-notice"));
  }

  /**
   * What EasyApply knows about the saved job, below the form.
   *
   * Keyed by the account the web app named (its tag), this tab and the job:
   * the panel drops any answer that arrives for a different key. Without a
   * known account — the web app could not be asked who this is, or gave no tag
   * for it — nothing is loaded: the account is part of what the panel shows.
   */
  private showJobPanel(jobId: number): void {
    if (!this.tab) return;
    const container = this.options.root.querySelector<HTMLElement>("#job-panel");
    if (!container) return;
    this.panel?.dispose();
    container.hidden = false;
    if (this.session.kind !== "signed_in" || this.session.sessionTag === null) {
      container.append(
        this.notice(
          {
            tone: "info",
            title: "Open the job in EasyApply to see its requirements and your match.",
            lines: ["The extension couldn't check which account this browser is signed in as, so it isn't loading anything more here."],
          },
          "panel-unavailable",
        ),
      );
      this.panel = null;
      return;
    }
    this.panel = new JobPanel({
      container,
      port: this.options.port,
      webUrl: this.options.webUrl,
      extensionId: this.options.extensionId,
      key: { sessionTag: this.session.sessionTag, label: this.session.label, tabId: this.tab.id, jobId },
      onSessionExpired: () => {
        this.session = { kind: "signed_out" };
        this.renderAccount();
      },
      onSessionChanged: () => this.showSessionChanged(),
    });
    void this.track(this.panel.start());
  }

  /**
   * The page's application form, when it has one: what EasyApply could fill
   * from the profile and what it will not. Available whether or not the job is
   * saved; nothing is read from the profile until the person asks.
   */
  private showApplicationPanel(detection: Extract<FormDetection, { status: "detected" }>): void {
    const container = this.options.root.querySelector<HTMLElement>("#application-panel");
    if (!container || !this.tab) return;
    const application = new ApplicationPanel({
      container,
      port: this.options.port,
      webUrl: this.options.webUrl,
      extensionId: this.options.extensionId,
      tabId: this.tab.id,
      account: labelOf(this.session),
      sessionUnknown: this.session.kind === "unknown",
      sessionTag: tagOf(this.session),
      detection,
      tailoredResume: () => {
        const tailoring = this.panel?.state.tailoring;
        return tailoring?.kind === "ready" ? tailoring.run.id : null;
      },
      onSessionChanged: () => this.showSessionChanged(),
      onSessionExpired: () => {
        this.session = { kind: "signed_out" };
        this.renderAccount();
        this.refresh();
      },
    });
    this.application = application;
    application.start();
  }

  /** After a save, the form is a record of what was sent, not a draft. */
  private lockForm(): void {
    const root = this.options.root;
    const form = root.querySelector<HTMLFormElement>("#review");
    if (form && !form.classList.contains("review--saved")) {
      // Collapsed so the job's requirements and match are what the eye lands
      // on; one click shows exactly what was saved again.
      form.classList.add("review--saved");
      const toggle = element(this.doc, "button", { type: "button", class: "link-button", id: "toggle-saved" }, "Show what was saved");
      toggle.addEventListener("click", () => {
        const collapsed = form.classList.toggle("review--saved");
        toggle.textContent = collapsed ? "Show what was saved" : "Hide what was saved";
      });
      form.prepend(toggle);
    }
    root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("#review input, #review textarea").forEach((control) => {
      control.readOnly = true;
    });
    const save = root.querySelector<HTMLButtonElement>("#save");
    if (save) {
      save.disabled = true;
      save.textContent = "Saved";
    }
  }
}

export function startPopup(options: PopupOptions): Popup {
  const popup = new Popup(options);
  void popup.start();
  return popup;
}
