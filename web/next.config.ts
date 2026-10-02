import type { NextConfig } from "next";

// Fully static: the pipeline writes JSON, the build bakes it into HTML, and
// the result can sit on any static host. Set NEXT_PUBLIC_BASE_PATH when the
// site is served from a sub-path (a GitHub Pages project site, for example).
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  basePath: basePath || undefined,
};

export default nextConfig;
