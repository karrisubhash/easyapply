/**
 * Every request to the web app's proxy carries the tag of the account it is for.
 *
 * Two halves. The source half reads every file under src/ with the TypeScript
 * parser and fails if anything but `fetchBackend` (src/api/client.ts) — the
 * one helper that attaches `X-EasyApply-Session` — could send a request to
 * /api/backend: any other call of a fetch-like function, any other use of
 * `fetch` as a value, any other network API, any other place a backend URL is
 * written. The behaviour half sends every call the popup can ask for, and a
 * save, and checks the header on each.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import ts from "typescript";

import { isApiCall, prepare, type ApiCall } from "../src/api/calls";
import { apiRequest, fetchBackend } from "../src/api/client";
import { submitJob } from "../src/api/jobs";
import { JOB_URL } from "./support/fixtures";

const SRC = resolve(__dirname, "../src");

function sourceFiles(dir = SRC): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return entry.name.endsWith(".ts") ? [path] : [];
  });
}

/** What calls something network-shaped, and from where. */
interface NetworkUse {
  file: string;
  /** The called name, or `value:fetch` for `fetch` used as a value. */
  what: string;
  /** The nearest named function around it. */
  within: string | null;
  firstArgument: string;
}

const NETWORK = /fetch|xmlhttprequest|sendbeacon|websocket|eventsource/i;

function calleeName(expression: ts.Expression): string | null {
  if (ts.isIdentifier(expression)) return expression.text;
  if (ts.isPropertyAccessExpression(expression)) return expression.name.text;
  if (ts.isElementAccessExpression(expression)) return expression.argumentExpression.getText();
  return null;
}

function functionName(node: ts.Node): string | null {
  if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.name && ts.isIdentifier(node.name)) return node.name.text;
  if (
    ts.isVariableDeclaration(node) &&
    ts.isIdentifier(node.name) &&
    node.initializer &&
    (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))
  ) {
    return node.name.text;
  }
  return null;
}

/** Whether an identifier `fetch` is not a use of the global as a value: a property name, a type, a key. */
function isNameOnly(node: ts.Identifier): boolean {
  const parent = node.parent;
  if (ts.isPropertyAccessExpression(parent) && parent.name === node) return true;
  if (ts.isBindingElement(parent) && parent.propertyName === node) return true;
  if ((ts.isPropertySignature(parent) || ts.isPropertyAssignment(parent) || ts.isMethodDeclaration(parent)) && parent.name === node) return true;
  if (ts.isTypeQueryNode(parent) || ts.isQualifiedName(parent)) return true;
  return false;
}

function networkUses(file: string, text: string): NetworkUse[] {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
  const uses: NetworkUse[] = [];
  const visit = (node: ts.Node, within: string | null) => {
    const inner = functionName(node) ?? within;
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const name = calleeName(node.expression);
      if (name && NETWORK.test(name)) {
        uses.push({ file, what: name, within: inner, firstArgument: node.arguments?.[0]?.getText(source) ?? "" });
      }
    }
    if (ts.isIdentifier(node) && node.text === "fetch" && !isNameOnly(node) && !(ts.isCallExpression(node.parent) && node.parent.expression === node)) {
      // `fetch` as a value: only ever the default transport in an options parameter (`fetch: fetchImpl = fetch`).
      const parent = node.parent;
      const isDefault = ts.isBindingElement(parent) && parent.initializer === node;
      uses.push({ file, what: isDefault ? "default:fetch" : "value:fetch", within: inner, firstArgument: "" });
    }
    ts.forEachChild(node, (child) => visit(child, inner));
  };
  visit(source, null);
  return uses;
}

/** The uses allowed: the helper's own call, calls of the helper, and the session question (not /api/backend). */
function allowed(use: NetworkUse): boolean {
  if (use.what === "fetchBackend") return true;
  if (use.what === "default:fetch") return ["api/client.ts", "api/jobs.ts", "api/session.ts"].includes(use.file);
  if (use.file === "api/client.ts" && use.within === "fetchBackend" && use.what === "fetchImpl") return true;
  if (use.file === "api/session.ts" && use.within === "getSession" && use.what === "fetchImpl") {
    return use.firstArgument === "sessionEndpoint(webUrl)";
  }
  return false;
}

function violations(file: string, text: string): NetworkUse[] {
  return networkUses(file, text).filter((use) => !allowed(use));
}

