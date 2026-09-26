# Resume Intelligence

How an uploaded resume becomes a draft profile version, and why the pieces are
arranged the way they are.

```
Upload → Validate → Store → Run → Extract → [OCR] → Structure → Validate result
       → Draft ProfileVersion → User review → Confirm (existing flow)
```

Everything here is local. There is no Azure, no queue, no Temporal and no
Kubernetes in this milestone; §11 and §12 describe where each of those attaches.

---

## 1. Domain model

Five tables, in `services/api/app/models/resume_document.py` and
`resume_processing.py`.

| Table | Owns |
|---|---|
| `resume_documents` | The uploaded artifact: filename, media type, size, SHA-256, storage key. Immutable. |
| `resume_processing_runs` | One attempt at processing it: status, failure, counts, timings, the version it produced. |
| `resume_processing_events` | One row per stage per run: outcome and duration. |
| `resume_document_pages` | Extracted text, one row per page **per run**. |
| `resume_structured_results` | The validated AI reading, with provenance, and what could not be mapped. |

**The artifact and the attempt are separate tables.** An upload happens once;
processing can happen many times. A status column on the document would destroy
the history of attempts and make "it failed, then it worked" unrepresentable.
The document therefore has no status at all — it is a fact, not a lifecycle.

**Pages are keyed to the run, not the document**, because a retry that uses OCR
legitimately produces different text for the same page. They live in their own
table so a routine `SELECT` of a run cannot drag resume text into a log line, an
exception repr or an APM trace, and so a retention purge is one `DELETE` that
rewrites no audit row. `text` is nullable for exactly that purge; `char_count`,
`page_count` and `ocr_page_count` survive it, so "what did this run read?" stays
answerable after the content is gone.

**Documents belong to a `user_id`, not a `profile_id`.** A resume can be
uploaded before a profile exists — that is the normal first-run path — and the
document should outlive any particular profile version. The profile is created
on demand during materialisation.

## 2. Processing state machine

Defined once in `app/models/resume_processing.py` and enforced twice.

```
uploaded → validating → extracting ─┬─────────────────→ structuring → validating_result
                                    └→ ocr_required → ocr_processing ─┘
                                                     → materialising → completed
any state → failed
```

- **Terminal:** `completed`, `failed`. Nothing leaves them.
- **Retryable:** `failed` only — and a retry is always a **new run** with the
  next `attempt`, never a reopened one. That keeps the graph acyclic and makes
  "how many times did this fail" a `COUNT`.
- **Failure** is one state plus `failed_stage` and `error_code`. Every failure
  has identical successor semantics, so per-stage failure states would multiply
  the transition table for no expressive gain.

Enforcement is a PL/pgSQL trigger (`trg_resume_processing_runs_transition`) as
well as a Python guard, because the whole point of the design is that a worker
can later write this table directly, and a rule that lives in one service is not
a rule. The trigger fires on **INSERT as well as UPDATE** — an UPDATE-only
trigger would happily accept a row created directly in `completed`.

Every `RAISE` carries `USING CONSTRAINT`. `app/api/errors.py` maps
`(SQLSTATE 23001, constraint_name IS NULL)` to the profile domain's "this
version is confirmed" message, so a bare `restrict_violation` raised here would
be reported to clients as a completely unrelated profile error.

**Deliberately absent: a "only one active run" constraint.** Processing is
synchronous, so a killed process strands a run in a non-terminal state. A
uniqueness rule over active runs would then wedge the document permanently with
no way back. Without it, a stranded run is untidy and the next attempt proceeds.

## 3. Storage abstraction

`app/services/storage.py` defines `StorageBackend` — `put`, `get`, `exists`,
`delete` — with `LocalFilesystemStorage` and `InMemoryStorage` implementations.

Processing code never learns where bytes live. It holds an opaque storage key it
received from `put` and passes it back to `get`. There is deliberately **no
`path()` and no `full_path`**: the moment one exists, a caller builds a string
with it and the abstraction is over.

