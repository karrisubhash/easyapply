/**
 * Pages of the EasyApply web app the popup links to.
 *
 * Opened in a new tab, where the web app's own session decides who is looking —
 * exactly as it does for the popup's requests. A link carries only ids the
 * web app already showed this person; the web app resolves them in that
 * person's account, so a link cannot show anyone else's job, match or resume.
 */

import { isId } from "./calls";

function page(webUrl: string, path: string): string {
  return new URL(path, webUrl).href;
}

function id(value: number): number {
  if (!isId(value)) throw new RangeError(`not an id: ${String(value)}`);
  return value;
}

export const webLinks = {
  job: (webUrl: string, jobId: number) => page(webUrl, `/jobs/${id(jobId)}`),
  match: (webUrl: string, jobId: number, matchId?: number) =>
    page(webUrl, `/jobs/${id(jobId)}/match${matchId === undefined ? "" : `?match=${id(matchId)}`}`),
  tailor: (webUrl: string, jobId: number) => page(webUrl, `/jobs/${id(jobId)}/tailor`),
  tailoredResume: (webUrl: string, runId: number) => page(webUrl, `/tailored-resumes/${id(runId)}`),
  /**
   * The file itself, through the web app's proxy. A download: opening it in a
   * tab lets the browser save it, with the filename the API chose. The
   * browser's session goes with it (a top-level navigation carries the
   * web app's SameSite=Lax cookie); the extension never fetches the bytes.
   */
  artifact: (webUrl: string, runId: number) => page(webUrl, `/api/backend/tailored-resumes/${id(runId)}/artifact`),
  profile: (webUrl: string) => page(webUrl, "/profile"),
  profileVersions: (webUrl: string) => page(webUrl, "/profile/versions"),
  resume: (webUrl: string) => page(webUrl, "/resume"),
};
