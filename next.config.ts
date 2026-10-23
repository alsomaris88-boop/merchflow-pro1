import type { NextConfig } from "next";

/**
 * Build profiles:
 *
 *  • Default (cloud / dev):  `next dev` / `next build` + `next start`.
 *    Full SSR + Next API routes at /api/* (they proxy to the Python worker).
 *
 *  • Desktop export (Windows runtime):  `npm run build:desktop`
 *    produces a pure static bundle in `out/` (HTML/CSS/JS only). The Python
 *    FastAPI worker mounts `out/` on `/` and serves the same /api surface from
 *    SQLite — one unified port (127.0.0.1:8000), zero Node at runtime.
 */
const isDesktopExport = process.env.DESKTOP_EXPORT === "1";

const nextConfig: NextConfig = {
  ...(isDesktopExport
    ? {
        output: "export",
        trailingSlash: true,
        images: { unoptimized: true },
        distDir: ".next-export",
        typescript: { tsconfigPath: "tsconfig.desktop.json" },
      }
    : {}),
};

export default nextConfig;