Keys are derived from content, never from the uploaded filename:
`resumes/{user_id}/{sha256}.{ext}`. That is safe by construction, stable across
retries, and cannot collide between users.

## 4. Extraction

`app/services/resume/extraction.py`. PDFs go through `pypdf`, one
`ExtractedPage` per page; pages that yield nothing are still emitted as empty
pages, because dropping them would renumber everything after and a page number
that disagrees with the document a human is looking at is worse than an empty
page.

DOCX goes through `python-docx`, keeping headings, paragraphs and table rows in
document order with headings marked — "EDUCATION" being a heading is a real
signal for structuring. It emits **one** page and says so in its metadata:
pagination is a Word rendering decision, not a property of the file, so no page
boundaries are invented.

What a Word template does is read too: page headers and footers **first**
(that is where templates put the contact block; the page's `furniture_lines`
metadata says how many lines they are), then the body — looking through content
controls (`w:sdt`), reading a text box straight after the paragraph that
anchors it and a nested table straight after its row, and collecting hyperlinks
from table cells and text boxes. A merged cell is read once, from the row's own
`w:tc` elements; `row.cells` returned it once per grid column. A header or
footer that cannot be read (a reference to a relationship that is not there)
is left out, and whatever it had added is taken back; it never costs the body.

A link in a table cell is positioned by what reads before it in its own row —
the earlier cells, then the earlier paragraphs of its cell — not only by what
came before the row. A template that puts the whole body in one sidebar | main
row otherwise placed every project link "above the first heading", on the
contact record.

The candidate's name is looked for on the first line of the furniture and the
first line of the body, nowhere else. The furniture's line is taken only when a
word of it appears in the local part of an email in the header block
("Priya Sharma" over priya@example.com) — a recruiter's "Randstad Technologies"
header never is — and then it outranks the body's first line, which under a
header that already printed the name is a tagline. Otherwise the body's first
line is read as it always was.

Encryption: a PDF with only an owner password opens with the empty password,
as every viewer does, and is read — by walking its page tree, never by trusting
its declared `/Count`. One that needs a password to open is
`document_encrypted`. AES encryption needs the `cryptography` package, which
this service does not ship; a certificate security handler or an algorithm
version pypdf does not implement raises `NotImplementedError` instead. Either is
also `document_encrypted` (with a message asking for an unprotected copy) rather
than an `unexpected_error` retried to exhaustion.

Bounds, as module constants beside the code they bound: at most
`MAX_PDF_PAGES` (20) pages of a PDF are read — the rest are counted and
reported, never opened; a DOCX whose ZIP directory declares more than
`MAX_DOCX_UNCOMPRESSED_BYTES` (50 MiB), more than `MAX_DOCX_ENTRIES` parts, or
a compression ratio over `MAX_DOCX_COMPRESSION_RATIO` (100) is refused as
`document_too_large` before anything is inflated.

`ExtractedDocument.text()` renders `<page number="N">` markers, which is what
lets the structuring stage cite a page.

## 5. OCR boundary

`app/services/resume/ocr.py`. `OcrEngine` takes image bytes and returns text —
nothing else. It knows nothing about PDFs, pages or resumes, which is what keeps
the rest of the system from depending on Tesseract; an Azure Document
Intelligence adapter is a new class satisfying the same protocol.

Rasterisation lives outside the engine and uses PDFium (`pypdfium2`) rather than
a poppler shell-out: no external binary, and no subprocess handed a path to an
untrusted file.

A PDF page yielding fewer than `RESUME_OCR_MIN_CHARS_PER_PAGE` characters is
treated as a scan. Only those pages are re-read, so a resume with a scanned
header and a real second page keeps the good text.

Only PDFs are routed here. A DOCX has no pages to rasterise, so one that yielded
no text fails at extraction with `no_text_extracted` — the honest answer — rather
than being handed to a PDF renderer that cannot read it.

