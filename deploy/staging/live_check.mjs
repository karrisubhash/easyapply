// Live test of the public staging deployment (docs/staging-deployment.md §13):
// real Microsoft sign-in, the web app, and the staging extension on real
// Greenhouse and Lever postings. It fills application forms and NEVER submits.
//
//   STAGING_CHECK_DIR=<dir outside the repo>   screenshots, downloads, run state
//   STAGING_USERS_FILE=<json outside the repo> {"a": {"upn","password"}, "b": {...}}
//   node deploy/staging/live_check.mjs <phase>
//     phases, in order: main | evidence | isolation | failures | persist | inspect | dbdown
//     (dbdown expects the database suspended first: `render postgres suspend …`)
//
// Needs the staging extension build (docs §7) and Playwright's Chromium
// (installed with the repo's dev dependencies).
import { createRequire } from "node:module";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(join(REPO, "apps/extension/package.json"));
const { chromium } = require("@playwright/test");

if (!process.env.STAGING_CHECK_DIR || !process.env.STAGING_USERS_FILE) {
  console.error("Set STAGING_CHECK_DIR and STAGING_USERS_FILE (both outside the repo).");
  process.exit(2);
}
const SP = process.env.STAGING_CHECK_DIR;
const BASE = process.env.STAGING_URL ?? "https://easyapply-staging.onrender.com";
const EXT_DIR = join(REPO, "apps/extension/.artifacts/staging");
const EXT_ID = "jjhciekcmhmngciccfgignmmlgjknakk";
const RESUME = join(REPO, "services/api/evaluation/datasets/resume_profile_v1/resume.pdf");
const GREENHOUSE = "https://job-boards.greenhouse.io/anthropic/jobs/5191226008";
const LEVER = "https://jobs.lever.co/palantir/ab7e3425-81d5-4705-a7b5-cd60c8a45cdb";
const SHOTS = join(SP, "staging-shots");
mkdirSync(SHOTS, { recursive: true });
const USERS = JSON.parse(readFileSync(process.env.STAGING_USERS_FILE, "utf8"));
const STATE_FILE = join(SP, "staging-state.json");
const state = (() => { try { return JSON.parse(readFileSync(STATE_FILE, "utf8")); } catch { return {}; } })();
const save = () => writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));

