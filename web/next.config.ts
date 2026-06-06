import type { NextConfig } from "next";

import { absoluteUrl, discoveryPaths } from "./lib/site";

const nextConfig: NextConfig = {
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
  async headers() {
    const discoveryLinks = [
      `<${absoluteUrl(discoveryPaths.llms)}>; rel="alternate"; type="text/markdown"`,
      `<${absoluteUrl(discoveryPaths.llmsFull)}>; rel="alternate"; type="text/markdown"`,
      `<${absoluteUrl(discoveryPaths.agentSkills)}>; rel="alternate"; type="application/json"`,
      `<${absoluteUrl(discoveryPaths.sitemap)}>; rel="sitemap"; type="application/xml"`,
      `<${absoluteUrl(discoveryPaths.rss)}>; rel="alternate"; type="application/rss+xml"`,
      `<${absoluteUrl(discoveryPaths.security)}>; rel="security"; type="text/plain"`,
    ].join(", ");

    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Link",
            value: discoveryLinks,
          },
        ],
      },
    ];
  },
};

export default nextConfig;
