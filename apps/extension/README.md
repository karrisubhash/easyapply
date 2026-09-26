# EasyApply Chrome extension

Manifest V3. On a job posting you are viewing, it:

1. reads the title, company, location, description, page address and the
   site's own job id, shows you exactly what will be saved, and — only when you
   press **Save** — sends it to EasyApply's existing `POST /api/v1/jobs`;
2. shows the job's **requirements** as EasyApply structured them (required and
   preferred, with where each was found in the posting);
3. on **Match my profile**, shows EasyApply's **match** of your current
   confirmed profile version against them — every verdict, explanation and
   piece of evidence exactly as the backend stored it;
4. on **Tailor my resume**, starts a **tailored resume** on the server, follows
   it to the end, and links it (open in EasyApply, download the .docx);
5. on the job's **application form**, on **Prepare answers from my profile**,
   proposes values from your confirmed profile with how sure each one is,
   marks every question it will not answer for you ("Review required"), and —
   only when you press **Fill** — fills the text fields you ticked, if they are
   empty.

It **never submits an application**, never clicks anything on the page, never
chooses an option or attaches a file, never answers a sensitive question
(work authorization, sponsorship, demographics, pay, ...), and never asks for
AI tailoring — the server decides the generator. You review the form and
submit it yourself.

It supports **two sites**: Greenhouse-hosted job boards
(`job-boards.greenhouse.io`, `job-boards.eu.greenhouse.io` — the form is on
the posting's page) and Lever (`jobs.lever.co` — the form is on the posting's
`/apply` page). Everything else is reported as unsupported rather than guessed
at.

Design, identity decision and limitations: [docs/chrome-extension.md](../../docs/chrome-extension.md).

## Load it in Chrome

You need the EasyApply stack running as the root [README](../../README.md)
describes: the API on `127.0.0.1:8000` and the web app on `localhost:3000`.

1. **Let the web app accept the extension.** The extension saves through the
   web app's proxy (that is where your identity is attached), and the proxy
   refuses cross-site writes unless told otherwise. Add this line to
   `apps/web/.env.local`, then restart `npm run dev`:

   ```
   PROXY_ALLOWED_EXTENSION_IDS=jjhciekcmhmngciccfgignmmlgjknakk
   ```

   The id is fixed by the `key` in `src/manifest.json`, so it is the same on
   every machine that loads this build.

2. **Build it**, from the repo root:

   ```powershell
   npm install
   npm run build --workspace @easyapply/extension
   ```

   This writes `apps/extension/dist/`.

3. **Load it.** Open `chrome://extensions`, turn on **Developer mode** (top
   right), click **Load unpacked**, and choose `apps/extension/dist`. The card
   should show the id `jjhciekcmhmngciccfgignmmlgjknakk`. Pin it from the
   puzzle-piece menu so the icon stays in the toolbar.

4. **Use it.** Open any posting on `job-boards.greenhouse.io` (a company's board
   is `https://job-boards.greenhouse.io/<company>`; open one job from it), click
   the EasyApply icon, check the fields and the account named beside the button
   ("Saving as …"), and press **Save to EasyApply**. **Open in EasyApply** takes
   you to the saved job. If the web app runs `AUTH_MODE=dev-session`, sign in on
   it first, in the same Chrome profile.

After changing the code: build again, click the reload arrow on the extension's
card in `chrome://extensions`, and reload any Greenhouse tab that was already
open (Chrome only injects the content script into pages loaded after that).

### A web app somewhere else

The web app's address is fixed at build time, because it is also the one host
permission the extension holds besides the job sites:

```powershell
$env:EASYAPPLY_WEB_URL = "http://127.0.0.1:3000"
npm run build --workspace @easyapply/extension
Remove-Item Env:EASYAPPLY_WEB_URL
```

It must be an origin only (`scheme://host[:port]`).

## Using the workflow

On a saved posting the popup shows, below the saved job:

| Section | What you see | What you can do |
|---|---|---|
| Requirements | Required and preferred items as EasyApply read them, each with its kind, detail and the quote it came from (flagged when the quote could not be verified). While EasyApply is still reading: "Job saved. EasyApply is extracting requirements." | **Check again** if it takes long; open the job in EasyApply to retry a failed reading. |
| Your match | Which profile version is used (your current confirmed one), then the stored match: counts per axis, coverage, and every requirement's verdict — Matched / Not found / Uncertain — with its explanation and evidence. | **Match my profile**. No profile, only a draft, or no current version each say what to do instead. |
| Tailored resume | The run's stage (Queued → Writing → Checking against your profile → Preparing the file), then Ready, Not approved — no file, or Failed with the reason. | **Tailor my resume**, **Open in EasyApply**, **Download (.docx)**. |
| Application form | Every field, grouped: Ready to fill, Check before filling, Needs your answer, Attach yourself, Already filled in. | Untick anything, then **Fill N selected fields**. The page's own form is then yours to finish and submit. |

Match and tailor are explicit: opening the popup only reads. Nothing is sent
to the job site except the values you approved, typed into empty text fields.

## Who the job is saved for

Whoever this browser is signed in to EasyApply as. The extension's requests
carry the browser's session cookie for the web app, exactly as the web UI's do,
and the web app decides who that is: `DEV_USER_EMAIL` under the default
`AUTH_MODE=development`, the account signed in on the web app's page under
`AUTH_MODE=dev-session`, and the Microsoft Entra account signed in under
`AUTH_MODE=entra`. The review screen names it beside the Save button ("Saving
as …": the account's email, else its name, else "your EasyApply account" —
an Entra account may have neither); signed out, it offers a link to sign in and
does not send anything.

The extension never reads that cookie (it is HttpOnly, and the extension has
no `cookies` permission) and sends no identity of its own: no user id, no
email, no token. The one header it adds is `X-EasyApply-Session`: the account
tag the web app itself reported in `/api/auth/session`, handed back on every
`/api/backend` request. It authenticates nothing; it lets the web app's proxy
refuse (HTTP 409 `session_changed`) a request made from a popup opened for an
account this browser is no longer signed in to. The popup is for one account
by that tag — "already saved", the job panel and the form's proposals are all
that account's — and if the proxy says the account changed, the popup throws
all of it away and offers **Reload**. See [docs/authentication.md](../../docs/authentication.md).

## What it reads on Greenhouse

| Field | Where it comes from |
|---|---|
| `job_title` | `.job__title h1` — **required**; without it nothing can be saved |
| `description_text` | `.job__description`, as text: paragraphs, headings on their own line, `- ` / `1. ` list items — **required** |
| `company_name` | the page title, `Job Application for <title> at <company>`; else the board logo's alt text |
| `location_raw` | `.job__location` |
| `external_job_id` | the number in `/<board>/jobs/<id>` |
| `source_url` | the page address, without its `#fragment` |
| `source_type` | always `greenhouse` |

Title, company, location and description can be corrected before saving. The
source, id and address cannot: they are what EasyApply uses to recognise a job
it already has.

## Messages you may see

| Message | Meaning |
|---|---|
| **Not a supported job page.** | No adapter reads this site. Only Greenhouse boards in this version. |
| **No single job here.** | A Greenhouse board or search page, or a closed posting Greenhouse redirected to its board. Open one posting. |
| **Can't read this page yet.** | A Greenhouse page with no content script: still loading, or open since before the extension was installed/reloaded. Wait, or reload the page. |
| **Couldn't read this job.** | A posting page where the title or description was not found. Nothing was sent. |
| **Sign in to EasyApply to save this job.** | `AUTH_MODE=dev-session` or `entra`, and this browser is not signed in. Sign in on the web app, then reopen the popup. |
| **You're not signed in to EasyApply.** | Save was refused for want of a session (HTTP 401 `session_required`) when the popup had not been able to check who was signed in. Nothing was saved. |
| **Saved to EasyApply.** | A new job (HTTP 201), structured in the same request. |
| **Already in EasyApply.** | EasyApply had this posting (HTTP 200) and kept the version it saved first. Any edit you made was not applied, and the popup says which fields differ. |
| **The web app doesn't accept this extension yet.** / **The web app doesn't accept this request from the extension.** | Step 1 above is missing, or the web app was not restarted. |
| **Job saved. EasyApply is extracting requirements.** | The job's reading is queued or running; the popup checks again, a bounded number of times. |
| **EasyApply is still reading this job.** | It is still running after about a minute. **Check again**, or follow it in EasyApply. |
| **Processing failed while ...** | Reading the requirements failed; the reason is EasyApply's own words for the error code. Retry from the job's page in EasyApply. |
| **You don't have an EasyApply profile yet.** / **Confirm your profile to match it.** / **Choose which profile version to use.** | Matching and tailoring use your current confirmed profile version; each message links where to fix it. |
| **Not approved — no file.** | The tailored resume contains statements that could not be traced to your profile, so it cannot be downloaded. Its page in EasyApply lists them. |
| **Your EasyApply session expired. Sign in again.** | The popup knew who was signed in, and the web app no longer recognises this browser (signed out, or the session expired; HTTP 401 `session_required`). Sign in again and reopen the popup. Nothing was done on your behalf. |
| **You're signed in to EasyApply as a different account now.** | Another account was signed in on the web app while the popup was open (HTTP 409 `session_changed`), or just before **Fill**. The popup has discarded everything it held for the previous account; **Reload** continues as the account signed in now. Nothing was done for either account. |
| **Couldn't check which account this browser is signed in as.** | The web app gave no account tag (a version older than it), so the popup does not load the saved job's details or read a profile for the form. Saving still works. |
| **This is a job's application form.** | A Lever `/apply` page: nothing to save here (the link opens the posting), but the form can be prepared. |
| **Review required** | A field EasyApply will not fill: a sensitive question, a choice, a file, or something your profile doesn't say. |
| **EasyApply isn't reachable.** / **The EasyApply API isn't running.** | Nothing on `localhost:3000`, or the web app is up but the API behind it is not. |