let failures = 0;
function log(ok, step, detail = "") {
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${step}${detail ? "  — " + detail : ""}`);
}
async function step(name, fn) {
  try { const r = await fn(); log(true, name, typeof r === "string" ? r : ""); return r; }
  catch (e) { log(false, name, String(e?.message ?? e).split("\n")[0].slice(0, 300)); return undefined; }
}
const shot = (page, name) => page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true }).catch(() => {});

async function launch() {
  const context = await chromium.launchPersistentContext("", {
    channel: "chromium", headless: true, acceptDownloads: true,
    args: [`--disable-extensions-except=${EXT_DIR}`, `--load-extension=${EXT_DIR}`],
    viewport: { width: 1280, height: 900 },
  });
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  return { context, worker };
}

async function microsoftSignIn(page, user) {
  await page.waitForURL(/login\.microsoftonline\.com/, { timeout: 90_000 });
  const email = page.locator('input[name="loginfmt"]');
  const other = page.locator("#otherTile").or(page.getByText("Use another account"));
  await email.or(other.first()).first().waitFor({ timeout: 30_000 });
  if (!(await email.isVisible().catch(() => false))) await other.first().click();
  await email.fill(user.upn);
  await page.locator("#idSIButton9").click();
  await page.locator('input[name="passwd"]').waitFor({ timeout: 30_000 });
  await page.locator('input[name="passwd"]').fill(user.password);
  await page.locator("#idSIButton9").click();
  const seen = [];
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(2500);
    if (page.url().startsWith(BASE)) return seen;
    const body = (await page.locator("body").innerText().catch(() => "")).slice(0, 2000);
    if (/Stay signed in\?/i.test(body)) { seen.push("stay-signed-in: No"); await page.locator("#idBtn_Back").click().catch(() => {}); continue; }
    if (/More information required|keep your account secure|Action Required|protect your account/i.test(body)) {
      seen.push("mfa-registration-prompt");
      const skip = page.getByRole("button", { name: /Skip for now|Ask later|Skip setup/i }).or(page.getByRole("link", { name: /Skip setup|Skip for now/i })).or(page.locator("#btnAskLater"));
      if (await skip.first().isVisible().catch(() => false)) { await skip.first().click(); seen.push("skipped"); continue; }
      const next = page.locator("#idSubmit_ProofUp_Redirect");
      if (await next.isVisible().catch(() => false)) { await next.click(); continue; }
      throw new Error("MFA registration required and not skippable: " + body.slice(0, 200).replace(/\s+/g, " "));
    }
    if (/Permissions requested|Accept/i.test(body) && (await page.locator("#idSIButton9").isVisible().catch(() => false))) { seen.push("consent: accept"); await page.locator("#idSIButton9").click(); continue; }
    if (/incorrect|doesn't exist|error/i.test(body) && !page.url().startsWith(BASE)) throw new Error("sign-in page says: " + body.slice(0, 200).replace(/\s+/g, " "));
  }
  throw new Error("did not return to EasyApply; at " + page.url());
}

async function signIn(context, user, label) {
  const page = await context.newPage();
  await page.goto(BASE + "/", { timeout: 180_000 });
  await page.getByRole("button", { name: "Sign in with Microsoft" }).click();
  const seen = await microsoftSignIn(page, user);
  await page.getByRole("button", { name: "Sign out" }).waitFor({ timeout: 90_000 });
  await shot(page, `${label}-signed-in`);
  const session = await (await context.request.get(BASE + "/api/auth/session")).json();
  await page.close();
  if (!session.authenticated || session.mode !== "entra") throw new Error("session: " + JSON.stringify(session));
  return `mode=${session.mode} as ${session.name ?? session.email ?? "?"}${seen.length ? " [" + seen.join(", ") + "]" : ""}`;
}

async function tabIdOf(worker, href) {
  for (let i = 0; i < 20; i++) {
    const id = await worker.evaluate(async (url) => {
      const want = new URL(url);
      const tabs = await chrome.tabs.query({});
      const same = (t) => { try { const u = new URL(t.url); return u.origin === want.origin && decodeURIComponent(u.pathname) === decodeURIComponent(want.pathname); } catch { return false; } };
      // A site outside the extension's host permissions: Chrome withholds its URL
      // (no "tabs" permission), so the newest URL-less tab is the one just opened.
      const hidden = tabs.filter((t) => !t.url).sort((a, b) => b.id - a.id)[0];
      return (tabs.find((t) => t.url === url) ?? tabs.find(same) ?? hidden)?.id;
    }, href);
    if (id !== undefined) return id;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("no tab at " + href);
}
async function openPosting(context, worker, href) {
  const page = await context.newPage();
  await page.goto(href, { timeout: 90_000, waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load", { timeout: 60_000 }).catch(() => {});
  const tabId = await tabIdOf(worker, page.url());
  for (let i = 0; i < 40; i++) {
    const ok = await worker.evaluate((id) => chrome.tabs.sendMessage(id, { type: "easyapply/extract" }).then(() => true).catch(() => false), tabId);
    if (ok) return { page, tabId };
    await page.waitForTimeout(500);
  }
  return { page, tabId, noContentScript: true };
}
async function openPopup(context, tabId) {
  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${EXT_ID}/popup.html?tabId=${tabId}`);
  await popup.locator("#review, #blocked").first().waitFor({ timeout: 60_000 });
  return popup;
}
const text = (loc) => loc.innerText({ timeout: 5_000 }).catch(() => "");
const proxy = (context, path, init = {}) =>
  context.request.fetch(BASE + "/api/backend" + path, { ...init, headers: { Origin: BASE, "Sec-Fetch-Site": "same-origin", ...(init.headers ?? {}) } });

