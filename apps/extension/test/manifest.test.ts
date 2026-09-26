// @vitest-environment node

import { createHash } from "node:crypto";

import { buildManifest, parseWebUrl } from "../scripts/build.mjs";
import { ADAPTERS } from "../src/extraction/registry";
import { EXTENSION_ID } from "./support/fake-chrome";

const manifest = buildManifest("http://localhost:3000");

/** Chrome's rule: SHA-256 of the DER public key, first 32 hex digits, 0-f read as a-p. */
function extensionIdOf(base64Key: string): string {
  const digest = createHash("sha256").update(Buffer.from(base64Key, "base64")).digest("hex").slice(0, 32);
  return [...digest].map((digit) => String.fromCharCode(97 + parseInt(digit, 16))).join("");
}

describe("the manifest", () => {
  it("is Manifest V3", () => {
    expect(manifest.manifest_version).toBe(3);
  });

  it("pins the extension id the documentation and PROXY_ALLOWED_EXTENSION_IDS name", () => {
    expect(extensionIdOf(manifest.key)).toBe(EXTENSION_ID);
  });

  it("injects the content script on exactly the sites an adapter reads", () => {
    const matches = manifest.content_scripts.flatMap((script) => script.matches).sort();
    expect(matches).toEqual(ADAPTERS.flatMap((adapter) => adapter.matches).sort());
  });

  it("asks for storage and nothing broader — no tabs, scripting, cookies or all-sites access", () => {
    expect(manifest.permissions).toEqual(["storage"]);
    expect(manifest).not.toHaveProperty("optional_permissions");
    expect(manifest.host_permissions).not.toContain("<all_urls>");
  });

  it("can reach the job sites and the one web app it was built for, and no other host", () => {
    expect(manifest.host_permissions).toEqual([
      ...ADAPTERS.flatMap((adapter) => adapter.matches),
      "http://localhost:3000/*",
    ]);
  });

  it("exposes nothing to web pages", () => {
    expect(manifest).not.toHaveProperty("web_accessible_resources");
    expect(manifest).not.toHaveProperty("externally_connectable");
  });
});

describe("the web app address a build is pointed at", () => {
  it.each([
    ["http://localhost:3000", "http://localhost:3000"],
    ["http://127.0.0.1:3100/", "http://127.0.0.1:3100"],
    ["https://app.easyapply.example", "https://app.easyapply.example"],
  ])("accepts the origin %s", (input, origin) => {
    expect(parseWebUrl(input)).toBe(origin);
  });

  it.each(["localhost:3000", "ftp://localhost", "http://localhost:3000/app", "http://localhost:3000?x=1", "http://user:pw@localhost:3000"])(
    "refuses %s",
    (input) => {
      expect(() => parseWebUrl(input)).toThrow();
    },
  );
});
