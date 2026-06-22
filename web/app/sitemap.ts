import type { MetadataRoute } from "next";

const baseUrl = "https://docpull.raintree.technology";

// Spec: SEO / XML sitemaps. A single-page site lists one canonical URL.
// Fragment anchors (#features, #install, …) are not separate URLs and are omitted.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: baseUrl,
      lastModified: new Date(),
      changeFrequency: "monthly",
      priority: 1,
    },
    {
      url: `${baseUrl}/docs`,
      lastModified: new Date(),
      changeFrequency: "weekly",
      priority: 0.9,
    },
  ];
}
