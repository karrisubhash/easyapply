import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@easyapply/shared": resolve(__dirname, "../../packages/shared/src/index.ts"),
    },
  },
  // The build injects the web app's address; tests pass addresses explicitly
  // wherever it matters, so this only has to be something plausible.
  define: {
    __EASYAPPLY_WEB_URL__: JSON.stringify("http://localhost:3000"),
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["test/**/*.test.ts"],
    // The flow suites (flow, workflow, application) load the whole extension
    // through its entry points. Measured 2026-09-26: the first such test in a
    // file takes ~0.6 s on an idle machine but 5.1-5.9 s with a Next build or a
    // second suite running beside it, which Vitest's 5 s default turned into
    // intermittent timeouts. 15 s is headroom for a busy machine, not a wait
    // any test relies on: a hang still fails.
    testTimeout: 15_000,
  },
});
