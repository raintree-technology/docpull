import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "DocPull",
    short_name: "DocPull",
    description:
      "Local-first context dependencies for AI agents, RAG pipelines, and MCP clients.",
    start_url: "/",
    display: "standalone",
    background_color: "#F5F4EF",
    theme_color: "#101213",
    icons: [
      {
        src: "/icon-192.png",
        sizes: "192x192",
        type: "image/png",
      },
      {
        src: "/icon-512.png",
        sizes: "512x512",
        type: "image/png",
      },
    ],
  };
}
