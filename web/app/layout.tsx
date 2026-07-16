import type { Metadata } from "next";
import "./globals.css";

const baseUrl = "https://docpull.raintree.technology";

export const metadata: Metadata = {
  metadataBase: new URL(baseUrl),
  title: {
    default: "DocPull",
    template: "%s | DocPull",
  },
  description:
    "DocPull is a local-first Python CLI, SDK, and MCP server for turning public web sources into cited context packs.",
  applicationName: "DocPull",
  manifest: "/manifest.webmanifest",
  authors: [{ name: "Raintree Technology", url: "https://raintree.technology" }],
  creator: "Raintree Technology",
  publisher: "Raintree Technology",
  robots: {
    index: true,
    follow: true,
  },
  openGraph: {
    type: "website",
    url: baseUrl,
    siteName: "DocPull",
    title: "DocPull",
    description:
      "Local-first context dependencies for AI agents, RAG pipelines, and MCP clients.",
  },
  twitter: {
    card: "summary",
    title: "DocPull",
    description:
      "Local-first context dependencies for AI agents, RAG pipelines, and MCP clients.",
  },
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/favicon.ico", sizes: "32x32" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
