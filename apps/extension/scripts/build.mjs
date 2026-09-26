/**
 * Build the extension into a directory Chrome can load unpacked.
 *
 *   npm run build --workspace @easyapply/extension
 *       -> apps/extension/dist, saving to http://localhost:3000
 *
 *   EASYAPPLY_WEB_URL=http://127.0.0.1:3100 node scripts/build.mjs --out .artifacts/build
 *       -> the same extension, pointed at another EasyApply web app
 *
 * The web app's origin is fixed at build time and written into the manifest as
 * the one host permission besides the job sites (see src/config.ts for why it
 * is not a runtime setting). Bundles are left unminified: an extension's code is
 * something a person installing it should be able to read.
 */

import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve, isAbsolute } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const PACKAGE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = resolve(PACKAGE_DIR, "src");

export const DEFAULT_WEB_URL = "http://localhost:3000";

/** An http(s) origin and nothing else, or an error saying what is wrong. */
export function parseWebUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`EASYAPPLY_WEB_URL is not a URL: ${JSON.stringify(value)}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`EASYAPPLY_WEB_URL must be http or https, not ${url.protocol}`);
  }
  if ((url.pathname !== "/" && url.pathname !== "") || url.search || url.hash || url.username || url.password) {
    throw new Error(`EASYAPPLY_WEB_URL must be an origin only (like ${DEFAULT_WEB_URL}), not ${value}`);
  }
  return url.origin;
}

/** The manifest this build ships: the template plus the web app's host permission. */
export function buildManifest(webUrl) {
  const manifest = JSON.parse(readFileSync(resolve(SRC, "manifest.json"), "utf8"));
  const pkg = JSON.parse(readFileSync(resolve(PACKAGE_DIR, "package.json"), "utf8"));
  manifest.version = pkg.version;
  manifest.host_permissions = [...manifest.host_permissions, `${parseWebUrl(webUrl)}/*`];
  return manifest;
}

function resolveOutDir(out) {
  const outDir = isAbsolute(out) ? out : resolve(PACKAGE_DIR, out);
  const inside = relative(PACKAGE_DIR, outDir);
  // The directory is emptied first; refuse to empty anything that is not ours.
  if (!inside || inside.startsWith("..") || isAbsolute(inside) || inside === "src") {
    throw new Error(`Refusing to build into ${outDir}: it must be a directory inside ${PACKAGE_DIR}.`);
  }
  return outDir;
}

export async function buildExtension({ webUrl = DEFAULT_WEB_URL, outDir = "dist" } = {}) {
  const origin = parseWebUrl(webUrl);
  const target = resolveOutDir(outDir);

  rmSync(target, { recursive: true, force: true });
  mkdirSync(target, { recursive: true });

  await build({
    entryPoints: {
      background: resolve(SRC, "background/index.ts"),
      content: resolve(SRC, "content/index.ts"),
      popup: resolve(SRC, "popup/index.ts"),
    },
    outdir: target,
    bundle: true,
    // Content scripts cannot be modules, so all three are plain scripts.
    format: "iife",
    platform: "browser",
    target: "chrome120",
    sourcemap: true,
    legalComments: "none",
    logLevel: "warning",
    define: { __EASYAPPLY_WEB_URL__: JSON.stringify(origin) },
  });

  cpSync(resolve(SRC, "popup/popup.html"), resolve(target, "popup.html"));
  cpSync(resolve(SRC, "popup/popup.css"), resolve(target, "popup.css"));
  writeFileSync(resolve(target, "manifest.json"), `${JSON.stringify(buildManifest(origin), null, 2)}\n`);

  return { outDir: target, webUrl: origin };
}

function argument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const webUrl = process.env.EASYAPPLY_WEB_URL || DEFAULT_WEB_URL;
  const outDir = argument("--out") ?? "dist";
  try {
    const result = await buildExtension({ webUrl, outDir });
    console.log(`Built ${relative(process.cwd(), result.outDir) || "."} — saving to ${result.webUrl}`);
    if (!existsSync(resolve(result.outDir, "manifest.json"))) throw new Error("manifest.json was not written");
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  }
}
