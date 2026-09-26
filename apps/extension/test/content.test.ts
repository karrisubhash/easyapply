// @vitest-environment-options {"url": "https://job-boards.greenhouse.io/acmerobotics/jobs/4012345002?gh_src=abc#app"}

/**
 * src/content/index.ts itself, in a document at a Greenhouse posting's address:
 * it answers the popup's extract request with the reading of the page it is in,
 * ignores anything else, and leaves the page as it found it.
 */

import { EXTRACT } from "../src/shared/messages";
import { createFakeChrome, EXTENSION_ID } from "./support/fake-chrome";
import { fixtureHtml, parseDocument } from "./support/fixtures";

type Listener = (message: unknown, sender: { id?: string }, sendResponse: (value: unknown) => void) => unknown;

let listener: Listener;

beforeAll(async () => {
  const fixture = parseDocument(fixtureHtml("greenhouse-job.html"));
  document.title = fixture.title;
  document.body.replaceChildren(...[...fixture.body.childNodes].map((node) => document.importNode(node, true)));

  const fake = createFakeChrome([]);
  const listeners: Listener[] = [];
  vi.stubGlobal("chrome", {
    ...fake.chrome,
    runtime: { ...fake.chrome.runtime, onMessage: { addListener: (fn: Listener) => listeners.push(fn) } },
  });
  await import("../src/content/index");
  expect(listeners).toHaveLength(1);
  listener = listeners[0]!;
});

afterAll(() => {
  vi.unstubAllGlobals();
});

function ask(message: unknown, sender: { id?: string } = { id: EXTENSION_ID }): unknown {
  let answer: unknown = "no answer";
  listener(message, sender, (value) => {
    answer = value;
  });
  return answer;
}

describe("the content script", () => {
  it("answers an extract request with the page it is in", () => {
    const answer = ask({ type: EXTRACT }) as { status: string; job: Record<string, unknown> };
    expect(answer.status).toBe("extracted");
    expect(answer.job).toMatchObject({
      source_url: "https://job-boards.greenhouse.io/acmerobotics/jobs/4012345002?gh_src=abc",
      external_job_id: "4012345002",
      job_title: "Senior Backend Engineer",
      company_name: "Acme Robotics",
    });
  });

  it("does not answer anyone but its own extension", () => {
    expect(ask({ type: EXTRACT }, { id: "abcdefghijklmnopabcdefghijklmnop" })).toBe("no answer");
    expect(ask({ type: EXTRACT }, {})).toBe("no answer");
  });

  it("does not answer other messages", () => {
    expect(ask({ type: "easyapply/submit", tabId: 1, payload: {} })).toBe("no answer");
  });

  it("leaves the page exactly as it was", () => {
    const before = document.documentElement.outerHTML;
    ask({ type: EXTRACT });
    expect(document.documentElement.outerHTML).toBe(before);
  });
});
