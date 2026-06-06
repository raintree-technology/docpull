import { absoluteUrl, discoveryPaths } from "@/lib/site";

export const dynamic = "force-static";

const payload = {
  version: "https://agent-skills.dev/v1",
  skills: [
    {
      name: "docpull-research",
      title: "docpull research",
      description:
        "Ground answers about libraries, frameworks, SDKs, and docs URLs in real fetched documentation via docpull MCP tools.",
      url: absoluteUrl(discoveryPaths.docpullResearchSkill),
      inputModes: ["text"],
      tags: [
        "documentation",
        "research",
        "mcp",
        "libraries",
        "frameworks",
        "grounding",
      ],
    },
  ],
};

export function GET() {
  return Response.json(payload, {
    headers: {
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
