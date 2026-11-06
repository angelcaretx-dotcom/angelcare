import type { MetadataRoute } from "next";
import { siteConfig } from "@/lib/site-config";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      // Private rider pages (ADR 0031) and the facility portal (ADR 0042).
      disallow: ["/account", "/facility"],
    },
    sitemap: `${siteConfig.url}/sitemap.xml`,
  };
}