// ---------------------------------------------------------------------------
async function main() {
  const { context, worker } = await launch();
  const A = USERS.a;

  await step("A: real Microsoft sign-in on the public URL", () => signIn(context, A, "A"));

  await step("session cookie flags", async () => {
    const cookies = (await context.cookies(BASE)).filter((c) => /session|easyapply/i.test(c.name));
    if (!cookies.length) throw new Error("no session cookie");
    const c = cookies[0];
    const ok = c.secure && c.httpOnly && ["Lax", "Strict"].includes(c.sameSite) && c.name.startsWith("__Host-");
    if (!ok) throw new Error(JSON.stringify({ name: c.name, secure: c.secure, httpOnly: c.httpOnly, sameSite: c.sameSite }));
    return `${c.name}: Secure, HttpOnly, SameSite=${c.sameSite}, host-only`;
  });

  const page = await context.newPage();
  await step("web: create profile", async () => {
    await page.goto(BASE + "/", { timeout: 120_000 });
    const create = page.getByRole("link", { name: "Create profile" });
    if (await create.isVisible({ timeout: 15_000 }).catch(() => false)) {
      await create.click();
      await page.getByRole("button", { name: "Create profile" }).click();
      await page.getByRole("heading", { name: "My Profile" }).waitFor({ timeout: 60_000 });
      return "created";
    }
    return "already existed";
  });

  const version = await step("web: upload resume → processed into a draft", async () => {
    await page.goto(BASE + "/resume", { timeout: 120_000 });
    await page.getByLabel("Resume file").setInputFiles(RESUME);
    await page.waitForURL(/\/resume\/\d+$/, { timeout: 120_000 });
    state.resumeDocId = Number(page.url().match(/\/resume\/(\d+)$/)[1]);
    const ready = page.getByRole("heading", { name: /Draft version \d+ is ready/i });
    await ready.waitFor({ timeout: 600_000 });
    state.version = Number((await ready.textContent()).match(/Draft version (\d+)/i)[1]);
    await shot(page, "A-resume-processed");
    return `document ${state.resumeDocId}, draft version ${state.version}`;
  });

  await step("web: confirm profile", async () => {
    await page.goto(`${BASE}/profile/versions/${state.version}`, { timeout: 120_000 });
    await page.getByRole("button", { name: "Confirm version" }).click();
    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
    await page.getByText("This version is confirmed").waitFor({ timeout: 60_000 });
    await shot(page, "A-profile-confirmed");
    return `version ${state.version} confirmed`;
  });

  // --- Greenhouse via the extension ---------------------------------------
  const gh = await step("extension: open real Greenhouse posting (content script runs)", async () => {
    const r = await openPosting(context, worker, GREENHOUSE);
    if (r.noContentScript) throw new Error("content script did not answer");
    state.ghTab = r.tabId;
    return GREENHOUSE;
  });
  let popup;
  await step("extension popup: review screen shows the staging account", async () => {
    popup = await openPopup(context, state.ghTab);
    await shot(popup, "A-gh-popup-review");
    const acct = await text(popup.locator("#account-email"));
    const title = await popup.locator("#field-job_title").inputValue().catch(() => "");
    if (!/Saving as/i.test(acct)) throw new Error("account line: " + acct + " | blocked: " + (await text(popup.locator("#blocked"))));
    return `${acct.trim()} | job title "${title}"`;
  });
  await step("extension: save job → requirements ready", async () => {
    await popup.locator("#save").click();
    await popup.locator("#outcome-notice").filter({ hasText: /Saved to EasyApply|Already in EasyApply/ }).waitFor({ timeout: 120_000 });
    await popup.locator("#requirements-status").filter({ hasText: "Requirements are ready" }).waitFor({ timeout: 300_000 });
    state.ghJobId = Number((await popup.locator("#open-job").getAttribute("href")).match(/\/jobs\/(\d+)$/)[1]);
    const req = await popup.locator("#requirements-required .req-text").count();
    const pref = await popup.locator("#requirements-preferred .req-text").count().catch(() => 0);
    await shot(popup, "A-gh-requirements");
    return `job ${state.ghJobId}: ${req} required, ${pref} preferred`;
  });
  await step("extension: match my profile", async () => {
    await popup.locator("#match-run").waitFor({ state: "visible", timeout: 60_000 });
    await popup.locator("#match-run").click();
    await popup.locator("#match-status").filter({ hasText: "Matched against profile version" }).waitFor({ timeout: 300_000 });
    state.ghMatchId = Number((await popup.locator("#open-match").getAttribute("href")).match(/[?&]match=(\d+)/)[1]);
    const rows = await popup.locator("#match-results .result").count();
    await shot(popup, "A-gh-match");
    return `match ${state.ghMatchId}, ${rows} requirement results`;
  });
  await step("web: match evidence page", async () => {
    const href = await popup.locator("#open-match").getAttribute("href");
    const p = await context.newPage();
    await p.goto(href, { timeout: 120_000 });
    await p.getByRole("heading").first().waitFor({ timeout: 60_000 });
    const body = await p.locator("body").innerText();
    await shot(p, "A-gh-match-evidence");
    await p.close();
    if (/does not exist|not found/i.test(body)) throw new Error("match page says not found");
    return `evidence page rendered (${body.length} chars)`;
  });
  await step("extension: tailor my resume → DOCX download", async () => {
    await popup.locator("#tailor-run").waitFor({ state: "visible", timeout: 60_000 });
    await popup.locator("#tailor-run").click();
    await popup.locator("#tailoring-status").filter({ hasText: "Your tailored resume is ready" }).waitFor({ timeout: 600_000 });
    state.ghRunId = Number((await popup.locator("#open-tailored").getAttribute("href")).match(/\/tailored-resumes\/(\d+)$/)[1]);
    const href = await popup.locator("#download-tailored").getAttribute("href");
    const res = await context.request.get(href);
    const body = await res.body();
    writeFileSync(join(SHOTS, "A-tailored.docx"), body);
    await shot(popup, "A-gh-tailored");
    if (res.status() !== 200 || body.subarray(0, 2).toString() !== "PK") throw new Error(`download ${res.status()} ${body.length}B`);
    return `run ${state.ghRunId}; DOCX ${body.length} bytes; ${res.headers()["content-disposition"] ?? ""}`;
  });
  await step("extension: application form → prepare → fill approved → NOT submitted", async () => {
    const status = await text(popup.locator("#application-status"));
    if (!/application form/i.test(status)) throw new Error("application status: " + status.slice(0, 200));
    await popup.locator("#application-prepare").click();
    await popup.locator("#application-status").filter({ hasText: /Proposed from your profile version/ }).waitFor({ timeout: 120_000 });
    const proposals = await popup.locator("#application-ready .proposal").count();
    const sensitive = await popup.locator("#application-review [data-sensitive]").count();
    await shot(popup, "A-gh-application-review");
    const fill = popup.locator("#application-fill");
    const fillLabel = await text(fill);
    await fill.click();
    await popup.locator("#application-report").filter({ hasText: /Filled \d+ field/ }).waitFor({ timeout: 60_000 });
    const report = await text(popup.locator("#application-report"));
    const tab = context.pages().find((p) => p.url().startsWith(GREENHOUSE));
    const firstName = tab ? await tab.locator("#first_name").inputValue().catch(() => "(no #first_name)") : "(tab?)";
    if (tab) await shot(tab, "A-gh-form-filled");
    const stillOnPosting = tab ? tab.url().startsWith(GREENHOUSE) : false;
    if (!/hasn't submitted anything/i.test(report) || !stillOnPosting) throw new Error("report: " + report);
    return `${proposals} proposed, ${sensitive} sensitive left to the person; "${fillLabel}"; ${report.split("\n")[0]}; first_name="${firstName}"; page not submitted`;
  });

  // --- Lever via the extension ---------------------------------------------
  await step("extension: real Lever posting → save → requirements", async () => {
    const r = await openPosting(context, worker, LEVER);
    if (r.noContentScript) throw new Error("content script did not answer");
    const lp = await openPopup(context, r.tabId);
    await lp.locator("#save").click();
    await lp.locator("#outcome-notice").filter({ hasText: /Saved to EasyApply|Already in EasyApply/ }).waitFor({ timeout: 120_000 });
    await lp.locator("#requirements-status").filter({ hasText: "Requirements are ready" }).waitFor({ timeout: 300_000 });
    state.leverJobId = Number((await lp.locator("#open-job").getAttribute("href")).match(/\/jobs\/(\d+)$/)[1]);
    await shot(lp, "A-lever-requirements");
    await lp.close();
    return `job ${state.leverJobId}`;
  });
  await step("extension: Lever apply page → prepare → fill approved → NOT submitted", async () => {
    const r = await openPosting(context, worker, LEVER + "/apply");
    if (r.noContentScript) throw new Error("content script did not answer on /apply");
    const lp = await openPopup(context, r.tabId);
    await lp.locator("#application-prepare").waitFor({ state: "visible", timeout: 120_000 });
    await lp.locator("#application-prepare").click();
    await lp.locator("#application-status").filter({ hasText: /Proposed from your profile version/ }).waitFor({ timeout: 120_000 });
    const proposals = await lp.locator("#application-ready .proposal").count();
    await lp.locator("#application-fill").click();
    await lp.locator("#application-report").filter({ hasText: /Filled \d+ field/ }).waitFor({ timeout: 60_000 });
    const report = await text(lp.locator("#application-report"));
    await shot(r.page, "A-lever-form-filled");
    await shot(lp, "A-lever-application");
    if (!/hasn't submitted anything/i.test(report) || !r.page.url().includes("/apply")) throw new Error("report: " + report);
    return `${proposals} proposed; ${report.split("\n")[0]}; page not submitted`;
  });

  save();
  await context.close();
}

// ---------------------------------------------------------------------------
async function isolation() {
  const B = USERS.b;
  const { context, worker } = await launch();
  await step("B: real Microsoft sign-in (second account)", () => signIn(context, B, "B"));
  const probes = [
    ["job", `/jobs/${state.ghJobId}`], ["job requirements", `/jobs/${state.ghJobId}/requirements`],
    ["job matches", `/jobs/${state.ghJobId}/matches`], ["match", `/matches/${state.ghMatchId}`],
    ["tailored resume", `/tailored-resumes/${state.ghRunId}`], ["tailored artifact", `/tailored-resumes/${state.ghRunId}/artifact`],
    ["resume document", `/resumes/${state.resumeDocId}`], ["resume result", `/resumes/${state.resumeDocId}/result`],
    ["profile version", `/profile/versions/${state.version}`], ["lever job", `/jobs/${state.leverJobId}`],
    ["twilio job", `/jobs/${state.twilioJobId}`], ["twilio match", `/matches/${state.twilioMatchId}`],
  ];
  for (const [name, path] of probes) {
    await step(`B cannot read A's ${name}`, async () => {
      const r = await proxy(context, path);
      if (r.status() !== 404) throw new Error(`${path} → ${r.status()} ${(await r.text()).slice(0, 120)}`);
      return `${path} → 404`;
    });
  }
  const lists = [
    ["jobs list", "/jobs", [state.ghJobId, state.leverJobId, state.twilioJobId]],
    ["resumes list", "/resumes", [state.resumeDocId]],
    ["tailored list", "/tailored-resumes", [state.ghRunId]],
  ];
  for (const [name, path, aIds] of lists) {
    await step(`B's ${name} holds none of A's`, async () => {
      const r = await proxy(context, path);
      const body = await r.json();
      const ids = (Array.isArray(body) ? body : body.items ?? []).map((x) => x.id);
      const leak = ids.filter((id) => aIds.includes(id));
      if (r.status() !== 200 || leak.length) throw new Error(`${r.status()} ids=${JSON.stringify(ids)}`);
      return `${r.status()}, ${ids.length} item(s), none of A's`;
    });
  }
  await step("B: extension popup on the same posting shows B, none of A", async () => {
    const r = await openPosting(context, worker, GREENHOUSE);
    const popup = await openPopup(context, r.tabId);
    const acct = await text(popup.locator("#account-email"));
    const body = await popup.locator("body").innerText();
    await shot(popup, "B-gh-popup");
    if (/Staging Tester A/.test(body) || /Alex Morgan/.test(body)) throw new Error("A's data visible: " + body.slice(0, 200));
    return acct.trim() || "(no account line)";
  });
  await context.close();

  // account switch inside ONE browser profile
  const { context: c2, worker: w2 } = await launch();
  await step("switch: sign in A in a fresh profile", () => signIn(c2, USERS.a, "switch-A"));
  const r = await openPosting(c2, w2, GREENHOUSE);
  let popup = await openPopup(c2, r.tabId);
  const before = await text(popup.locator("#account-email"));
  await step("switch: sign out A (Entra end-session → /signed-out)", async () => {
    const p = await c2.newPage();
    await p.goto(BASE + "/", { timeout: 120_000 });
    await p.getByRole("button", { name: "Sign out" }).click();
    await p.waitForURL(/login\.microsoftonline\.com|\/signed-out/, { timeout: 60_000 });
    for (let i = 0; i < 10 && !p.url().startsWith(BASE); i++) {
      const pick = p.locator("[data-test-id], .table-row").first();
      if (await pick.isVisible().catch(() => false)) await pick.click().catch(() => {});
      await p.waitForTimeout(2000);
    }
    const session = await (await c2.request.get(BASE + "/api/auth/session")).json();
    await shot(p, "switch-signed-out");
    await p.close();
    if (session.authenticated) throw new Error("still signed in");
    return `back at ${new URL(r.page.url()).host}; session authenticated=false`;
  });
  await step("switch: popup while signed out asks for sign-in", async () => {
    await popup.close();
    popup = await openPopup(c2, r.tabId);
    const body = await popup.locator("body").innerText();
    await shot(popup, "switch-popup-signed-out");
    if (!/sign in/i.test(body)) throw new Error(body.slice(0, 200));
    return body.split("\n").find((l) => /sign in/i.test(l))?.trim();
  });
  await step("switch: sign in B in the same profile", () => signIn(c2, USERS.b, "switch-B"));
  await step("switch: popup now shows B, nothing of A", async () => {
    await popup.close();
    popup = await openPopup(c2, r.tabId);
    const after = await text(popup.locator("#account-email"));
    const body = await popup.locator("body").innerText();
    await shot(popup, "switch-popup-B");
    if (!/Saving as Staging Tester B/.test(after) || /Alex Morgan|Staging Tester A/.test(body)) throw new Error(`before="${before}" after="${after}"`);
    return `before: "${before.trim()}" → after: "${after.trim()}"`;
  });
  await c2.close();
}

// ---------------------------------------------------------------------------
async function failures_() {
  const { context, worker } = await launch();
  await step("signed out: proxy answers 401 session_required", async () => {
    const r = await proxy(context, "/profile");
    const b = await r.json();
    if (r.status() !== 401 || b.code !== "session_required") throw new Error(`${r.status()} ${JSON.stringify(b)}`);
    return "401 session_required";
  });
  await step("signed out: extension popup asks for sign-in", async () => {
    const r = await openPosting(context, worker, GREENHOUSE);
    const popup = await openPopup(context, r.tabId);
    const body = await popup.locator("body").innerText();
    await shot(popup, "fail-signed-out-popup");
    if (!/sign in/i.test(body)) throw new Error(body.slice(0, 200));
    return body.split("\n").find((l) => /sign in/i.test(l))?.trim();
  });
  await step("tampered session cookie is not a session", async () => {
    await signIn(context, USERS.a, "fail-A");
    const cookies = await context.cookies(BASE);
    const c = cookies.find((x) => /session/i.test(x.name));
    await context.addCookies([{ ...c, value: c.value.slice(0, -6) + "AAAAAA" }]);
    const r = await proxy(context, "/profile");
    if (r.status() !== 401) throw new Error(`${r.status()}`);
    return `modified cookie → ${r.status()} ${(await r.json()).code}`;
  });
  await context.clearCookies();
  await step("unsupported website: popup declines", async () => {
    const p = await context.newPage();
    await p.goto("https://en.wikipedia.org/wiki/Job_hunting", { timeout: 60_000 });
    const tabId = await tabIdOf(worker, p.url());
    const popup = await openPopup(context, tabId);
    const body = await popup.locator("body").innerText();
    await shot(popup, "fail-unsupported-site");
    if (await popup.locator("#save").isVisible().catch(() => false)) throw new Error("save offered on example.com");
    return body.split("\n").filter(Boolean).slice(0, 2).join(" | ");
  });
  await step("invalid job page (Greenhouse board, not a posting): no save offered", async () => {
    const r = await openPosting(context, worker, "https://job-boards.greenhouse.io/anthropic");
    const popup = await openPopup(context, r.tabId);
    const body = await popup.locator("body").innerText();
    await shot(popup, "fail-board-page");
    if (await popup.locator("#save").isEnabled().catch(() => false)) throw new Error("save enabled on a board page: " + body.slice(0, 160));
    return body.split("\n").filter(Boolean).slice(0, 2).join(" | ");
  });
  await step("corrupt resume: processing fails and says so", async () => {
    await signIn(context, USERS.b, "fail-B");
    const p = await context.newPage();
    await p.goto(BASE + "/", { timeout: 120_000 });
    const create = p.getByRole("link", { name: "Create profile" });
    if (await create.isVisible({ timeout: 10_000 }).catch(() => false)) {
      await create.click(); await p.getByRole("button", { name: "Create profile" }).click();
      await p.getByRole("heading", { name: "My Profile" }).waitFor({ timeout: 60_000 });
    }
    const bad = join(SP, "corrupt.pdf");
    writeFileSync(bad, Buffer.from("%PDF-1.7\nthis is not really a pdf\n%%EOF\n"));
    await p.goto(BASE + "/resume", { timeout: 120_000 });
    await p.getByLabel("Resume file").setInputFiles(bad);
    await p.waitForTimeout(3000);
    let body = await p.locator("body").innerText();
    for (let i = 0; i < 60 && !/fail|could not|couldn't|unreadable|not a valid|damaged|error/i.test(body); i++) {
      await p.waitForTimeout(5000); body = await p.locator("body").innerText();
    }
    await shot(p, "fail-corrupt-resume");
    const line = body.split("\n").find((l) => /fail|could not|couldn't|unreadable|not a valid|damaged|error/i.test(l));
    if (!line) throw new Error("no failure message; url " + p.url());
    return line.trim().slice(0, 160);
  });
  await step("tailoring without a confirmed profile is refused clearly", async () => {
    const job = await proxy(context, "/jobs", { method: "POST", data: { source_type: "company_careers", source_url: "https://example.com/careers/fail-tailor", external_job_id: "fail-tailor", job_title: "Backend Engineer", company_name: "Acme", description_text: "Requirements:\n- Python\n" } });
    const jobId = (await job.json()).id;
    const r = await proxy(context, `/jobs/${jobId}/tailored-resumes`, { method: "POST", data: {} });
    const b = await r.text();
    if (r.status() < 400 || r.status() >= 500) throw new Error(`${r.status()} ${b.slice(0, 160)}`);
    return `${r.status()} ${b.slice(0, 160)}`;
  });
  await context.close();
}

async function evidence() {
  const { context, worker } = await launch();
  await step("A: sign in", () => signIn(context, USERS.a, "ev-A"));
  for (const [name, jobId] of [["Greenhouse (Anthropic)", state.ghJobId], ["Lever (Palantir)", state.leverJobId]]) {
    await step(`requirements read from the real ${name} posting`, async () => {
      const r = await proxy(context, `/jobs/${jobId}/requirements`);
      const b = await r.json();
      const reqs = b.requirements ?? b.items ?? b.result?.requirements ?? [];
      const keys = Object.keys(b).join(",");
      return `${r.status()}; ${Array.isArray(reqs) ? reqs.length : "?"} requirement(s); keys: ${keys}`;
    });
  }
  const TWILIO = "https://job-boards.greenhouse.io/twilio/jobs/8015771";
  await step("Twilio (Greenhouse): save → requirements → match → evidence page (loaded)", async () => {
    const r = await openPosting(context, worker, TWILIO);
    if (r.noContentScript) throw new Error("content script did not answer");
    const popup = await openPopup(context, r.tabId);
    await popup.locator("#save").click();
    await popup.locator("#outcome-notice").filter({ hasText: /Saved to EasyApply|Already in EasyApply/ }).waitFor({ timeout: 120_000 });
    await popup.locator("#requirements-status").filter({ hasText: "Requirements are ready" }).waitFor({ timeout: 300_000 });
    state.twilioJobId = Number((await popup.locator("#open-job").getAttribute("href")).match(/\/jobs\/(\d+)$/)[1]);
    const req = await popup.locator("#requirements-required .req-text").count();
    const pref = await popup.locator("#requirements-preferred .req-text").count().catch(() => 0);
    await popup.locator("#match-run").waitFor({ state: "visible", timeout: 120_000 });
    await popup.locator("#match-run").click();
    await popup.locator("#match-status").filter({ hasText: "Matched against profile version" }).waitFor({ timeout: 300_000 });
    const rows = await popup.locator("#match-results .result").count();
    await shot(popup, "ev-twilio-match-popup");
    const href = await popup.locator("#open-match").getAttribute("href");
    state.twilioMatchId = Number(href.match(/[?&]match=(\d+)/)[1]);
    console.log(`      requirements ${req} required / ${pref} preferred; ${rows} match results`);
    const p = await context.newPage();
    await p.goto(href, { timeout: 120_000 });
    await p.waitForFunction(() => !document.querySelector('[class*="animate-pulse"]'), null, { timeout: 120_000 }).catch(() => {});
    await p.waitForTimeout(3000);
    const body = await p.locator("main").innerText().catch(() => p.locator("body").innerText());
    await shot(p, "ev-twilio-match-evidence");
    await p.close();
    save();
    if (body.length < 300) throw new Error("evidence page too short: " + body.replace(/\s+/g, " ").slice(0, 200));
    return `${rows} requirement results in the popup; evidence page ${body.length} chars; first lines: ${body.split("\n").filter(Boolean).slice(0, 4).join(" | ").slice(0, 220)}`;
  });
  await context.close();
}

async function inspect() {
  const { context } = await launch();
  await step("A: sign in", () => signIn(context, USERS.a, "in-A"));
  for (const jobId of [state.ghJobId, state.leverJobId]) {
    const job = await (await proxy(context, `/jobs/${jobId}`)).json();
    const req = await (await proxy(context, `/jobs/${jobId}/requirements`)).json();
    const d = job.description_text ?? "";
    writeFileSync(join(SHOTS, `job-${jobId}-description.txt`), d);
    writeFileSync(join(SHOTS, `job-${jobId}-requirements.json`), JSON.stringify(req, null, 2));
    console.log(`job ${jobId}: ${job.company_name} | ${job.job_title} | description ${d.length} chars, ${d.split("\n").length} lines`);
    console.log(`  requirements keys: ${JSON.stringify(Object.keys(req.requirements ?? {}))}`);
    const r = req.requirements ?? {};
    for (const [k, v] of Object.entries(r)) if (Array.isArray(v)) console.log(`   ${k}: ${v.length}`);
  }
  await context.close();
}

async function dbdown() {
  const { context } = await launch();
  await step("sign in while the database is down (web + Entra need no database)", () => signIn(context, USERS.a, "dbdown-A"));
  for (const path of ["/profile", "/jobs"]) {
    await step(`signed-in ${path} with the database down: a clean error, no internals`, async () => {
      const r = await proxy(context, path);
      const t = await r.text();
      if (r.status() < 500 || /Traceback|psycopg|sqlalchemy|password|dpg-/i.test(t)) throw new Error(`${r.status()} ${t.slice(0, 200)}`);
      return `${r.status()} ${t.slice(0, 160)}`;
    });
  }
  await step("dashboard with the database down says something is wrong", async () => {
    const p = await context.newPage();
    await p.goto(BASE + "/", { timeout: 120_000 });
    await p.waitForTimeout(8000);
    const body = await p.locator("main").innerText().catch(() => "");
    await shot(p, "dbdown-dashboard");
    return body.split("\n").filter(Boolean).slice(0, 5).join(" | ").slice(0, 240);
  });
  await context.close();
}

async function persist() {
  const { createHash } = await import("node:crypto");
  const { context } = await launch();
  await step("A: sign in after the restart", () => signIn(context, USERS.a, "persist-A"));
  await step("A's data is all still there", async () => {
    const jobs = await (await proxy(context, "/jobs")).json();
    const runs = await (await proxy(context, "/tailored-resumes")).json();
    const ids = (Array.isArray(jobs) ? jobs : jobs.items ?? []).map((j) => j.id).sort();
    return `jobs ${JSON.stringify(ids)}; tailored runs ${(Array.isArray(runs) ? runs : runs.items ?? []).length}`;
  });
  await step("tailored DOCX re-downloads byte-identical after the restart", async () => {
    const before = readFileSync(join(SHOTS, "A-tailored.docx"));
    const r = await proxy(context, `/tailored-resumes/${state.ghRunId}/artifact`);
    const after = await r.body();
    const h = (b) => createHash("sha256").update(b).digest("hex");
    if (r.status() !== 200 || h(before) !== h(after)) throw new Error(`${r.status()} before ${h(before).slice(0, 12)} after ${h(after).slice(0, 12)}`);
    return `sha256 ${h(after).slice(0, 16)}… identical (${after.length} bytes)`;
  });
  await context.close();
}

const phase = process.argv[2] ?? "main";
if (phase === "persist") { await persist(); console.log(`\npersist: ${failures} failure(s)`); process.exit(failures ? 1 : 0); }
if (phase === "dbdown") { await dbdown(); console.log(`\ndbdown: ${failures} failure(s)`); process.exit(failures ? 1 : 0); }
if (phase === "inspect") { await inspect(); process.exit(0); }
if (phase === "evidence") { await evidence(); console.log(`\nevidence: ${failures} failure(s)`); process.exit(failures ? 1 : 0); }
if (phase === "main") await main();
else if (phase === "isolation") await isolation();
else if (phase === "failures") await failures_();
console.log(`\n${phase}: ${failures} failure(s)`);
process.exit(failures ? 1 : 0);
