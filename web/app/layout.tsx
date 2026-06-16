import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";

const baseUrl = "https://docpull.raintree.technology";
const analyticsEnabled = process.env.VERCEL_ENV === "production";

export const metadata: Metadata = {
  metadataBase: new URL(baseUrl),
  title: "docpull - Public web to agent-ready Markdown.",
  description:
    "Python CLI, SDK, and MCP server that turns public static and server-rendered web pages into clean Markdown, NDJSON, and local context packs for AI agents and RAG.",
  applicationName: "docpull",
  authors: [{ name: "Raintree Technology", url: "https://raintree.technology" }],
  creator: "Raintree Technology",
  publisher: "Raintree Technology",
  alternates: {
    canonical: "/",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  icons: {
    icon: "/logo.svg",
  },
  keywords: [
    "web extraction",
    "source packs",
    "documentation",
    "markdown",
    "AI",
    "LLM",
    "RAG",
    "MCP",
    "AI agents",
    "web scraping",
    "web crawler",
    "python",
    "cli",
    "sdk",
    "docs",
    "crawler",
    "Parallel",
    "context packs",
  ],

  openGraph: {
    title: "docpull - Public web to agent-ready Markdown.",
    description:
      "Turn public static and server-rendered web pages into clean Markdown, NDJSON, and local context packs for AI agents, MCP clients, and RAG pipelines.",
    url: baseUrl,
    type: "website",
    siteName: "docpull",
    locale: "en_US",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "docpull - Public web to agent-ready Markdown.",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "docpull - Public web to agent-ready Markdown.",
    description:
      "Turn public static and server-rendered web pages into clean Markdown, NDJSON, and local context packs for AI agents, MCP clients, and RAG pipelines.",
    site: "@raintree_tech",
    creator: "@raintree_tech",
    images: ["/og-image.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="scroll-smooth" suppressHydrationWarning>
      <body className="antialiased">
        <ThemeProvider>{children}</ThemeProvider>
        {analyticsEnabled ? <Analytics /> : null}
      </body>
    </html>
  );
}
