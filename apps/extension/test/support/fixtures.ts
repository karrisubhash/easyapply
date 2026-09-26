import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const FIXTURES = resolve(__dirname, "../fixtures");

export const JOB_URL = "https://job-boards.greenhouse.io/acmerobotics/jobs/4012345002";
export const BOARD_URL = "https://job-boards.greenhouse.io/acmerobotics";

export function fixtureHtml(name: string): string {
  return readFileSync(resolve(FIXTURES, name), "utf8");
}

/** A fresh, inert document: DOMParser never runs a fixture's scripts. */
export function parseDocument(html: string): Document {
  return new DOMParser().parseFromString(html, "text/html");
}

export function jobDocument(): Document {
  return parseDocument(fixtureHtml("greenhouse-job.html"));
}

export function boardDocument(): Document {
  return parseDocument(fixtureHtml("greenhouse-board.html"));
}

/**
 * The description of greenhouse-job.html, written out by hand from the
 * fixture's markup before the reader was run against it — what a person
 * copying the posting would expect, in the shape the job structurer reads.
 */
export const EXPECTED_DESCRIPTION = [
  "Acme Robotics builds warehouse robots. We are a small team with big ambitions.",
  "",
  "This paragraph",
  "has a line break.",
  "",
  "What you'll do",
  "",
  "- Design and operate Python services on PostgreSQL.",
  "- Own the job-scheduling API end to end.",
  "- Mentor engineers",
  "  - through code review",
  "  - through pairing",
  "",
  "What you'll bring",
  "",
  "- Experience - 5+ years of backend development.",
  "- Strong knowledge of SQL and FastAPI.",
  "",
  "Nice to have",
  "",
  "1. Kubernetes",
  "2. Terraform",
  "",
  "Pay Transparency",
  "",
  "Annual Base Salary Range:",
  "",
  "$150,000 - $180,000 USD",
  "",
  "Acme Robotics is an equal opportunity employer.",
].join("\n");
