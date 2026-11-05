import type { NextConfig } from "next";

// The Django API, also reachable at this site's own /api/ (ADR 0031): the
// rider pages call it same-origin, so their sign-in cookie belongs to this
// site and is never sent cross-site.
const apiBase = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        // Django's URLs end in "/"; Next.js strips trailing slashes, so
        // add it back on the way through.
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*/`,
      },
    ];
  },
};

export default nextConfig;
