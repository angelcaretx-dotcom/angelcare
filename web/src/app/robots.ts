import type { MetadataRoute } from "next";
import { siteConfig } from "@/lib/site-config";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      // Private rider pages (ADR 0031).
      disallow: "/account",
    },
    sitemap: `${siteConfig.url}/sitemap.xml`,
  };
}