/** The string and template literals in a file (comments are not code). */
function literals(file: string, text: string): string[] {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isStringLiteralLike(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      found.push(node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

const FILES = sourceFiles().map((path) => ({ file: relative(SRC, path).replace(/\\/g, "/"), text: readFileSync(path, "utf8") }));

describe("no request reaches /api/backend except through fetchBackend", () => {
  it("reads the whole source tree", () => {
    expect(FILES.length).toBeGreaterThan(20);
    expect(FILES.map(({ file }) => file)).toEqual(expect.arrayContaining(["api/client.ts", "api/jobs.ts", "background/service.ts", "popup/app.ts"]));
  });

  it("finds the network uses it knows of — so a pass is not a scanner that sees nothing", () => {
    const uses = FILES.flatMap(({ file, text }) => networkUses(file, text));
    expect(uses).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ file: "api/client.ts", within: "fetchBackend", what: "fetchImpl" }),
        expect.objectContaining({ file: "api/client.ts", within: "apiRequest", what: "fetchBackend" }),
        expect.objectContaining({ file: "api/jobs.ts", within: "submitJob", what: "fetchBackend" }),
        expect.objectContaining({ file: "api/session.ts", within: "getSession", what: "fetchImpl" }),
      ]),
    );
  });

  it("finds nothing else in src/", () => {
    expect(FILES.flatMap(({ file, text }) => violations(file, text))).toEqual([]);
  });

  it.each([
    ["a direct fetch", `export async function load(web: string) { return fetch(web + "/api/backend/jobs"); }`],
    ["a method-style fetch", `export async function load(web: string) { return globalThis.fetch(web + "/api/backend/jobs"); }`],
    ["an injected fetch outside the helper", `export async function load(fetchImpl: typeof fetch) { return fetchImpl("/api/backend/jobs"); }`],
    ["fetch passed around as a value", `const send = fetch; export const x = send;`],
    ["another network API", `export function load() { return new XMLHttpRequest(); }`],
    ["a beacon", `export function ping() { navigator.sendBeacon("/api/backend/jobs"); }`],
  ])("would catch %s", (_what, text) => {
    expect(violations("popup/rogue.ts", text).length).toBeGreaterThan(0);
  });

  it("would catch the session route's fetch pointed anywhere else", () => {
    const text = `export async function getSession({ fetch: fetchImpl = fetch }) { return fetchImpl(backendUrl(webUrl, "/jobs")); }`;
    expect(violations("api/session.ts", text)).toEqual([expect.objectContaining({ what: "fetchImpl", within: "getSession" })]);
  });

  it("writes a backend URL in one place, plus the download link (a navigation, not a request)", () => {
    // client.ts builds every backend URL (backendUrl); links.ts is the artifact <a href>.
    const writers = FILES.filter(({ file, text }) => literals(file, text).some((literal) => literal.includes("/api/backend")));
    expect(writers.map(({ file }) => file).sort()).toEqual(["api/client.ts", "api/links.ts"]);
    expect(literals("x.ts", "const a = `http://h/api/backend/${1}`; // /api/backend in a comment")).toEqual(["http://h/api/backend/", ""]);
  });
});

// --- the behaviour ------------------------------------------------------------------

const WEB = "http://localhost:3000";
const TAG = "Tag-of_account-A";

function recording() {
  const seen: { url: string; headers: Headers }[] = [];
  const fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    seen.push({ url: String(input), headers: new Headers(init.headers) });
    return new Response("{}", { status: 404, headers: { "Content-Type": "application/json" } });
  }) as typeof globalThis.fetch;
  return { fetch, seen };
}

const EVERY_CALL: ApiCall[] = [
  { op: "requirements", jobId: 1 },
  { op: "profileVersions" },
  { op: "currentProfileVersion" },
  { op: "matches", jobId: 2 },
  { op: "match", matchId: 3 },
  { op: "createMatch", jobId: 4, profileVersionId: 5 },
  { op: "tailoringRuns", jobId: 6 },
  { op: "tailoringRun", runId: 7 },
  { op: "startTailoring", jobId: 8, profileVersionId: 9, matchResultId: null },
];

describe("the tag on the wire", () => {
  it("covers every call the popup can ask for", () => {
    const ops = new Set(EVERY_CALL.map((call) => call.op));
    // A new op added to src/api/calls.ts without being added here fails this.
    for (const op of ["requirements", "profileVersions", "currentProfileVersion", "matches", "match", "createMatch", "tailoringRuns", "tailoringRun", "startTailoring"]) {
      expect(ops.has(op as ApiCall["op"])).toBe(true);
    }
    expect(isApiCall({ op: "anythingElse" })).toBe(false);
  });

  it.each(EVERY_CALL.map((call) => [call.op, call] as const))("%s carries X-EasyApply-Session", async (_op, call) => {
    const { fetch, seen } = recording();
    await apiRequest(prepare(call), { webUrl: WEB, sessionTag: TAG, fetch });
    expect(seen).toHaveLength(1);
    expect(seen[0]?.url.startsWith(`${WEB}/api/backend/`)).toBe(true);
    expect(seen[0]?.headers.get("x-easyapply-session")).toBe(TAG);
  });

  it("the save carries it too", async () => {
    const { fetch, seen } = recording();
    await submitJob(
      { source_type: "greenhouse", source_url: JOB_URL, job_title: "Engineer", description_text: "Build." },
      { webUrl: WEB, sessionTag: TAG, fetch },
    );
    expect(seen.map((request) => [request.url, request.headers.get("x-easyapply-session")])).toEqual([[`${WEB}/api/backend/jobs`, TAG]]);
  });

  it("no header at all when the account's tag is unknown", async () => {
    const { fetch, seen } = recording();
    await apiRequest(prepare({ op: "profileVersions" }), { webUrl: WEB, sessionTag: null, fetch });
    await fetchBackend(fetch, `${WEB}/api/backend/profile/versions`, { headers: { Accept: "application/json" } }, null);
    for (const request of seen) expect(request.headers.has("x-easyapply-session")).toBe(false);
  });

  it("the helper never lets a caller's own header stand in for the tag, in any letter case", async () => {
    const { fetch, seen } = recording();
    await fetchBackend(fetch, `${WEB}/api/backend/jobs`, { headers: { "X-EasyApply-Session": "forged" } }, TAG);
    await fetchBackend(fetch, `${WEB}/api/backend/jobs`, { headers: { "x-easyapply-session": "forged" } }, TAG);
    await fetchBackend(fetch, `${WEB}/api/backend/jobs`, { headers: { "X-EASYAPPLY-SESSION": "forged" } }, null);
    expect(seen.map((request) => request.headers.get("x-easyapply-session"))).toEqual([TAG, TAG, null]);
  });
});
