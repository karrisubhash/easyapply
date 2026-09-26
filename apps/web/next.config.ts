import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // @easyapply/shared is published as TypeScript source, not build output.
  transpilePackages: ["@easyapply/shared"],

  /**
   * Where the build goes.
   *
   * Overridable because `next dev` and `next start` cannot share one directory:
   * the dev server rewrites `.next` continuously, so a production build served
   * out of it answers every route with a webpack runtime error. Unset — which
   * is every ordinary invocation — this is Next's own default, so nothing about
   * `npm run dev` or `npm run build` changes. The end-to-end suite sets it so
   * that it can build and serve what ships while a development server is
   * running beside it.
   */
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
};

export default nextConfig;
