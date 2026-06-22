export type DocsNavItem = {
  label: string;
  href: string;
  active?: boolean;
};

export type DocsNavGroup = {
  title: string;
  items: readonly DocsNavItem[];
};

export type DocsTableRow = readonly [name: string, description: string];

export const overviewBadges = ["v5.0.0", "Python 3.10+"] as const;

export const docsNav = [
  {
    title: "Get started",
    items: [
      { label: "Overview", href: "#overview", active: true },
      { label: "Install", href: "#install" },
      { label: "Quickstart", href: "#quickstart" },
      { label: "Outputs", href: "#outputs" },
      { label: "Profiles", href: "#profiles" },
    ],
  },
  {
    title: "Guides",
    items: [
      { label: "Zero-budget runs", href: "#zero-budget" },
      { label: "Python SDK", href: "#python-sdk" },
      { label: "MCP server", href: "#mcp-server" },
      { label: "Agent skills", href: "#agent-skills" },
      { label: "Rendering fallback", href: "#rendering" },
      { label: "Provider workflows", href: "#providers" },
    ],
  },
  {
    title: "Reference",
    items: [
      { label: "Security defaults", href: "#security" },
      { label: "Troubleshooting", href: "#troubleshooting" },
      { label: "CLI recipes", href: "#recipes" },
      { label: "Changelog", href: "#resources" },
    ],
  },
] as const satisfies readonly DocsNavGroup[];

export const toc = [
  { label: "Overview", href: "#overview" },
  { label: "Install", href: "#install" },
  { label: "Quickstart", href: "#quickstart" },
  { label: "Outputs", href: "#outputs" },
  { label: "Profiles", href: "#profiles" },
  { label: "MCP server", href: "#mcp-server" },
  { label: "Rendering", href: "#rendering" },
  { label: "Troubleshooting", href: "#troubleshooting" },
] as const satisfies readonly DocsNavItem[];

export const outputRows = [
  ["Markdown", "Readable source snapshots with YAML frontmatter."],
  ["NDJSON", "Streamed or chunked records for agents and RAG."],
  ["SQLite", "Local retrieval with an FTS5 search index."],
  ["OKF", "Portable Open Knowledge Format bundles."],
  ["Archive / mirror", "Cached offline source snapshots."],
] as const satisfies readonly DocsTableRow[];

export const profileRows = [
  ["rag", "Default deduped Markdown plus metadata for retrieval."],
  ["llm", "NDJSON chunks shaped for LLM and agent pipelines."],
  ["okf", "Portable bundle with indexes, manifests, and hashes."],
  ["mirror", "Cached archive of the fetched source pages."],
  ["quick", "Small sampling crawl for fast inspection."],
  ["sec-filing", "Evidence chunks tuned for EDGAR-style filings."],
] as const satisfies readonly DocsTableRow[];
