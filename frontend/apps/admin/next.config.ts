import path from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";

const apiUrl = process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  transpilePackages: ["@saas/ui", "@saas/api-client"],
  // The Docker image runs the self-contained server (NEXT_OUTPUT=standalone is set in its Dockerfile).
  ...(process.env.NEXT_OUTPUT === "standalone" && {
    output: "standalone" as const,
    outputFileTracingRoot: path.join(path.dirname(fileURLToPath(import.meta.url)), "../.."),
  }),
  // Same-origin /api calls are proxied to Django so session cookies and CSRF work without CORS.
  // On AWS the load balancer sends /api/* straight to the API, so this proxy is only used locally.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
