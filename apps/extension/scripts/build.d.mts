export const DEFAULT_WEB_URL: string;
export function parseWebUrl(value: string): string;
export function buildManifest(webUrl: string): {
  manifest_version: number;
  name: string;
  version: string;
  key: string;
  permissions: string[];
  host_permissions: string[];
  content_scripts: { matches: string[]; js: string[]; run_at?: string }[];
  [key: string]: unknown;
};
export function buildExtension(options?: { webUrl?: string; outDir?: string }): Promise<{
  outDir: string;
  webUrl: string;
}>;
