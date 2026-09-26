/**
 * The extension against the contract the API publishes.
 *
 * Types are already checked by the compiler (they are the generated ones).
 * What the compiler cannot see — lengths, the closed set of sources, which keys
 * the API refuses — is read here from packages/shared/openapi.json, the file
 * `npm run api:types` regenerates from the running API's schema. A backend
 * change that would make the extension send something the API refuses fails
 * this test first.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { FIELD_LIMITS, MAX_SOURCE_URL_LENGTH } from "../src/contract";
import { ADAPTERS } from "../src/extraction/registry";
import { buildPayload, draftFrom } from "../src/review/payload";
import { JOB_URL } from "./support/fixtures";

const REPO = resolve(__dirname, "../../..");

interface JsonSchema {
  type?: string;
  maxLength?: number;
  enum?: string[];
  anyOf?: JsonSchema[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean;
}

const openapi = JSON.parse(readFileSync(resolve(REPO, "packages/shared/openapi.json"), "utf8")) as {
  components: { schemas: Record<string, JsonSchema> };
  paths: Record<string, Record<string, unknown>>;
};
const schema = openapi.components.schemas.JobIngestionRequest as JsonSchema;

function stringSchema(property: JsonSchema | undefined): JsonSchema | undefined {
  if (!property) return undefined;
  if (property.type === "string") return property;
  return property.anyOf?.find((option) => option.type === "string");
}

describe("the published JobIngestionRequest", () => {
  it("still exists, and POST /api/v1/jobs still takes it", () => {
    expect(schema).toBeDefined();
    expect(openapi.paths["/api/v1/jobs"]?.post).toBeDefined();
  });

  it.each(Object.entries(FIELD_LIMITS))("limits %s to the length the API publishes (%d)", (field, limit) => {
    expect(stringSchema(schema.properties?.[field])?.maxLength).toBe(limit);
  });

  it("has the source type every adapter sends", () => {
    const sources = schema.properties?.source_type?.enum ?? [];
    for (const adapter of ADAPTERS) expect(sources).toContain(adapter.sourceType);
  });

  it("accepts every key the extension sends, and nothing is missing that it requires", () => {
    const job = {
      source_type: "greenhouse" as const,
      source_url: JOB_URL,
      external_job_id: "4012345002",
      job_title: "Senior Backend Engineer",
      company_name: null,
      location_raw: null,
      description_text: "Text",
    };
    const { payload } = buildPayload(job, draftFrom(job));
    const allowed = Object.keys(schema.properties ?? {});

    // additionalProperties: false is Pydantic's extra="forbid": an unknown key is a 422.
    expect(schema.additionalProperties).toBe(false);
    for (const key of Object.keys(payload)) expect(allowed).toContain(key);
    for (const key of schema.required ?? []) expect(payload).toHaveProperty(key);
  });

  it("does not let a client claim a user — there is no such field to send", () => {
    const properties = Object.keys(schema.properties ?? {});
    expect(properties.filter((key) => /user|owner|email|status/.test(key))).toEqual([]);
  });

  it("mirrors the URL limit the API enforces in its validator", () => {
    // Not expressible in the OpenAPI document, so read from where it is defined.
    const source = readFileSync(resolve(REPO, "services/api/app/schemas/job.py"), "utf8");
    const match = /^_MAX_URL_LENGTH\s*=\s*([\d_]+)/m.exec(source);
    expect(match).not.toBeNull();
    expect(Number(match?.[1]?.replace(/_/g, ""))).toBe(MAX_SOURCE_URL_LENGTH);
  });
});
