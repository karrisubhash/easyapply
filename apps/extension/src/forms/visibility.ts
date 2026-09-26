/**
 * Whether a person could see and use a form control — shared by every form
 * adapter, because "visible" is a property of the page, not of the site.
 *
 * Hidden by an attribute (`hidden`, `aria-hidden="true"`, `inert`) on it or an
 * ancestor, or by `display: none` / `visibility: hidden` on it or an ancestor —
 * computed where the document has a window, inline otherwise (a parsed test
 * page has none). Where the page has layout, a control with no box at all is
 * hidden too; where it has none (a test document), that check is skipped
 * rather than calling everything hidden.
 */
export function isVisible(element: Element): boolean {
  if (element.closest('[hidden], [aria-hidden="true"], [inert]')) return false;
  const view = element.ownerDocument.defaultView;
  for (let node: Element | null = element; node; node = node.parentElement) {
    const style = view ? view.getComputedStyle(node) : (node as HTMLElement).style;
    if (style && (style.display === "none" || style.visibility === "hidden")) return false;
  }
  const hasLayout = element.ownerDocument.body.getClientRects().length > 0;
  return !hasLayout || element.getClientRects().length > 0;
}