**OCR being unavailable is a hard failure when the pages it cannot reach carry
most of the document** — strictly more than `MAX_UNREAD_PAGE_SHARE` (half) of
its pages — not an empty result. An empty result would produce a confidently
empty resume that the user might then confirm. When they are a minority (a
readable resume with a last page reading "References available on request."),
the run continues without entering `ocr_required`: those pages keep whatever
text they have, are flagged `unread` in their page metadata, an `ocr` event is
recorded as `skipped`, and the review screen lists each one in `unmapped`.
The allowance is for **sparse** pages only: a page with no text at all may be a
whole scanned page — page 1's name, contact block and current role — and
without an engine nothing can tell it from a blank one, so any such page fails
the run as before. A failed `ocr_unavailable` may be retried by hand once an
engine is installed (§10).

A page whose bitmap would exceed `MAX_RENDER_PIXELS` (25 MP) at the configured
scale is rendered at the largest scale that fits, never at full size — PDFium
allocates the whole bitmap from the page's declared size, and a 435-byte PDF
can declare a 3 GB page. It is not skipped: image-to-PDF tools size the page in
pixels at 72 dpi, so an ordinary 300 dpi A4 scan is 35 MP at scale 2, and
skipping it left the run with no text at all. Only a page with no renderable
size is flagged unread and reported.

## 6. AI structuring boundary

`app/services/resume/structuring.py`. `ResumeStructurer` takes an
`ExtractedDocument` and returns a validated `ResumeCandidate`. A structurer
cannot touch the database, cannot reach the profile service, and cannot decide
anything about profile versions. Whatever it returns is a proposal.

- `DeterministicResumeStructurer` — heuristics, no network. **The default**, so a
  clean checkout, CI and the whole test suite run with no API key and no
  non-determinism.
- `AnthropicResumeStructurer` — Claude via `messages.parse()`, with the candidate
  model passed as `output_format`: the SDK turns the type into both the schema it
  sends and the validator it runs on the reply. The model is never asked to
  "return JSON" and then parsed hopefully; a malformed reading is an exception at
  the boundary rather than a mystery three layers downstream.

Enabled by `RESUME_AI_ENABLED`, which defaults to **false**.

The deterministic structurer's section headings are whole lines — so "Project
Manager" is a job title, not a Projects heading — drawn from a closed, curated
vocabulary that includes the multi-word forms real resumes use ("Academic
Profile", "Awards & Recognition", "Languages Known", "Certification Courses",
"Summary of Experience", "References available upon request"). Decoration after
the words ("EXPERIENCE ——", "EDUCATION ____", "Projects •", a trailing colon or
the " | " of an empty table cell), ornament on both ends ("— SKILLS —") and a
continuation suffix ("Experience (continued)", "(Contd.)") are not part of the
label. Two headings side by side on one line — "EXPERIENCE SKILLS", which is
how PDFium joins two columns' headings, or "EXPERIENCE | SKILLS" from a Word
table — open the first. A heading word followed by the other column's CONTENT
at a narrow gap ("EXPERIENCE AWS Certified") is the same shape as a job title
and stays content; only a gap of three or more spaces or a tab separates it.

The document is sent inside a `<resume>` fence, and the system prompt says
that everything inside it is untrusted data, never instructions. Any `<page`,
`</page`, `<resume` or `</resume` in the document's own text is neutralised
(`<` becomes `‹`) by `ExtractedDocument.text()`, so a resume cannot close the
fence and speak as the prompt. The model is told to quote exactly, so what it
returns is restored (`extraction.restore_fences`) before anything is stored or
verified: a faithful quote of "<Page />" is verified against the document's own
text, and no "‹" the resume never had reaches the draft. `PROMPT_VERSION`
2026-09-25.1 (the wording the model sees is unchanged).

## 7. Provenance

