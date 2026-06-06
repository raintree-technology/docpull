import type { MetadataRoute } from "next";

import { absoluteUrl, discoveryPaths, site } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date(site.publishedAt);

  return [
    {
      url: absoluteUrl(discoveryPaths.home),
      lastModified,
      changeFrequency: "monthly",
      priority: 1,
    },
    {
      url: absoluteUrl(discoveryPaths.llms),
      lastModified,
      changeFrequency: "monthly",
      priority: 0.8,
    },
    {
      url: absoluteUrl(discoveryPaths.llmsFull),
      lastModified,
      changeFrequency: "monthly",
      priority: 0.8,
    },
    {
      url: absoluteUrl(discoveryPaths.docpullResearchSkill),
      lastModified,
      changeFrequency: "monthly",
      priority: 0.7,
    },
  ];
}
