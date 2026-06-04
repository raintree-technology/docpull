import type { MetadataRoute } from "next";

const baseUrl = "https://docpull.raintree.technology";

// Spec: SEO / robots.txt (RFC 9309). Allow all crawlers and point them at the sitemap.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
    },
    sitemap: `${baseUrl}/sitemap.xml`,
    host: baseUrl,
  };
}
