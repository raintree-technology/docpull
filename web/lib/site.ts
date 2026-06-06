export const site = {
  name: "docpull",
  baseUrl: "https://docpull.raintree.technology",
  description:
    "Local web puller for turning server-rendered sites into clean Markdown. Fast, secure, and built for archives, search indexes, and agent workflows.",
  rssDescription:
    "Local web puller for turning server-rendered sites into clean Markdown.",
  publishedAt: "2026-06-05T00:00:00.000Z",
  securityExpiresAt: "2027-06-01T00:00:00.000Z",
} as const;

export const discoveryPaths = {
  home: "/",
  llms: "/llms.txt",
  llmsFull: "/llms-full.txt",
  sitemap: "/sitemap.xml",
  robots: "/robots.txt",
  rss: "/rss.xml",
  security: "/.well-known/security.txt",
  agentSkills: "/.well-known/agent-skills.json",
  docpullResearchSkill: "/agent-skills/docpull-research.md",
} as const;

export function absoluteUrl(path: string) {
  return new URL(path, site.baseUrl).toString();
}

export function utcDate(value: string) {
  return new Date(value).toUTCString();
}