`app/schemas/resume_candidate.py`. Every candidate item carries
`provenance: [{page, quote, confidence, verified}]`.

Citations are **verified, not trusted**. After the model returns, each quote is
searched for in the page it cites (whitespace-normalised, nothing else relaxed).
A quote that is not there is marked `verified: false` and shown to the user
rather than deleted — the reviewer should see that the machine could not support
its own claim. That check doubles as a hallucination detector.

Spans are quotes, not character offsets: models cannot count characters, and
offsets would be silently wrong.

Provenance lives on `resume_structured_results`, **not** on the profile
snapshot. The snapshot's collections are re-sorted on read, so any index-based
pointer into them would silently rot. The review UI reads provenance from the
structured result.

**Date precision.** A `CandidateDate` is `{raw, value, precision}`. "May 2024"
becomes `raw: "May 2024"`, `value: 2024-05-01`, `precision: "month"` — and the
UI displays `raw`, never the normalised value. The profile domain's existing
paired precision columns carry this through to the draft.

## 8. Profile integration

An AI-extracted version is **always a draft**. It is created through the
existing profile service via `create_draft_version_with_snapshot`, which was
added so the draft and its snapshot land in **one transaction** — calling
`create_draft_version` then `replace_snapshot` would be two commits, and a
failure between them would leave an empty draft with a version number
permanently burnt.

Drafts are stamped `created_by_type='ai'`. The existing confirmation flow,
immutability triggers and edit rules apply unchanged.

**Items that cannot be mapped are reported, not dropped.** `experiences` requires
a company, a title and a start date; an experience the resume never dated cannot
be stored, and inventing a date to make it insertable would defeat the whole
design. Those land in `resume_structured_results.unmapped` with a reason, and
the review screen lists them.

`unmapped` gathers four sources, in this order: pages the run could not fully
read (§5, and pages past the page limit); sections the structurer recognised
and has nowhere to put — coursework, volunteer work, awards, publications and
the like — quoted back rather than guessed into another section, together with
a column of a one-line role ("Software Engineer | Infosys | Bangalore") that is
neither a recognisable "City, Region" location nor an employment type; hyperlinks on
the document that the reading placed on neither the contact record nor a
project (`structuring.unplaced_links`, checked against the document whichever
structurer ran); and items the snapshot cannot hold. A project's own links are
mapped onto `ProjectItem.links`; a project technology longer than the column's
128 characters is reported, never truncated.

Skills are de-duplicated case-insensitively before insert, because the snapshot
has a `lower(name)` unique index and a resume does not.

## 9. Duplicate uploads

`UNIQUE (user_id, content_sha256)`. Identical bytes from the same user resolve
to the existing document and the endpoint returns **200**; a new document
returns **201**.

- This makes the endpoint **idempotent**: a client retrying after a network
  timeout gets its own resume back, not a second copy. It is safe because the
  artifact is immutable, so "the same content" really is "the same document".
- Scope is **per user, not global**. A global hash index would let one user's
  upload fail because a stranger uploaded the same file — an existence oracle
  dressed up as a conflict.
- **200 says nothing about processing.** The existing document's latest run may
  have failed; the client decides whether to call `reprocess`.

A concurrent duplicate that loses the race between the `SELECT` and the `INSERT`
is recovered through a `SAVEPOINT` — a constraint violation aborts the
transaction, and nothing could be read afterwards without one.

## 10. Failure and retry

A stage failure records `failed_stage`, `error_code` and a message **from our own
vocabulary**. Parser and model exceptions are never stored raw: `pypdf`,
`python-docx` and OCR libraries quote document content in their messages, and
that content is the user's personal data.

`POST /resumes/{id}/reprocess` opens a new run. It is refused with 409 while a
run is still in flight, and after a failure the document itself causes. A
failure the deployment caused (`failures.DEPLOYMENT_ERROR_CODES`: no OCR engine,
a missing, revoked or unpaid provider account, a bug of ours) is never retried
automatically but may be retried by hand — a re-upload of the same bytes
returns the same document, so refusing it left the document wedged for good.
`can_retry` follows the same rule.

