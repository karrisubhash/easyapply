/**
 * Turning page markup into the text EasyApply stores.
 *
 * Two readers, for two kinds of field:
 *
 *   - `cleanLabel` for one-line values (a title, a company, a location). It is
 *     the same rule the API applies (`_clean_label` in
 *     services/api/app/schemas/job.py): control characters dropped, every run
 *     of whitespace collapsed to one space. Applying it here too is what makes
 *     the review screen show the value the API will actually store.
 *   - `blockText` for the description. It walks the DOM rather than reading
 *     `innerText`, which depends on layout (jsdom has none, and a hidden tab
 *     can report a different answer), and it writes the shape the job
 *     structurer already reads: a heading on its own line, a blank line
 *     between paragraphs, and `- ` / `1. ` in front of each list item (see
 *     `_BULLET_PREFIX` in services/api/app/services/job/structuring.py).
 *
 * Both only READ the DOM. Nothing here writes to the page, clicks, or fills
 * anything in.
 */

/** Collapse whitespace and drop control characters from a one-line value. */
export function cleanLabel(value: string | null | undefined): string {
  if (!value) return "";
  let kept = "";
  for (const char of value) {
    // The API keeps \n and \t here only to collapse them with everything else.
    if ((char.codePointAt(0) ?? 0) >= 0x20 || char === "\n" || char === "\t") kept += char;
  }
  return kept.split(/\s+/).filter(Boolean).join(" ");
}

/**
 * Tidy multi-line text without changing what it says.
 *
 * Line endings unified, C0 controls other than newline and tab dropped (the API
 * strips those too), trailing spaces removed, runs of blank lines reduced to
 * one, and the ends trimmed. Words, punctuation and line structure are left
 * exactly as they were.
 */
export function cleanMultiline(value: string | null | undefined): string {
  if (!value) return "";
  let kept = "";
  for (const char of value.replace(/\r\n?/g, "\n")) {
    const code = char.codePointAt(0) ?? 0;
    if (code >= 0x20 || char === "\n" || char === "\t") kept += char === " " ? " " : char;
  }
  return kept
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** Elements whose content is never part of the posting's text. */
const SKIPPED = new Set([
  "script",
  "style",
  "noscript",
  "template",
  "svg",
  "math",
  "button",
  "form",
  "input",
  "select",
  "textarea",
  "iframe",
  "object",
  "embed",
  "canvas",
  "video",
  "audio",
  "img",
  "picture",
]);

/** Blocks that stand apart from their neighbours by a blank line. */
const PARAGRAPH_BLOCKS = new Set([
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "blockquote",
  "table",
  "dl",
  "figure",
  "hr",
]);

/** Blocks that only need to start on a line of their own. */
const LINE_BLOCKS = new Set([
  "div",
  "section",
  "article",
  "header",
  "footer",
  "main",
  "aside",
  "nav",
  "tr",
  "dt",
  "dd",
  "figcaption",
  "address",
  "details",
  "summary",
]);

/**
 * Accumulates text, deciding line breaks lazily.
 *
 * A block asks for a break; the break is only written once there is text to
 * put after it. That is what keeps empty wrappers — a Greenhouse description is
 * divs inside divs — from turning into runs of blank lines, and a list item's
 * marker from sitting on a line of its own when the item's text is inside a
 * `<p>`.
 */
class TextWriter {
  private out = "";
  private pendingBreak: 0 | 1 | 2 = 0;
  private pendingMarker: string | null = null;

  /** The next text starts on a new line (1) or after a blank line (2). */
  requestBreak(level: 1 | 2): void {
    if (this.out === "") return;
    if (level > this.pendingBreak) this.pendingBreak = level;
  }

  /** A `<br>`: one ends the line, a second in a row leaves a blank one. */
  lineBreak(): void {
    if (this.out === "") return;
    this.pendingBreak = this.pendingBreak === 0 ? 1 : 2;
  }

  /** The next text begins a list item, after this marker. */
  startItem(marker: string): void {
    this.requestBreak(1);
    this.pendingMarker = marker;
  }

  /** A list item has begun and none of its text has been written yet. */
  get itemPending(): boolean {
    return this.pendingMarker !== null;
  }

  write(raw: string): void {
    let chunk = raw.replace(/\s+/g, " ");
    const lineStart = this.out === "" || this.pendingBreak > 0 || this.pendingMarker !== null;
    if (lineStart || this.out.endsWith(" ")) chunk = chunk.trimStart();
    if (chunk === "") return;

    if (this.pendingBreak > 0) {
      this.out = this.out.trimEnd() + (this.pendingBreak === 2 ? "\n\n" : "\n");
      this.pendingBreak = 0;
    }
    if (this.pendingMarker !== null) {
      this.out += this.pendingMarker;
      this.pendingMarker = null;
    }
    this.out += chunk;
  }

  toString(): string {
    return cleanMultiline(this.out);
  }
}

interface ListFrame {
  ordered: boolean;
  next: number;
}

function isHidden(element: Element): boolean {
  if (element.hasAttribute("hidden")) return true;
  const style = (element as HTMLElement).style;
  return style?.display === "none" || style?.visibility === "hidden";
}

function walk(node: Node, writer: TextWriter, lists: ListFrame[]): void {
  if (node.nodeType === Node.TEXT_NODE) {
    writer.write((node as Text).data);
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;

  const element = node as Element;
  const tag = element.localName;
  if (SKIPPED.has(tag) || isHidden(element)) return;

  const inItem = lists.length > 0;
  const children = () => element.childNodes.forEach((child) => walk(child, writer, lists));

  if (tag === "br") {
    writer.lineBreak();
    return;
  }

  if (tag === "ul" || tag === "ol") {
    const level = inItem ? 1 : 2;
    writer.requestBreak(level);
    const start = tag === "ol" ? Number((element as HTMLOListElement).start) || 1 : 1;
    lists.push({ ordered: tag === "ol", next: start });
    children();
    lists.pop();
    writer.requestBreak(level);
    return;
  }

  if (tag === "li") {
    const frame = lists[lists.length - 1];
    const indent = "  ".repeat(Math.max(0, lists.length - 1));
    const marker = frame?.ordered ? `${frame.next++}. ` : "- ";
    writer.startItem(indent + marker);
    children();
    writer.requestBreak(1);
    return;
  }

  if (tag === "pre") {
    if (!writer.itemPending) writer.requestBreak(inItem ? 1 : 2);
    for (const line of (element.textContent ?? "").split(/\r?\n/)) {
      writer.write(line);
      writer.requestBreak(1);
    }
    writer.requestBreak(inItem ? 1 : 2);
    return;
  }

  if (tag === "td" || tag === "th") {
    if (element.previousElementSibling) writer.write(" | ");
    children();
    return;
  }

  if (PARAGRAPH_BLOCKS.has(tag) || LINE_BLOCKS.has(tag)) {
    const level = PARAGRAPH_BLOCKS.has(tag) && !inItem ? 2 : 1;
    if (!writer.itemPending) writer.requestBreak(level);
    children();
    writer.requestBreak(level);
    return;
  }

  children();
}

/** The readable text of an element, with its paragraph and list structure. */
export function blockText(element: Element | null | undefined): string {
  if (!element) return "";
  const writer = new TextWriter();
  walk(element, writer, []);
  return writer.toString();
}

/** The one-line text of an element: `blockText`, with its lines joined. */
export function labelText(element: Element | null | undefined): string {
  return cleanLabel(blockText(element));
}
