import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { FlatCompat } from "@eslint/eslintrc";

/**
 * ESLint, flat-config form.
 *
 * `next/core-web-vitals` and `next/typescript` are still shipped as eslintrc
 * presets, so FlatCompat is how they are consumed until Next ships flat
 * versions of them. That is the only reason this file is not a plain array.
 *
 * Deliberately close to the Next.js default. This project's real correctness
 * guarantees are the generated OpenAPI contract, `tsc --noEmit`, and the test
 * suites; a lint config that grew its own opinions would add a fourth source of
 * truth about what "correct" means without adding a fourth guarantee.
 */
const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

export default [
  {
    // `.next-e2e` is the end-to-end suite's own build, and `.next-ext-e2e` the
    // Chrome extension's real-stack run's (apps/extension/e2e/real-stack.mjs).
    // Same generated output as `.next`, somewhere else so it can be served
    // while a dev server rewrites `.next` beside it — and just as much not source.
    ignores: [".next/**", ".next-e2e/**", ".next-ext-e2e/**", "node_modules/**", "next-env.d.ts"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // The codebase uses `<a>` for two things Link cannot do: an external
      // posting URL on a third-party site, and a download the browser must
      // handle itself. Both are correct uses of an anchor, and the rule has no
      // way to tell them from an internal navigation that should be a Link.
      // Left as a warning rather than off, so a genuine mistake still shows.
      "@next/next/no-html-link-for-pages": "warn",
    },
  },
  {
    // A CommonJS file has no `import`. `e2e/support/env.cjs` is deliberately
    // CJS — its own docstring explains why: bare Node ESM, Playwright's
    // TypeScript config loader and the compiled specs all read it, and CJS is
    // the one format all three can load. Asking it to use `import` is asking it
    // to stop working.
    files: ["**/*.cjs"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  {
    // `e2e/` is Playwright, and there is no React in it. `use` there is the
    // fixture argument Playwright passes to `test.extend`, not a React hook —
    // the rule matches it on the name alone and has no way to tell. Scoped to
    // this directory so the rule keeps doing its job everywhere React actually
    // runs.
    files: ["e2e/**"],
    rules: {
      "react-hooks/rules-of-hooks": "off",
    },
  },
  {
    // Tests reach for shapes the app never produces — a malformed API body, a
    // half-built fixture — and expressing those honestly sometimes needs `any`.
    // Narrowing them into real types would be inventing a contract the test
    // exists to prove does not hold.
    files: ["**/*.test.ts", "**/*.test.tsx", "src/test/**"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-require-imports": "off",
    },
  },
];