## 11. Security

Implemented now:

- Content-sniffed validation (PDF magic bytes; DOCX must be a ZIP containing
  `word/document.xml`). Filename, extension and browser Content-Type are
  cross-checked but never believed.
- Configuration-driven size limit, enforced at the HTTP edge before the body is
  read: `ResumeUploadSizeLimit` (ASGI middleware in `app/main.py`) answers 413
  to an upload whose Content-Length exceeds the limit plus a multipart margin,
  without receiving a byte of it, and cuts off a chunked body once it passes
  that. The handler's own count is the exact check on the file itself — on its
  own it ran only after FastAPI had parsed, and spooled, the whole form.
- Storage keys derived from the content hash; a regex plus a root-containment
  check on every read and write, so a key from the database is still validated —
  a database is not a trust boundary.
- No filesystem paths in any API response; `storage_key` is never serialised.
- Uploads are never executed. Nothing opens the file during validation; parsing
  happens later against already-accepted bytes.
- Ownership scoped in the query, so another user's document is a 404 rather than
  a 403.
- Error messages from a fixed vocabulary, so PII cannot leak through them.

**Not yet done, and required before this is production-secure:** antivirus/CDR
scanning of uploads; a retention sweeper that actually purges page text and
payloads on a schedule (the columns are nullable for it, but nothing runs);
encryption at rest beyond whatever the filesystem provides; per-user upload rate
limiting; an audited erasure path (the profile domain's immutability triggers
currently make hard deletion of a confirmed version impossible); and real
authentication, which is still the dev-header seam.

## 12. Migration paths

**Azure Blob Storage.** Add `AzureBlobStorage` satisfying `StorageBackend` and
select it in `get_storage()`. No resume-processing code changes. Keys are
already opaque and content-addressed, which maps directly onto blob names.

**Azure AI Document Intelligence.** Add a class satisfying `OcrEngine` and
return it from `get_ocr_engine()`. Because the engine takes image bytes and
returns text, nothing else moves.

**Temporal / Event Hubs / a worker.** The seam is
`process_run(db, *, run_id: int)`. It takes an integer and re-reads everything
else from the database; nothing is passed in memory from the request, and no
lock is held across the model call. A task body is that one call. The API would
commit the run in `uploaded` and enqueue instead of calling the pipeline inline.
What would then need adding: a lease/heartbeat column pair and a reaper for
stranded runs, and — only then — a uniqueness rule over active runs.

## 13. Configuration

All in `app/core/config.py`: `RESUME_STORAGE_ROOT`, `RESUME_MAX_UPLOAD_BYTES`,
`RESUME_OCR_MIN_CHARS_PER_PAGE`, `RESUME_OCR_ENABLED`,
`RESUME_OCR_RENDER_SCALE`, `RESUME_AI_ENABLED`, `RESUME_AI_MODEL`,
`RESUME_AI_MAX_TOKENS`, `ANTHROPIC_API_KEY`. The resource bounds on parsing a
document (`MAX_PDF_PAGES`, `MAX_DOCX_*`, `MAX_UNREAD_PAGE_SHARE` in
`extraction.py`; `MAX_RENDER_PIXELS` in `ocr.py`) are named module constants
rather than settings: they bound hostile input, not deployment behaviour.

## 14. Observability

`resume_processing_events` holds one row per stage per run with an outcome and a
duration, which answers: which resume was processed, when it started, which
stage failed, how long each stage took, what the error code was, and which
profile version was produced (`resume_processing_runs.profile_version_id`).

These are rows rather than log lines because those questions are queries, and a
log search is not a query. Structured log records are emitted alongside them
with the run and document ids attached, and never the document's content.
