import { buildExtension } from "../scripts/build.mjs";
import { BUILD_DIR, WEB_URL } from "./env";

/** Build the extension the suite loads, pointed at the web app it saves to. */
export default async function globalSetup(): Promise<void> {
  const { webUrl } = await buildExtension({ webUrl: WEB_URL, outDir: BUILD_DIR });
  console.log(`Extension built for ${webUrl}`);
}
