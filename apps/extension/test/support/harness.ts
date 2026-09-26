/**
 * The extension's real entry points, run against a fake Chrome and a fake web
 * app — the harness the workflow and application tests share with
 * test/flow.test.ts's approach.
 *
 * `launch` loads src/background/index.ts as Chrome would; `openPopup` loads
 * src/popup/index.ts into a fresh root, as clicking the toolbar button does.
 */

// The module graph the entry points load, imported statically so it is
// transformed while this file is collected — not inside the first test's time
// budget. (Each test still loads the entry points fresh with vi.resetModules.)
import "../../src/popup/app";
import "../../src/background/service";

import { createFakeApi, CONFIGURED, type FakeApi, type FakeSession } from "./fake-api";
import { createFakeChrome, type FakeTab } from "./fake-chrome";
import { JOB_URL, jobDocument } from "./fixtures";

export interface Harness {
  api: FakeApi;
  fake: ReturnType<typeof createFakeChrome>;
  tab: FakeTab;
}

export async function launch({
  session = CONFIGURED,
  tabs,
}: { session?: FakeSession; tabs?: FakeTab[] } = {}): Promise<Harness> {
  vi.resetModules();
  const allTabs = tabs ?? [{ id: 11, url: JOB_URL, document: jobDocument() }];
  const fake = createFakeChrome(allTabs, { activeTabId: allTabs[0]?.id });
  const api = createFakeApi({ session });
  vi.stubGlobal("chrome", fake.chrome);
  vi.stubGlobal("fetch", api.fetch);
  await import("../../src/background/index");
  return { api, fake, tab: allTabs[0] as FakeTab };
}

export async function openPopup(): Promise<HTMLElement> {
  const root = document.createElement("main");
  root.id = "app";
  document.body.replaceChildren(root);
  vi.resetModules();
  await import("../../src/popup/index");
  await vi.waitFor(() => {
    if (!root.querySelector("#review, #blocked")) throw new Error("still reading the page");
  });
  return root;
}

export function byId<T extends HTMLElement = HTMLElement>(root: ParentNode, id: string): T {
  const node = root.querySelector<T>(`#${id}`);
  if (!node) throw new Error(`no #${id}`);
  return node;
}

export function text(root: ParentNode, id: string): string {
  return (root.querySelector(`#${id}`)?.textContent ?? "").replace(/\s+/g, " ").trim();
}

/**
 * Wait until #id exists and its text matches.
 *
 * Under fake timers, time is moved forward in steps of `stepMs` between checks,
 * so a poll that backs off over minutes of (fake) time finishes in
 * milliseconds; `within` bounds how much fake time may pass. Under real timers
 * it simply re-checks.
 */
export async function waitForText(
  root: ParentNode,
  id: string,
  pattern: RegExp,
  within = 20_000,
  stepMs = 250,
): Promise<string> {
  let elapsed = 0;
  for (;;) {
    const seen = text(root, id);
    if (pattern.test(seen)) return seen;
    if (elapsed > within) throw new Error(`#${id} says ${JSON.stringify(seen)} after ${elapsed} ms`);
    if (vi.isFakeTimers()) {
      await vi.advanceTimersByTimeAsync(stepMs);
    } else {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    elapsed += vi.isFakeTimers() ? stepMs : 20;
  }
}

export async function save(root: HTMLElement): Promise<void> {
  byId<HTMLButtonElement>(root, "save").click();
  await waitForText(root, "outcome-notice", /Saved to EasyApply|Already in EasyApply/);
}