## Tests

```powershell
npm run typecheck --workspace @easyapply/extension
npm run test --workspace @easyapply/extension                # unit + flow tests (Vitest, jsdom)
npm run test:browser --workspace @easyapply/extension        # the built extension in real Chromium, against a stub web app
npm run test:browser:real --workspace @easyapply/extension   # the same suite against the REAL stack (below)
```

`test:browser:real` (`e2e/real-stack.mjs`) builds and starts everything it
needs on its own ports and data, so it runs beside a development server and
beside the web app's own e2e suite: database `easyapply_ext_e2e` (rebuilt from
the Alembic migrations), the API on `127.0.0.1:8300` with every AI switch off,
and a production build of the web app (`apps/web/.next-ext-e2e`) on
`127.0.0.1:3300` in `AUTH_MODE=dev-session` with this extension allowed. It
seeds two accounts' confirmed profiles through the API, runs the browser
suite, compares what the popup shows with what the API stored, and stops the
servers. `EXT_E2E_SKIP_BUILD=1` reuses an existing build.

`test:browser` builds the extension into `.artifacts/e2e-extension`, loads it
into Playwright's Chromium (`npx playwright install chromium` if it is not
installed), serves the Greenhouse fixtures at their real `https://` addresses,
and saves to a recording stub of the web app on `127.0.0.1:3397`.

The stub behaves like the web app under `AUTH_MODE=dev-session`: the suite
signs in on its page first, and asserts that the extension's save carries that
page's session cookie.

To run the same browser suite against a **real** web app and API, start them
on spare ports with a database the suite may write to — never the development
one — with the web app in `AUTH_MODE=dev-session` (and a `SESSION_SECRET`) and
its `PROXY_ALLOWED_EXTENSION_IDS` set, then:

```powershell
$env:EXTENSION_E2E_WEB_URL = "http://127.0.0.1:3300"
npm run test:browser --workspace @easyapply/extension
```

## Adding a site

A site lives in `src/adapters/<site>/`: `site.ts` (its hosts, match patterns
and path rules, shared by both adapters), `job.ts` (the posting) and, if it
has one, `application.ts` (its application form). Greenhouse and Lever are the
two examples; no code outside those folders knows a site's markup.

1. Write the posting adapter implementing `SiteAdapter`
   (`src/extraction/types.ts`): `handles(url)` and a pure, read-only
   `extract(document, url)` that returns `extracted`, `not_job_page`,
   `application_page` (a form on a page of its own, like Lever's `/apply`),
   `failed` or `unsupported` — never throws, never guesses.
2. Add it to `ADAPTERS` in `src/extraction/registry.ts`, and its form adapter
   (implementing `FormAdapter`, `src/forms/types.ts`) to `FORM_ADAPTERS` in
   `src/forms/registry.ts`.
3. Add its match patterns to `content_scripts` and `host_permissions` in
   `src/manifest.json`. `test/manifest.test.ts` fails until 2 and 3 agree.
4. Add a fixture under `test/fixtures/` copied from the site's real structure
   (with invented words), and tests beside `test/greenhouse.test.ts`.
