import type { Metadata } from "next";
import { DocsArticle } from "@/components/docs/DocsArticle";
import { DocsShell } from "@/components/docs/DocsShell";

export const metadata: Metadata = {
  title: "Docs - docpull",
  description:
    "Install and use docpull to turn public web pages into local Markdown, NDJSON, context packs, MCP tools, and agent-ready source artifacts.",
  alternates: {
    canonical: "/docs",
  },
};

export default function DocsPage() {
  return (
    <DocsShell>
      <DocsArticle />
    </DocsShell>
  );
}
