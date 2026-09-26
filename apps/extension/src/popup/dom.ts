/**
 * Building the popup's DOM — the only way any screen here creates nodes.
 *
 * A string child becomes a text node, never markup: job postings, requirement
 * text, match explanations and profile values are all shown through this, and
 * all of them are text that somebody other than this extension wrote. Nothing
 * in the popup uses innerHTML.
 */

export type Child = Node | string | null | undefined | false;

export function element<K extends keyof HTMLElementTagNameMap>(
  doc: Document,
  tag: K,
  attributes: Record<string, string | undefined> = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === undefined) continue;
    if (name === "class") node.className = value;
    else node.setAttribute(name, value);
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    // A string becomes a text node. Never markup.
    node.append(child);
  }
  return node;
}

export interface NoticeLink {
  href: string;
  text: string;
  id: string;
}

export interface Notice {
  tone: "success" | "info" | "error";
  title: string;
  lines: string[];
  /** A link under the lines — to the saved job, or to sign in. */
  link?: NoticeLink;
}

export function noticeElement(doc: Document, notice: Notice, id?: string): HTMLElement {
  return element(
    doc,
    "div",
    { class: `notice notice--${notice.tone}`, id, role: notice.tone === "error" ? "alert" : "status" },
    element(doc, "p", { class: "notice__title" }, notice.title),
    ...notice.lines.map((line) => element(doc, "p", {}, line)),
    notice.link ? element(doc, "p", {}, linkElement(doc, notice.link)) : null,
  );
}

/** A link that opens in a new tab and tells the destination nothing about where it came from. */
export function linkElement(doc: Document, link: NoticeLink): HTMLAnchorElement {
  return element(doc, "a", { href: link.href, target: "_blank", rel: "noreferrer", id: link.id }, link.text);
}
